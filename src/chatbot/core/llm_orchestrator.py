"""LLM Orchestrator — Azure OpenAI tool-use loop.

For each turn we:
  1. Build the messages list: system prompt at position 0, then accumulated
     conversation history, then the new user message.
  2. Call Azure OpenAI Chat Completions with the message list and the union
     of tool schemas contributed by each enabled skill.
  3. If `finish_reason == "tool_calls"`, dispatch every requested tool through
     the skill that owns it. Each returns a `ToolResult` which may carry a
     `TurnSignal` (clarification, handoff, …) and/or a `terminal` flag. The
     orchestrator collects signals and stops the iteration loop when any
     terminal result fires.
  4. Otherwise, return the assistant's text.

The orchestrator is agnostic about *which* tools are terminal — that's the
skill's call. No tool name is special-cased here. New skill types (handoff,
confirmation, end-of-conversation) plug in by returning the right ToolResult.

Prompt caching on Azure OpenAI is automatic for gpt-4o once a prompt crosses
~1024 tokens. We don't have to mark anything; we just keep the system prompt
and tool list at fixed positions so the prefix is identical between turns.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openai import AsyncAzureOpenAI

from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.core.conversation_manager import Session
from src.chatbot.observability.logger import get_logger, new_trace_id, truncate
from src.chatbot.skills.base import Skill, TurnSignal

_log = get_logger("orch")


@dataclass
class ToolCallTrace:
    name: str
    input: dict
    duration_ms: int
    ok: bool
    output_chars: int


@dataclass
class ClarificationData:
    question: str
    expected: str = "free_text"
    suggested_replies: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    trace_id: str
    text: str
    iterations: int
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    capped: bool = False
    # Generic surface: every TurnSignal a skill emitted during this turn.
    signals: list[TurnSignal] = field(default_factory=list)
    # Backward-compat convenience accessors, derived from `signals` after the
    # loop. New code should prefer iterating `signals` directly.
    awaiting_clarification: bool = False
    clarification: ClarificationData | None = None
    # Messages appended to session.history during this turn — the chat handler
    # persists these via ConversationManager.persist_turn.
    new_messages: list[dict[str, Any]] = field(default_factory=list)
    # Turn-log payload (TurnLog row kwargs). The chat handler persists this.
    log_payload: dict[str, Any] = field(default_factory=dict)


def _serialize_assistant(msg: Any) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a JSON-serializable history entry.

    OpenAI requires assistant messages with tool_calls to be sent back exactly as
    received (id, type, function.name, function.arguments) — otherwise a follow-up
    `role:"tool"` message has nothing to attach to.
    """
    record: dict = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        record["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return record


def _detect_pending_clarification(history: list[dict]) -> dict | None:
    """If the tail of history is a paused ask_clarification (assistant emitted
    the tool call + the placeholder tool message was appended), return the
    parsed args of that call. Otherwise None. Observability-only — the
    orchestrator does not branch on this.
    """
    if len(history) < 2:
        return None
    last = history[-1]
    prev = history[-2]
    if last.get("role") != "tool":
        return None
    if not isinstance(last.get("content"), str) or "awaiting" not in last["content"].lower():
        return None
    if prev.get("role") != "assistant":
        return None
    for tc in prev.get("tool_calls") or []:
        fn = tc.get("function") or {}
        if fn.get("name") == "ask_clarification" and tc.get("id") == last.get("tool_call_id"):
            try:
                return json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                return {}
    return None


class LLMOrchestrator:
    def __init__(self, client: AsyncAzureOpenAI):
        self.client = client

    async def run_turn(
        self,
        session: Session,
        user_message: str,
        bot_config: BotConfig,
        skills: list[Skill],
    ) -> TurnResult:
        trace_id = new_trace_id()
        t0 = time.perf_counter()

        _log.info(
            "[orch] TURN-START trace=%s session=%s customer=%s history_len_before=%d message=%r",
            trace_id, session.session_id, session.customer_id,
            len(session.history), truncate(user_message, 120),
        )

        # Best-effort detection that the previous turn ended in a paused
        # clarification: the last assistant message emitted ask_clarification
        # and the placeholder tool message followed. We don't gate any logic
        # on this — purely for learning/observability. If it's true the new
        # user_message is the *answer* to the previous question.
        pending_clar = _detect_pending_clarification(session.history)
        if pending_clar is not None:
            _log.info(
                "[orch] CLARIFICATION-CONTINUATION trace=%s answering_question=%r expected=%s",
                trace_id,
                truncate(pending_clar.get("question") or "", 120),
                pending_clar.get("expected") or "<unknown>",
            )

        # Build the system prompt in three layers:
        #   1. bot persona (from YAML)
        #   2. additions contributed by each enabled skill (e.g. how to use
        #      ask_clarification — the platform-generic rule lives with the skill)
        #   3. authenticated-customer context (per turn)
        system_prompt = bot_config.system_prompt
        sys_addons = [s.system_prompt_addition() for s in skills]
        sys_addons = [a for a in sys_addons if a]
        if sys_addons:
            system_prompt = system_prompt.rstrip() + "\n\n" + "\n\n".join(sys_addons)
        if session.customer_id:
            system_prompt = (
                f"Current authenticated customer: customer_id={session.customer_id}. "
                f"Use this id in tool calls; do not ask the user for it.\n\n"
                f"{system_prompt}"
            )

        # Union of tool schemas across enabled skills.
        openai_tools: list[dict] = []
        per_skill_counts: list[tuple[str, int]] = []
        for skill in skills:
            schemas = await skill.prepare_tools()
            per_skill_counts.append((skill.name, len(schemas)))
            openai_tools.extend(schemas)
        _log.info(
            "[orch] TOOLS-ASSEMBLED trace=%s skills=%s total=%d",
            trace_id, per_skill_counts, len(openai_tools),
        )
        _log.debug(
            "[orch] tool_names trace=%s names=%s",
            trace_id, [t["function"]["name"] for t in openai_tools],
        )

        # Append the new user turn so the model sees it on the first iteration.
        user_msg = {"role": "user", "content": user_message}
        session.history.append(user_msg)
        pre_persist_index = len(session.history) - 1  # index of the new user msg

        result = TurnResult(trace_id=trace_id, text="", iterations=0)

        for iteration in range(bot_config.max_tool_iterations):
            result.iterations = iteration + 1
            _log.info(
                "[orch] LLM-CALL trace=%s iter=%d history_len=%d model=%s",
                trace_id, iteration + 1, len(session.history), bot_config.llm_deployment,
            )

            # Reasoning models (o-series) and chat models use different param names.
            params: dict = {
                "model": bot_config.llm_deployment,
                "messages": [{"role": "system", "content": system_prompt}, *session.history],
                "tools": openai_tools,
                "tool_choice": "auto",
            }
            if bot_config.llm_reasoning:
                params["max_completion_tokens"] = bot_config.max_tokens
            else:
                params["max_tokens"] = bot_config.max_tokens
                params["temperature"] = bot_config.temperature

            response = await self.client.chat.completions.create(**params)

            choice = response.choices[0]
            msg = choice.message
            finish_reason = choice.finish_reason

            usage = getattr(response, "usage", None)
            iter_prompt = iter_completion = iter_cached = 0
            if usage is not None:
                iter_prompt = getattr(usage, "prompt_tokens", 0) or 0
                iter_completion = getattr(usage, "completion_tokens", 0) or 0
                details = getattr(usage, "prompt_tokens_details", None)
                if details is not None:
                    iter_cached = getattr(details, "cached_tokens", 0) or 0
                result.prompt_tokens += iter_prompt
                result.completion_tokens += iter_completion
                result.cached_tokens += iter_cached

            n_tool_calls = len(msg.tool_calls or [])
            _log.info(
                "[orch] LLM-RESPONSE trace=%s iter=%d finish=%s tool_calls=%d "
                "tokens=in:%d/out:%d/cached:%d",
                trace_id, iteration + 1, finish_reason, n_tool_calls,
                iter_prompt, iter_completion, iter_cached,
            )
            assistant_record = _serialize_assistant(msg)
            _log.debug(
                "[orch] assistant_message trace=%s payload=%s",
                trace_id, truncate(assistant_record, 400),
            )
            session.history.append(assistant_record)

            if finish_reason != "tool_calls" or not msg.tool_calls:
                result.text = msg.content or ""
                _log.info(
                    "[orch] FINAL-TEXT trace=%s chars=%d text=%r",
                    trace_id, len(result.text), truncate(result.text, 200),
                )
                break

            # Dispatch every tool uniformly. A skill may return a terminal
            # ToolResult (e.g. clarification), in which case we still emit
            # placeholder role:"tool" messages for any remaining tool_calls —
            # OpenAI requires every tool_call_id to be answered — and then
            # exit the iteration loop.
            terminal_seen = False
            terminal_visible_text: str | None = None
            for tc in msg.tool_calls:
                if terminal_seen:
                    placeholder = "(skipped: terminal signal pending)"
                    session.history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": placeholder,
                    })
                    _log.debug(
                        "[orch] tool_placeholder trace=%s tool_call_id=%s content=%r",
                        trace_id, tc.id, placeholder,
                    )
                    continue

                t_start = time.perf_counter()
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}

                _log.info(
                    "[orch] TOOL-DISPATCH trace=%s iter=%d name=%s id=%s args=%s",
                    trace_id, iteration + 1, tc.function.name, tc.id, truncate(args, 200),
                )

                handler = next((s for s in skills if s.owns_tool(tc.function.name)), None)
                if handler is None:
                    text, is_err = (
                        f"Tool '{tc.function.name}' is not available on this bot.",
                        True,
                    )
                    tool_result = None
                    _log.warning(
                        "[orch] tool_unhandled trace=%s name=%s — no skill owns it",
                        trace_id, tc.function.name,
                    )
                else:
                    try:
                        tool_result = await handler.execute_tool(tc.function.name, args)
                        text, is_err = tool_result.text, tool_result.is_error
                    except Exception as e:
                        text, is_err, tool_result = (
                            f"Tool execution error: {e}",
                            True,
                            None,
                        )

                duration_ms = int((time.perf_counter() - t_start) * 1000)
                _log.info(
                    "[orch] TOOL-RESULT  trace=%s iter=%d name=%s duration=%dms ok=%s "
                    "signal=%s terminal=%s output=%r",
                    trace_id, iteration + 1, tc.function.name, duration_ms,
                    not is_err,
                    tool_result.signal.type if (tool_result and tool_result.signal) else None,
                    bool(tool_result and tool_result.terminal),
                    truncate(text, 200),
                )

                result.tool_calls.append(ToolCallTrace(
                    name=tc.function.name,
                    input=args,
                    duration_ms=duration_ms,
                    ok=not is_err,
                    output_chars=len(text),
                ))
                session.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": text or "(empty)",
                })

                if tool_result is not None:
                    if tool_result.signal is not None:
                        result.signals.append(tool_result.signal)
                        _log.info(
                            "[orch] SIGNAL-COLLECTED trace=%s type=%s payload=%s "
                            "(total_signals=%d)",
                            trace_id,
                            tool_result.signal.type,
                            truncate(tool_result.signal.payload, 240),
                            len(result.signals),
                        )
                    if tool_result.terminal:
                        terminal_seen = True
                        if tool_result.user_visible_text:
                            terminal_visible_text = tool_result.user_visible_text

            if terminal_seen:
                if terminal_visible_text:
                    result.text = terminal_visible_text
                break
            # No terminal signal — fall through to the next LLM iteration.
        else:
            result.capped = True
            last = session.history[-1] if session.history else None
            if last and last.get("role") == "assistant":
                result.text = last.get("content") or (
                    "(I hit my tool-iteration cap. Please clarify or try again.)"
                )
            _log.warning(
                "[orch] CAP-HIT trace=%s max_iterations=%d",
                trace_id, bot_config.max_tool_iterations,
            )

        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        result.new_messages = session.history[pre_persist_index:]

        # Derive backward-compat clarification fields from the generic signals
        # list. Callers that don't care about clarification specifically can
        # just iterate `result.signals`.
        for sig in result.signals:
            if sig.type == "clarification":
                result.awaiting_clarification = True
                result.clarification = ClarificationData(
                    question=sig.payload.get("question", ""),
                    expected=sig.payload.get("expected", "free_text"),
                    suggested_replies=list(sig.payload.get("suggested_replies") or []),
                )
                break

        _log.info(
            "[orch] TURN-END   trace=%s iter=%d latency=%dms awaiting_clarification=%s "
            "new_messages=%d tokens=in:%d/out:%d/cached:%d",
            trace_id, result.iterations, result.latency_ms,
            result.awaiting_clarification, len(result.new_messages),
            result.prompt_tokens, result.completion_tokens, result.cached_tokens,
        )

        result.log_payload = {
            "trace_id": result.trace_id,
            "session_id": session.session_id,
            "bot_id": bot_config.bot_id,
            "customer_id": session.customer_id,
            "ts": datetime.now(timezone.utc),
            "iterations": result.iterations,
            "capped": result.capped,
            "tool_calls": [
                {"name": tc.name, "ok": tc.ok, "duration_ms": tc.duration_ms,
                 "output_chars": tc.output_chars}
                for tc in result.tool_calls
            ],
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cached_tokens": result.cached_tokens,
            "latency_ms": result.latency_ms,
            "response_chars": len(result.text),
            "awaiting_clarification": result.awaiting_clarification,
            # Full signal payloads so a turn log row is self-contained for
            # post-hoc clarification debugging.
            "signals": [
                {"type": s.type, "payload": s.payload} for s in result.signals
            ],
        }

        return result
