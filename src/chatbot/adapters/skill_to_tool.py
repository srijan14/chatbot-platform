"""Adapter: Skill (chatbot platform) → list[BaseTool] (LangChain).

A Skill declares OpenAI-format tool schemas + an `execute_tool(name, args)`
coroutine. LangGraph's `ToolNode` wants LangChain `BaseTool`s with Pydantic
arg schemas. This adapter bridges the two so the existing skills run inside
a LangGraph graph unchanged.

Special handling lives here, not in the skills:
  • If a tool returns a `terminal` ToolResult with a `clarification` signal,
    we route through LangGraph's `interrupt()`. The graph pauses; the user's
    next message resumes via `Command(resume=…)`. The skill itself stays
    LangGraph-agnostic.
  • If a tool returns `is_error=True`, we raise so LangGraph delivers an
    error `ToolMessage` to the agent (which may then retry or surrender).
  • Non-clarification terminal signals (handoff, end_conversation, …) are
    serialised into the tool's text return — the agent sees them and can
    either acknowledge or echo them. The chat handler extracts them from
    the final state.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from src.chatbot.adapters.json_schema_to_pydantic import build_args_model
from src.chatbot.core.turn_context import add_sources
from src.chatbot.observability.logger import get_logger, truncate
from src.chatbot.skills.base import Skill, ToolResult

_log = get_logger("orch")


async def skill_to_langchain_tools(skill: Skill) -> list[StructuredTool]:
    """Return one StructuredTool per OpenAI tool schema the skill declares."""
    openai_tools = await skill.prepare_tools()
    tools: list[StructuredTool] = []
    for spec in openai_tools:
        fn = spec.get("function", {})
        name = fn["name"]
        description = fn.get("description", "")
        parameters = fn.get("parameters") or {"type": "object", "properties": {}}
        args_model = build_args_model(f"{name}_Args", parameters)

        # Snapshot loop-variables into defaults so each tool binds its own.
        async def _invoke(
            _skill: Skill = skill,
            _name: str = name,
            **kwargs: Any,
        ) -> str:
            # Drop None values so a skill's `arguments.get(...)` defaults still
            # apply — LangChain populates omitted fields with the model's None.
            args = {k: v for k, v in kwargs.items() if v is not None}
            _log.info(
                "[orch] TOOL-INVOKE skill=%s tool=%s args=%s",
                _skill.name, _name, truncate(args, 240),
            )
            result: ToolResult = await _skill.execute_tool(_name, args)
            # Collect any source references into the turn's collector so they
            # reach the chat response (see core/turn_context.py).
            add_sources(result.sources)
            return _handle_result(_name, result)

        tool = StructuredTool.from_function(
            coroutine=_invoke,
            name=name,
            description=description,
            args_schema=args_model,
        )
        tools.append(tool)
    return tools


def _handle_result(tool_name: str, result: ToolResult) -> str:
    """Translate a ToolResult into a LangChain tool return value.

    - clarification signal + terminal → call interrupt(payload); return the
      user's resume value as the tool's text so the agent can continue.
    - other signals → embedded as JSON in the tool's text so the agent sees
      them; the chat handler may also extract them from graph state.
    - is_error → raise; LangGraph wraps this as an error ToolMessage.
    - normal path → return ToolResult.text.
    """
    if result.signal and result.signal.type == "clarification" and result.terminal:
        payload = {
            "type": "clarification",
            **result.signal.payload,
        }
        _log.info(
            "[orch] TOOL-INTERRUPT tool=%s payload=%s",
            tool_name, truncate(result.signal.payload, 240),
        )
        # interrupt() pauses graph execution. When the user replies, the graph
        # resumes from this exact call site and `interrupt()` returns the
        # resume value (the user's text). We surface that to the agent as the
        # tool's "result", so the next agent step has the answer in history.
        user_reply = interrupt(payload)
        return f"User replied: {user_reply}"

    if result.is_error:
        # LangGraph's ToolNode catches exceptions and feeds them back to the
        # agent as an error ToolMessage — exactly the recovery path we want.
        raise RuntimeError(result.text or "Tool reported is_error with no text")

    if result.signal is not None:
        # Non-clarification signals (handoff, end_conversation, confirmation,…)
        # — embed in the tool text so the agent sees them. The chat handler
        # extracts them from `state["signals"]` for the response payload.
        text = result.text or ""
        return f"{text}\n[signal:{result.signal.type}] {json.dumps(result.signal.payload)}"

    return result.text or ""
