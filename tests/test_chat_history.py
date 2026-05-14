"""Tests for the /chat/history pipeline: read-only session loader and the
visible-bubble filter that strips OpenAI tool plumbing."""
from __future__ import annotations

import pytest

from src.chatbot.api.chat import _to_visible_messages
from src.chatbot.api.schemas import HistoryMessage
from src.chatbot.core.conversation_manager import ConversationManager


@pytest.mark.asyncio
async def test_load_session_returns_none_when_absent(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    assert await cm.load_session("does-not-exist") is None


@pytest.mark.asyncio
async def test_load_session_does_not_create_row(db_sessionmaker):
    """Page-load fetches must not implicitly create empty sessions."""
    cm = ConversationManager(db_sessionmaker)
    await cm.load_session("nope")
    # Second load still finds nothing (no row was accidentally written).
    assert await cm.load_session("nope") is None


@pytest.mark.asyncio
async def test_load_session_returns_history_and_flag(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s1", customer_id="CUST007")
    await cm.persist_turn(
        sess,
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        awaiting_clarification=False,
    )

    loaded = await cm.load_session("s1")
    assert loaded is not None
    assert loaded.customer_id == "CUST007"
    assert [m["role"] for m in loaded.history] == ["user", "assistant"]
    assert loaded.awaiting_clarification is False


@pytest.mark.asyncio
async def test_load_session_surfaces_awaiting_flag(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s2", customer_id="CUST006")
    await cm.persist_turn(
        sess,
        [{"role": "user", "content": "pay my bill"}],
        awaiting_clarification=True,
    )
    loaded = await cm.load_session("s2")
    assert loaded is not None and loaded.awaiting_clarification is True


# --- visible-message filter -------------------------------------------------

def test_visible_filter_keeps_user_and_assistant_text():
    raw = [
        {"role": "user", "content": "what plan am I on?"},
        {"role": "assistant", "content": "You're on Pro 599."},
    ]
    out = _to_visible_messages(raw)
    assert out == [
        HistoryMessage(role="user", text="what plan am I on?"),
        HistoryMessage(role="assistant", text="You're on Pro 599."),
    ]


def test_visible_filter_drops_tool_plumbing():
    """Assistant tool_call envelopes + role:'tool' messages must be hidden."""
    raw = [
        {"role": "user", "content": "what plan am I on?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_x",
                "type": "function",
                "function": {"name": "get_current_plan",
                             "arguments": '{"customer_id":"CUST001"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_x", "content": '{"plan_id":"PRO_599"}'},
        {"role": "assistant", "content": "You're on Pro 599."},
    ]
    out = _to_visible_messages(raw)
    assert [(m.role, m.text) for m in out] == [
        ("user", "what plan am I on?"),
        ("assistant", "You're on Pro 599."),
    ]


def test_visible_filter_extracts_clarification_question():
    """Clarification turns store the question only inside the tool_call args.
    The history view must surface it as a normal assistant bubble."""
    raw = [
        {"role": "user", "content": "change my plan"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_clar",
                "type": "function",
                "function": {
                    "name": "ask_clarification",
                    "arguments": '{"question":"Which plan would you like?","expected":"plan_id"}',
                },
            }],
        },
        {"role": "tool", "tool_call_id": "call_clar", "content": "(awaiting user response)"},
    ]
    out = _to_visible_messages(raw)
    assert [(m.role, m.text) for m in out] == [
        ("user", "change my plan"),
        ("assistant", "Which plan would you like?"),
    ]


def test_visible_filter_skips_malformed_clarify_args():
    """Garbage in tool_call.arguments shouldn't crash the history view."""
    raw = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_x",
                "type": "function",
                "function": {"name": "ask_clarification", "arguments": "not-json"},
            }],
        },
    ]
    out = _to_visible_messages(raw)
    assert out == []
