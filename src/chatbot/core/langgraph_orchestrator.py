"""LangGraph-based orchestrator (replacement for LLMOrchestrator).

Owns:
  • A `create_react_agent` graph per bot (cached at first build).
  • A LangGraph `AsyncSqliteSaver` checkpointer that persists per-session
    conversation state (the `messages` list and our `customer_id` field).
  • The `run_turn(session, message, bot_config, skills) → TurnResult`
    interface the chat handler already calls.

Why this replaces the old loop:
  • Tool-call iteration, state persistence, and clarification pause/resume
    are all owned by LangGraph's prebuilt agent + interrupt primitive. The
    old `_detect_pending_clarification` history-scan trick is gone; we now
    detect a paused graph via `Command(resume=…)` on the next turn.
  • Skills are unchanged. The adapter in `src/chatbot/adapters/skill_to_tool.py`
    bridges the `Skill` ABC to LangChain `StructuredTool`s.
  • System-prompt assembly (persona + per-skill additions + auth context for
    the signed-in customer) lives in the per-bot `_make_prompt` callable
    passed into `create_react_agent` — so the prompt is rebuilt every turn
    with the live customer_id rather than baked into a static system msg.
"""
from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
from langchain_openai import AzureChatOpenAI

from src.chatbot.adapters.skill_to_tool import skill_to_langchain_tools
from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.core.conversation_manager import Session
from src.chatbot.core.llm_orchestrator import (
    ClarificationData,
    ToolCallTrace,
    TurnResult,
)
from src.chatbot.core.state import AgentState
from src.chatbot.observability.logger import get_logger, new_trace_id, truncate
from src.chatbot.skills.base import Skill, TurnSignal

_log = get_logger("orch")


