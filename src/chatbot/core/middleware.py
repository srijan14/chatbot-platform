"""LangChain v1 middlewares for the chatbot platform.

Three middlewares plug into `langchain.agents.create_agent` to give us
chatbot-platform behaviour the agent itself doesn't ship with:

1. ``build_dynamic_prompt(bot_config, skills)``
   Returns a ``@dynamic_prompt``-decorated middleware. The system prompt is
   re-built every model call from (a) the bot persona, (b) each skill's
   ``system_prompt_addition()``, and (c) the live ``customer_id`` from
   graph state — replaces the deprecated `Callable[[State], list[Message]]`
   prompt parameter from ``create_react_agent``.

2. ``TokenUsageMiddleware``
   ``after_model`` hook. Reads the just-appended ``AIMessage``'s
   ``usage_metadata`` and accumulates prompt / completion / cached token
   counts into typed state fields. The orchestrator reads totals off
   final state at turn end instead of scanning messages — single source
   of truth, debuggable in the checkpoint.

3. ``BudgetGuardMiddleware``
   ``before_model`` hook. Checks an in-memory ``dict[customer_id, int]``
   of tokens-used. When over the per-tenant daily cap, short-circuits
   the agent by appending an apologetic ``AIMessage`` and setting
   ``jump_to="__end__"`` — never calls the model. Demo-grade store
   (in-process dict, resets on restart); a production deployment would
   back this with Redis or Postgres.
"""
from __future__ import annotations

from typing import Any, Iterable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    dynamic_prompt,
)
from langchain_core.messages import AIMessage, SystemMessage

from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.observability.logger import get_logger
from src.chatbot.skills.base import Skill

_log = get_logger("orch")


# --- 1. Dynamic prompt middleware -------------------------------------------

def build_dynamic_prompt(bot_config: BotConfig, skills: Iterable[Skill]):
    """Return a ``@dynamic_prompt`` middleware closed over this bot's persona
    + each skill's calling-convention rule, with live ``customer_id`` from state.
    """
    static_parts: list[str] = [bot_config.system_prompt.strip()]
    for skill in skills:
        addition = skill.system_prompt_addition()
        if addition:
            static_parts.append(addition.strip())
    static_prompt = "\n\n".join(p for p in static_parts if p)

    @dynamic_prompt
    def _build(request: ModelRequest) -> str:
        customer_id = request.state.get("customer_id")
        if customer_id:
            return (
                f"{static_prompt}\n\n"
                f"Authenticated customer for this session: {customer_id}. "
                "Use this customer_id for any tool that needs it; do NOT "
                "ask the user for it."
            )
        return static_prompt

    return _build


# --- 2. Token-usage middleware ----------------------------------------------

class TokenUsageMiddleware(AgentMiddleware):
    """Accumulate token usage into typed state fields after each model call.

    LangChain populates `AIMessage.usage_metadata` with
    {input_tokens, output_tokens, input_token_details: {cache_read: ...}}
    when the underlying provider returns usage info. We sum these per
    turn so the chat handler can read costs directly off graph state.
    """

    def after_model(self, state, runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None
        usage = getattr(last, "usage_metadata", None) or {}
        prompt = int(usage.get("input_tokens", 0) or 0)
        completion = int(usage.get("output_tokens", 0) or 0)
        cached = int((usage.get("input_token_details") or {}).get("cache_read", 0) or 0)
        if not (prompt or completion or cached):
            return None
        return {
            "prompt_tokens_used": int(state.get("prompt_tokens_used", 0) or 0) + prompt,
            "completion_tokens_used": int(state.get("completion_tokens_used", 0) or 0) + completion,
            "cached_tokens_used": int(state.get("cached_tokens_used", 0) or 0) + cached,
        }


# --- 3. Per-tenant budget guard ---------------------------------------------

class BudgetGuardMiddleware(AgentMiddleware):
    """Short-circuit the agent when a tenant exceeds its daily token cap.

    Pre-model hook reads the running tally from an in-memory store keyed by
    ``customer_id`` and adds the prior turn's tokens (read off state) to the
    tally. If the new tally is over ``daily_cap``, append a polite AI message
    explaining the cap and jump to end — the model is never called.

    The in-process dict is intentionally simple (POC/demo). A real deployment
    would persist this in Redis with a midnight-rollover key so the cap
    resets at the right wall-clock boundary.
    """

    def __init__(self, *, daily_cap: int, store: dict[str, int] | None = None):
        super().__init__()
        self._daily_cap = int(daily_cap)
        # Caller can pass a shared dict to inspect/reset between requests.
        self._store: dict[str, int] = store if store is not None else {}

    @property
    def store(self) -> dict[str, int]:
        return self._store

    def before_model(self, state, runtime) -> dict[str, Any] | None:
        customer_id = state.get("customer_id")
        if not customer_id:
            # No tenant identity → no budget tracking (anonymous demo path).
            return None

        # Roll prior-turn usage from state into the cumulative store. We do
        # this on `before_model` (not `after_model`) because after_model
        # writes state updates AFTER our turn ends — by next turn the totals
        # are already in state.
        prior = int(state.get("prompt_tokens_used", 0) or 0) + int(
            state.get("completion_tokens_used", 0) or 0
        )
        # Only count the increment between what state shows and what the
        # store has — avoids double-counting on graph resume.
        seen = self._store.get(customer_id, 0)
        if prior > seen:
            self._store[customer_id] = prior

        if self._store.get(customer_id, 0) >= self._daily_cap:
            _log.info(
                "[orch] BUDGET-GUARD-REJECT customer=%s used=%d cap=%d",
                customer_id, self._store[customer_id], self._daily_cap,
            )
            msg = AIMessage(
                content=(
                    f"You've reached today's usage cap "
                    f"({self._daily_cap:,} tokens). Please try again tomorrow."
                )
            )
            # Returning state with jump_to="__end__" terminates the agent
            # loop without ever calling the model.
            return {"messages": [msg], "jump_to": "__end__"}
        return None
