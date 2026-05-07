"""LLM Orchestrator — Azure OpenAI tool-use loop.

For each turn we:
  1. Build the messages list: system prompt at position 0, then accumulated
     conversation history, then the new user message.
  2. Call Azure OpenAI Chat Completions with the message list and tool schemas.
  3. If `finish_reason == "tool_calls"`, execute every requested tool via MCP and
     append a `role: "tool"` message per call to the history. Then loop.
  4. Otherwise, return the assistant's text.

Prompt caching on Azure OpenAI is automatic for gpt-4o once a prompt crosses
~1024 tokens. We don't have to mark anything; we just keep the system prompt and
tool list at fixed positions so the prefix is identical between turns. The
`prompt_tokens_details.cached_tokens` field on the usage report tells us how
much of the prompt was served from cache.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncAzureOpenAI

from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.core.conversation_manager import Session
from src.chatbot.skills.tool_call_skill import ToolCallSkill
from src.chatbot.observability.logger import new_trace_id, now_iso, log_turn


@dataclass
class ToolCallTrace:
    name: str
    input: dict
    duration_ms: int
    ok: bool
    output_chars: int


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


class LLMOrchestrator:
    def __init__(self, client: AsyncAzureOpenAI):
        self.client = client

    async def run_turn(
        self,
        session: Session,
        user_message: str,
        bot_config: BotConfig,
        skill: ToolCallSkill,
    ) -> TurnResult:
        trace_id = new_trace_id()
        t0 = time.perf_counter()

        # Inject the authenticated customer into the system prompt so the model
        # passes the right customer_id without nagging the user for it.
        system_prompt = bot_config.system_prompt
        if session.customer_id:
            system_prompt = (
                f"Current authenticated customer: customer_id={session.customer_id}. "
                f"Use this id in tool calls; do not ask the user for it.\n\n"
                f"{system_prompt}"
            )

        openai_tools = await skill.prepare_tools()

        # Append the new user turn so the model sees it on the first iteration.
        session.history.append({"role": "user", "content": user_message})

        result = TurnResult(trace_id=trace_id, text="", iterations=0)

        for iteration in range(bot_config.max_tool_iterations):
            result.iterations = iteration + 1

            # Reasoning models (o-series) and chat models use different param names.
            # Reasoning: max_completion_tokens (covers reasoning + output tokens),
            # no `temperature` (only default 1.0 is allowed). Chat: max_tokens + temp.
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
            if usage is not None:
                result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                details = getattr(usage, "prompt_tokens_details", None)
                if details is not None:
                    result.cached_tokens += getattr(details, "cached_tokens", 0) or 0

            session.history.append(_serialize_assistant(msg))

            if finish_reason != "tool_calls" or not msg.tool_calls:
                result.text = msg.content or ""
                break

            # Execute every tool the model requested in this round; each gets its
            # own `role:"tool"` message echoing the matching tool_call_id.
            for tc in msg.tool_calls:
                t_start = time.perf_counter()
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                try:
                    text, is_err = await skill.execute_tool(tc.function.name, args)
                except Exception as e:
                    text, is_err = f"Tool execution error: {e}", True
                duration_ms = int((time.perf_counter() - t_start) * 1000)
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
        else:
            result.capped = True
            last = session.history[-1] if session.history else None
            if last and last.get("role") == "assistant":
                result.text = last.get("content") or (
                    "(I hit my tool-iteration cap. Please clarify or try again.)"
                )

        result.latency_ms = int((time.perf_counter() - t0) * 1000)

        log_turn({
            "ts": now_iso(),
            "trace_id": result.trace_id,
            "session_id": session.session_id,
            "bot_id": bot_config.bot_id,
            "customer_id": session.customer_id,
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
        })

        return result