class LangGraphOrchestrator:
    def __init__(
        self,
        *,
        azure_endpoint: str,
        azure_api_key: str,
        azure_api_version: str,
        checkpointer: AsyncSqliteSaver,
    ):
        self._azure_endpoint = azure_endpoint
        self._azure_api_key = azure_api_key
        self._azure_api_version = azure_api_version
        self._checkpointer = checkpointer
        # bot_id → compiled graph. Built lazily on first turn for that bot.
        self._graphs: dict[str, Any] = {}

    def _build_llm(self, bot_config: BotConfig) -> AzureChatOpenAI:
        """Build an Azure chat model bound to this bot's deployment & params.

        Each bot's YAML specifies its own deployment (e.g. gpt-4o vs gpt-4o-mini)
        and temperature, so we build one model per bot rather than one global.
        """
        return AzureChatOpenAI(
            azure_endpoint=self._azure_endpoint,
            api_key=self._azure_api_key,
            api_version=self._azure_api_version,
            azure_deployment=bot_config.llm_deployment,
            temperature=bot_config.temperature,
            max_tokens=bot_config.max_tokens,
        )

    async def get_or_build_graph(
        self,
        bot_config: BotConfig,
        skills: list[Skill],
    ) -> Any:
        if bot_config.bot_id in self._graphs:
            return self._graphs[bot_config.bot_id]

        # Each skill contributes one or more LangChain tools. Concatenate.
        all_tools = []
        for skill in skills:
            tools = await skill_to_langchain_tools(skill)
            all_tools.extend(tools)

        # Static system-prompt fragments — bot persona + each skill's calling
        # convention. These never depend on the live turn so we capture them
        # in the closure rather than rebuilding per turn.
        skill_additions: list[str] = []
        for skill in skills:
            addition = skill.system_prompt_addition()
            if addition:
                skill_additions.append(addition)
        static_prompt_parts = [bot_config.system_prompt] + skill_additions
        static_prompt = "\n\n".join(p.strip() for p in static_prompt_parts if p)

        def _make_prompt(state: AgentState) -> list[BaseMessage]:
            """Build the message list for the LLM each invocation.

            Prepended SystemMessage assembles persona + skill rules + auth
            context (if a customer is signed in for this session). This runs
            every LLM call inside the agent, so live state changes (customer
            log-in mid-conversation) take effect immediately.
            """
            parts = [static_prompt]
            customer_id = state.get("customer_id")
            if customer_id:
                parts.append(
                    f"Authenticated customer for this session: {customer_id}. "
                    "Use this customer_id for any tool that needs it; do NOT "
                    "ask the user for it."
                )
            system = SystemMessage(content="\n\n".join(p for p in parts if p))
            return [system] + state["messages"]

        llm = self._build_llm(bot_config)
        graph = create_react_agent(
            model=llm,
            tools=all_tools,
            prompt=_make_prompt,
            state_schema=AgentState,
            checkpointer=self._checkpointer,
        )
        self._graphs[bot_config.bot_id] = graph
        _log.info(
            "[orch] GRAPH-BUILT bot=%s tools=%s",
            bot_config.bot_id,
            [t.name for t in all_tools],
        )
        return graph

    async def run_turn(
        self,
        session: Session,
        user_message: str,
        bot_config: BotConfig,
        skills: list[Skill],
    ) -> TurnResult:
        trace_id = new_trace_id()
        t_start = time.monotonic()
        _log.info(
            "[orch] TURN-START trace=%s bot=%s session=%s customer=%s message=%r resume=%s",
            trace_id,
            bot_config.bot_id,
            session.session_id,
            session.customer_id or "<none>",
            truncate(user_message, 160),
            session.awaiting_clarification,
        )

        graph = await self.get_or_build_graph(bot_config, skills)
        config = {
            "configurable": {"thread_id": session.session_id},
            # max_tool_iterations is per-turn; recursion_limit counts every
            # node visit (prepare, agent, tools, agent, tools, ...). 4 visits
            # per iteration is a safe upper bound.
            "recursion_limit": max(bot_config.max_tool_iterations * 4, 25),
        }

        # Snapshot the pre-turn state so we can compute "messages added this turn".
        pre_state = await graph.aget_state(config)
        pre_message_count = len(pre_state.values.get("messages", [])) if pre_state.values else 0

        if session.awaiting_clarification:
            # Resume an interrupted graph with the user's reply.
            _log.info("[orch] RESUME-FROM-INTERRUPT trace=%s", trace_id)
            graph_input: Any = Command(resume=user_message)
        else:
            graph_input = {
                "messages": [HumanMessage(content=user_message)],
                "bot_id": bot_config.bot_id,
                "customer_id": session.customer_id,
            }

        capped = False
        try:
            await graph.ainvoke(graph_input, config=config)
        except Exception as exc:  # pragma: no cover - surfaced as bot error
            # GraphRecursionError or LLM failures end up here. Surface as a
            # plain TurnResult with is_error semantics in the response text.
            _log.exception("[orch] GRAPH-FAILED trace=%s", trace_id)
            latency_ms = int((time.monotonic() - t_start) * 1000)
            return TurnResult(
                trace_id=trace_id,
                text=f"Sorry, I hit an internal error: {type(exc).__name__}.",
                iterations=0,
                latency_ms=latency_ms,
                capped=False,
                signals=[],
                new_messages=[],
                log_payload=self._build_log_payload(
                    trace_id, session, bot_config, latency_ms, 0, 0, 0, 0,
                    False, False, [],
                ),
            )

        post_state = await graph.aget_state(config)
        new_messages_lc: list[BaseMessage] = post_state.values["messages"][pre_message_count:]
        latency_ms = int((time.monotonic() - t_start) * 1000)

        # Inspect interrupt state.
        interrupt_payload = _extract_interrupt(post_state)
        awaiting_clarification = interrupt_payload is not None and interrupt_payload.get("type") == "clarification"

        signals: list[TurnSignal] = []
        clarification = None
        if awaiting_clarification:
            payload = {k: v for k, v in interrupt_payload.items() if k != "type"}
            signals.append(TurnSignal(type="clarification", payload=payload))
            clarification = ClarificationData(
                question=payload.get("question", ""),
                expected=payload.get("expected", "free_text"),
                suggested_replies=list(payload.get("suggested_replies") or []),
            )

        # Build response text. If interrupted on clarification, the user-visible
        # text IS the clarification question — the agent didn't get to write a
        # final reply yet. Otherwise, the last AIMessage with non-empty content.
        if awaiting_clarification:
            text = clarification.question if clarification else ""
        else:
            text = _last_assistant_text(new_messages_lc)

        # Tool-call trace + token aggregation across this turn's messages.
        tool_calls_trace: list[ToolCallTrace] = []
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        iterations = 0
        for m in new_messages_lc:
            if isinstance(m, AIMessage):
                iterations += 1
                usage = m.usage_metadata or {}
                prompt_tokens += usage.get("input_tokens", 0) or 0
                completion_tokens += usage.get("output_tokens", 0) or 0
                cached_tokens += (usage.get("input_token_details", {}) or {}).get("cache_read", 0) or 0
                for tc in m.tool_calls or []:
                    tool_calls_trace.append(
                        ToolCallTrace(
                            name=tc.get("name", "<unknown>"),
                            input=tc.get("args", {}) or {},
                            duration_ms=0,  # individual tool latency not tracked at this layer
                            ok=True,  # ToolNode rewrites to error ToolMessage on failure
                            output_chars=0,
                        )
                    )
            elif isinstance(m, ToolMessage):
                # Mark prior trace ok=False if this tool message reports an error.
                if getattr(m, "status", None) == "error" and tool_calls_trace:
                    # Find the most recent trace with the same tool name.
                    name = m.name or ""
                    for trace in reversed(tool_calls_trace):
                        if trace.name == name:
                            trace.ok = False
                            break
                if tool_calls_trace:
                    content = m.content if isinstance(m.content, str) else str(m.content)
                    # Update output_chars on the matching trace (last match wins).
                    name = m.name or ""
                    for trace in reversed(tool_calls_trace):
                        if trace.name == name:
                            trace.output_chars = len(content)
                            break

        _log.info(
            "[orch] TURN-END trace=%s iterations=%d tool_calls=%d awaiting_clar=%s text_chars=%d latency_ms=%d",
            trace_id, iterations, len(tool_calls_trace), awaiting_clarification,
            len(text or ""), latency_ms,
        )

        log_payload = self._build_log_payload(
            trace_id, session, bot_config, latency_ms,
            iterations, prompt_tokens, completion_tokens, cached_tokens,
            awaiting_clarification, capped, tool_calls_trace,
        )

        return TurnResult(
            trace_id=trace_id,
            text=text or "",
            iterations=iterations,
            tool_calls=tool_calls_trace,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            latency_ms=latency_ms,
            capped=capped,
            signals=signals,
            awaiting_clarification=awaiting_clarification,
            clarification=clarification,
            # LangGraph owns conversation state now; nothing for the chat
            # handler to persist into MessageRow. Kept empty for API parity.
            new_messages=[],
            log_payload=log_payload,
        )

    async def get_state_messages(
        self,
        session_id: str,
        bot_config: BotConfig,
        skills: list[Skill],
    ) -> list[BaseMessage]:
        """Return the persisted message history for a session.

        Used by the /chat/history endpoint to hydrate the UI on page load.
        Reads from the LangGraph checkpointer via the bot's compiled graph.
        Returns an empty list if the session has no checkpoint yet.
        """
        graph = await self.get_or_build_graph(bot_config, skills)
        config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(config)
        if not state.values:
            return []
        return list(state.values.get("messages", []) or [])

    @staticmethod
    def _build_log_payload(
        trace_id: str,
        session: Session,
        bot_config: BotConfig,
        latency_ms: int,
        iterations: int,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int,
        awaiting_clarification: bool,
        capped: bool,
        tool_calls_trace: list[ToolCallTrace],
    ) -> dict[str, Any]:
        return {
            "trace_id": trace_id,
            "session_id": session.session_id,
            "bot_id": bot_config.bot_id,
            "customer_id": session.customer_id,
            "iterations": iterations,
            "capped": capped,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "latency_ms": latency_ms,
            "response_chars": 0,  # filled by chat handler from final text
            "awaiting_clarification": awaiting_clarification,
            "tool_calls": [
                {
                    "name": t.name,
                    "input": t.input,
                    "duration_ms": t.duration_ms,
                    "ok": t.ok,
                    "output_chars": t.output_chars,
                }
                for t in tool_calls_trace
            ],
        }


def _extract_interrupt(state: Any) -> dict | None:
    """Pull the interrupt payload from a graph state snapshot.

    LangGraph 1.x: `state.tasks` is a tuple of `PregelTask` objects; any
    interrupted task carries a tuple of `Interrupt` objects on `.interrupts`,
    each with a `.value` (what we passed to `interrupt(...)`).
    """
    tasks = getattr(state, "tasks", None) or ()
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or ()
        for itr in interrupts:
            value = getattr(itr, "value", None)
            if isinstance(value, dict):
                return value
    return None


def _last_assistant_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            content = m.content
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                # Multi-part content (rare with the chat API). Concat text parts.
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                joined = "".join(parts)
                if joined:
                    return joined
    return ""
