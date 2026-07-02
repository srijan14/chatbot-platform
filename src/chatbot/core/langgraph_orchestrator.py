"""LangChain v1 agent orchestrator.

Owns:
  • A `langchain.agents.create_agent` graph per bot (cached at first build),
    with three middlewares: dynamic system prompt, token-usage accumulator,
    per-tenant budget guard.
  • A LangGraph checkpointer (SQLite file or Postgres, chosen from env) that
    persists per-session conversation state (the agent's `messages` list and
    our custom fields).
  • The `run_turn(session, message, bot_config, skills) → TurnResult`
    interface the chat handler already calls.

Why this shape:
  • `create_react_agent` from `langgraph.prebuilt` is deprecated in
    LangGraph v1 (removed in v2). `langchain.agents.create_agent` is the
    canonical replacement, and the dynamic-prompt-via-callable pattern
    was removed in favour of `@dynamic_prompt` middleware.
  • Skills stay LangGraph-agnostic. The adapter in
    `src/chatbot/adapters/skill_to_tool.py` bridges the `Skill` ABC to
    LangChain `StructuredTool`s; clarification's terminal `ToolResult`
    becomes a `langgraph.types.interrupt(...)` call inside the adapter.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_openai import AzureChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from src.chatbot.adapters.skill_to_tool import skill_to_langchain_tools
from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.core.conversation_manager import Session
from src.chatbot.core.middleware import (
    BudgetGuardMiddleware,
    TokenUsageMiddleware,
    build_dynamic_prompt,
)
from src.chatbot.core.state import ChatbotAgentState
from src.chatbot.core.turn_context import capture_sources
from src.chatbot.core.turn_result import (
    ClarificationData,
    ToolCallTrace,
    TurnResult,
)
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
        checkpointer: BaseCheckpointSaver,
        budget_daily_cap: int = 1_000_000,
    ):
        self._azure_endpoint = azure_endpoint
        self._azure_api_key = azure_api_key
        self._azure_api_version = azure_api_version
        self._checkpointer = checkpointer
        # In-process per-customer token tally. Shared across all bots so a
        # tenant's budget covers everything they do on this platform.
        # (Demo-grade — Redis would back this in production.)
        self._budget_store: dict[str, int] = {}
        self._budget_daily_cap = budget_daily_cap
        # bot_id → compiled graph. Built lazily on first turn for that bot.
        self._graphs: dict[str, Any] = {}
        # session_id → lock. Serializes turns on the same conversation thread.
        # Concurrent ainvoke() on one thread_id races the checkpointer and can
        # leave an orphaned tool_call (assistant tool_calls with no tool reply),
        # which then 400s every later turn. One lock per session prevents that.
        self._session_locks: dict[str, asyncio.Lock] = {}

    @property
    def budget_store(self) -> dict[str, int]:
        return self._budget_store

    def _build_llm(self, bot_config: BotConfig) -> AzureChatOpenAI:
        """Build an Azure chat model bound to this bot's deployment & params.

        Only passes auth kwargs that have non-empty values, so AzureChatOpenAI's
        env-var auto-read (AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY /
        OPENAI_API_VERSION) still works when callers didn't supply explicit
        creds. Passing an empty string would otherwise override the env
        default with garbage.

        Reasoning models (o1/o3/o4-mini, gpt-5, etc.) reject custom
        `temperature` — only the server default (1.0) is allowed. When
        `llm_reasoning` is set on the bot config (auto-detected from the
        deployment name regex, or explicit via YAML `llm.reasoning: true`),
        we omit temperature entirely so Azure uses its default.
        """
        kwargs: dict[str, Any] = {
            "azure_deployment": bot_config.llm_deployment,
            "max_tokens": bot_config.max_tokens,
        }
        if self._azure_endpoint:
            kwargs["azure_endpoint"] = self._azure_endpoint
        if self._azure_api_key:
            kwargs["api_key"] = self._azure_api_key
        if self._azure_api_version:
            kwargs["api_version"] = self._azure_api_version
        # Only set temperature for non-reasoning models; o-series + gpt-5
        # require the default (1.0) and reject anything else.
        if not bot_config.llm_reasoning:
            kwargs["temperature"] = bot_config.temperature

        return AzureChatOpenAI(**kwargs)

    async def get_or_build_graph(
        self,
        bot_config: BotConfig,
        skills: list[Skill],
    ) -> Any:
        if bot_config.bot_id in self._graphs:
            return self._graphs[bot_config.bot_id]

        # Each skill contributes one or more LangChain tools.
        all_tools = []
        for skill in skills:
            tools = await skill_to_langchain_tools(skill)
            all_tools.extend(tools)

        middlewares = [
            build_dynamic_prompt(bot_config, skills),
            TokenUsageMiddleware(),
            BudgetGuardMiddleware(
                daily_cap=self._budget_daily_cap,
                store=self._budget_store,
            ),
        ]

        llm = self._build_llm(bot_config)
        graph = create_agent(
            model=llm,
            tools=all_tools,
            middleware=middlewares,
            state_schema=ChatbotAgentState,
            checkpointer=self._checkpointer,
        )
        self._graphs[bot_config.bot_id] = graph
        _log.info(
            "[orch] GRAPH-BUILT bot=%s tools=%s middlewares=%s",
            bot_config.bot_id,
            [t.name for t in all_tools],
            [type(m).__name__ for m in middlewares],
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
            "recursion_limit": max(bot_config.max_tool_iterations * 4, 25),
        }

        capped = False
        # Serialize turns on this conversation thread. Concurrent ainvoke() on
        # one thread_id races the checkpointer and is a prime way to orphan a
        # tool_call; the lock makes turns sequential per session.
        lock = self._session_locks.setdefault(session.session_id, asyncio.Lock())
        async with lock:
            # Snapshot pre-turn state so we can slice the messages added this turn.
            pre_state = await graph.aget_state(config)
            pre_messages: list[BaseMessage] = (
                list(pre_state.values.get("messages", []) or []) if pre_state.values else []
            )
            pre_message_count = len(pre_messages)
            pre_prompt_tokens = int(pre_state.values.get("prompt_tokens_used", 0) or 0) if pre_state.values else 0
            pre_completion_tokens = int(pre_state.values.get("completion_tokens_used", 0) or 0) if pre_state.values else 0
            pre_cached_tokens = int(pre_state.values.get("cached_tokens_used", 0) or 0) if pre_state.values else 0

            if session.awaiting_clarification:
                _log.info("[orch] RESUME-FROM-INTERRUPT trace=%s", trace_id)
                graph_input: Any = Command(resume=user_message)
            else:
                # Heal a poisoned thread: if a prior turn left an assistant
                # message with tool_calls but no matching tool replies (crash,
                # an unresumed interrupt, or a concurrent turn), replaying it
                # 400s on every later turn. Close the open tool_calls with
                # synthetic results before appending the new user message.
                repair = _dangling_tool_messages(pre_messages)
                if repair:
                    await graph.aupdate_state(config, {"messages": repair})
                    pre_message_count += len(repair)
                    _log.warning(
                        "[orch] THREAD-REPAIRED trace=%s closed=%d open tool_call(s)",
                        trace_id, len(repair),
                    )
                graph_input = {
                    "messages": [HumanMessage(content=user_message)],
                    "bot_id": bot_config.bot_id,
                    "customer_id": session.customer_id,
                }

            try:
                # Collect source references skills emit while the graph runs
                # (RAG citations) so they can surface on the chat response.
                with capture_sources() as collected_sources:
                    result = await graph.ainvoke(graph_input, config=config)
            except Exception as exc:
                # Surface the exception type AND message in the response so the
                # bot can see what actually broke without having to dig through
                # the structured log. Full traceback still lives in the log via
                # _log.exception().
                _log.exception("[orch] GRAPH-FAILED trace=%s", trace_id)
                latency_ms = int((time.monotonic() - t_start) * 1000)
                if _is_orphaned_tool_calls_error(exc):
                    # Last-resort self-heal for corruption the pre-invoke repair
                    # couldn't fix (e.g. mid-history). Wipe the thread so the
                    # next message starts clean instead of failing forever.
                    _log.warning(
                        "[orch] THREAD-POISONED trace=%s → clearing session=%s",
                        trace_id, session.session_id,
                    )
                    try:
                        await self.clear_session(session.session_id)
                    except Exception:
                        _log.exception(
                            "[orch] clear-after-poison failed session=%s",
                            session.session_id,
                        )
                    err_text = (
                        "Sorry — our previous exchange got into a bad state, so "
                        "I've reset this conversation. Please send your message "
                        "again."
                    )
                else:
                    err_text = f"Sorry, I hit an internal error: {type(exc).__name__}: {exc}"
                return TurnResult(
                    trace_id=trace_id,
                    text=err_text,
                    iterations=0,
                    latency_ms=latency_ms,
                    capped=False,
                    signals=[],
                    new_messages=[],
                    log_payload=self._build_log_payload(
                        trace_id, session, bot_config, latency_ms,
                        0, 0, 0, 0, False, False, [],
                    ),
                )

        latency_ms = int((time.monotonic() - t_start) * 1000)

        # v1 idiom: interrupts surface on the return value, not via aget_state.
        interrupts = result.get("__interrupt__") or []
        interrupt_payload: dict | None = None
        for itr in interrupts:
            value = getattr(itr, "value", None)
            if isinstance(value, dict):
                interrupt_payload = value
                break
        awaiting_clarification = (
            interrupt_payload is not None
            and interrupt_payload.get("type") == "clarification"
        )

        # Slice messages added this turn. Use result["messages"] directly —
        # in v1, ainvoke returns the final state dict.
        all_messages: list[BaseMessage] = list(result.get("messages") or [])
        new_messages_lc: list[BaseMessage] = all_messages[pre_message_count:]

        # Tokens — read deltas off middleware-populated state fields, not by
        # scanning messages. Single source of truth: the TokenUsageMiddleware.
        post_prompt_tokens = int(result.get("prompt_tokens_used", 0) or 0)
        post_completion_tokens = int(result.get("completion_tokens_used", 0) or 0)
        post_cached_tokens = int(result.get("cached_tokens_used", 0) or 0)
        prompt_tokens = max(0, post_prompt_tokens - pre_prompt_tokens)
        completion_tokens = max(0, post_completion_tokens - pre_completion_tokens)
        cached_tokens = max(0, post_cached_tokens - pre_cached_tokens)

        # Signals + clarification.
        signals: list[TurnSignal] = []
        clarification = None
        if awaiting_clarification and interrupt_payload is not None:
            payload = {k: v for k, v in interrupt_payload.items() if k != "type"}
            signals.append(TurnSignal(type="clarification", payload=payload))
            clarification = ClarificationData(
                question=payload.get("question", ""),
                expected=payload.get("expected", "free_text"),
                suggested_replies=list(payload.get("suggested_replies") or []),
            )

        if awaiting_clarification:
            text = clarification.question if clarification else ""
        else:
            text = _last_assistant_text(new_messages_lc)

        # Tool-call trace.
        tool_calls_trace: list[ToolCallTrace] = []
        iterations = 0
        for m in new_messages_lc:
            if isinstance(m, AIMessage):
                iterations += 1
                for tc in m.tool_calls or []:
                    tool_calls_trace.append(
                        ToolCallTrace(
                            name=tc.get("name", "<unknown>"),
                            input=tc.get("args", {}) or {},
                            duration_ms=0,
                            ok=True,
                            output_chars=0,
                        )
                    )
            elif isinstance(m, ToolMessage):
                name = m.name or ""
                if getattr(m, "status", None) == "error" and tool_calls_trace:
                    for trace in reversed(tool_calls_trace):
                        if trace.name == name:
                            trace.ok = False
                            break
                if tool_calls_trace:
                    content = m.content if isinstance(m.content, str) else str(m.content)
                    for trace in reversed(tool_calls_trace):
                        if trace.name == name:
                            trace.output_chars = len(content)
                            break

        _log.info(
            "[orch] TURN-END trace=%s iterations=%d tool_calls=%d awaiting_clar=%s text_chars=%d latency_ms=%d tokens=in:%d/out:%d/cached:%d",
            trace_id, iterations, len(tool_calls_trace), awaiting_clarification,
            len(text or ""), latency_ms,
            prompt_tokens, completion_tokens, cached_tokens,
        )

        # De-duplicate the turn's source documents by id, preserving order, so
        # the same document retrieved by several passages appears once.
        sources: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for src in collected_sources:
            key = src.get("document_id") or ""
            if key in seen_sources:
                continue
            seen_sources.add(key)
            sources.append(src)

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
            sources=sources,
            new_messages=[],
            log_payload=log_payload,
        )

    async def clear_session(self, session_id: str) -> None:
        """Wipe a session's persisted conversation from the checkpointer.

        The agent's real message history lives in the LangGraph checkpointer
        (keyed by thread_id == session_id), NOT in the relational tables. A
        reset must clear it here too, otherwise a poisoned thread (e.g. an
        orphaned tool_call left by a crashed turn) keeps replaying and 400s.
        """
        saver = self._checkpointer
        try:
            if hasattr(saver, "adelete_thread"):
                await saver.adelete_thread(session_id)
            elif hasattr(saver, "delete_thread"):
                saver.delete_thread(session_id)
            else:  # older checkpointer without thread deletion — best effort
                _log.warning(
                    "[orch] checkpointer has no (a)delete_thread; cannot clear "
                    "thread for session=%s", session_id,
                )
        except Exception as exc:
            _log.warning(
                "[orch] clear_session failed for %s (%s: %s)",
                session_id, type(exc).__name__, exc,
            )

    async def get_state_messages(
        self,
        session_id: str,
        bot_config: BotConfig,
        skills: list[Skill],
    ) -> list[BaseMessage]:
        """Return the persisted message history for a session (for /chat/history)."""
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


def _last_assistant_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            content = m.content
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                joined = "".join(parts)
                if joined:
                    return joined
    return ""


def _dangling_tool_messages(messages: list[BaseMessage]) -> list[ToolMessage]:
    """Synthetic tool replies for an open assistant tool_calls turn at the tail.

    OpenAI rejects any history where an assistant message with `tool_calls`
    isn't followed by a tool message per `tool_call_id`. A turn that died after
    the model emitted tool calls (crash, unresumed interrupt, concurrent turn)
    leaves exactly that. We find the last assistant message bearing tool_calls
    and return a placeholder ToolMessage for each id that has no reply, so the
    caller can append them and make the thread valid again.
    """
    if not messages:
        return []
    idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, AIMessage) and m.tool_calls:
            idx = i
            break
        if isinstance(m, HumanMessage):
            # Hit a fresh user turn before any open tool-call assistant message;
            # no tail orphan to repair here.
            return []
    if idx is None:
        return []
    answered = {
        m.tool_call_id
        for m in messages[idx + 1:]
        if isinstance(m, ToolMessage) and m.tool_call_id
    }
    out: list[ToolMessage] = []
    for tc in messages[idx].tool_calls:
        tcid = tc.get("id")
        if tcid and tcid not in answered:
            out.append(
                ToolMessage(
                    content="(no response — previous turn did not complete)",
                    tool_call_id=tcid,
                    name=tc.get("name", "") or "",
                    status="error",
                )
            )
    return out


def _is_orphaned_tool_calls_error(exc: Exception) -> bool:
    """True if `exc` is the provider 400 for an unanswered assistant tool_call."""
    msg = str(exc)
    return (
        "must be followed by tool messages" in msg
        or "did not have response messages" in msg
    )
