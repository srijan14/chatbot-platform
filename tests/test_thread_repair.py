"""Unit tests for the orchestrator's poisoned-thread repair helpers.

These cover the pure functions that heal a conversation whose history has an
assistant `tool_calls` message with no matching tool replies — the state that
makes the provider 400 on every subsequent turn.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.chatbot.core.langgraph_orchestrator import (
    _dangling_tool_messages,
    _is_orphaned_tool_calls_error,
)


def _ai_with_tool_calls(*ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "search_knowledge_base", "args": {"query": "x"}, "id": i}
            for i in ids
        ],
    )


def test_no_repair_for_clean_history():
    msgs = [
        HumanMessage(content="hi"),
        _ai_with_tool_calls("call_1"),
        ToolMessage(content="ok", tool_call_id="call_1"),
        AIMessage(content="here you go"),
    ]
    assert _dangling_tool_messages(msgs) == []


def test_no_repair_for_empty_or_plain_tail():
    assert _dangling_tool_messages([]) == []
    assert _dangling_tool_messages([HumanMessage(content="hi")]) == []


def test_repairs_orphaned_tool_call_at_tail():
    # Turn died right after the model emitted the tool call.
    msgs = [HumanMessage(content="cancellation policy?"), _ai_with_tool_calls("call_abc")]
    repair = _dangling_tool_messages(msgs)
    assert len(repair) == 1
    assert isinstance(repair[0], ToolMessage)
    assert repair[0].tool_call_id == "call_abc"
    assert repair[0].status == "error"


def test_repairs_only_unanswered_of_parallel_calls():
    # Two parallel tool calls; only the first got a reply before the crash.
    msgs = [
        HumanMessage(content="q"),
        _ai_with_tool_calls("call_1", "call_2"),
        ToolMessage(content="done", tool_call_id="call_1"),
    ]
    repair = _dangling_tool_messages(msgs)
    assert [m.tool_call_id for m in repair] == ["call_2"]


def test_error_detector_matches_provider_400():
    msg = (
        "Error code: 400 - An assistant message with 'tool_calls' must be "
        "followed by tool messages responding to each 'tool_call_id'. The "
        "following tool_call_ids did not have response messages: call_0XFsz"
    )
    assert _is_orphaned_tool_calls_error(RuntimeError(msg)) is True
    assert _is_orphaned_tool_calls_error(RuntimeError("some other 400")) is False
