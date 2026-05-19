"""Tests for the /chat/history pipeline: read-only session loader (metadata)
and the visible-bubble filter that strips LangChain tool plumbing."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.chatbot.api.chat import _messages_to_visible
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
async def test_load_session_surfaces_awaiting_flag(db_sessionmaker):
    """ConversationManager persists awaiting_clarification on SessionRow even
    when no messages were written this turn (the new chat handler relies on
    this so it knows whether to resume an interrupted graph)."""
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s2", customer_id="CUST006")
    await cm.persist_turn(sess, new_messages=[], awaiting_clarification=True)
    loaded = await cm.load_session("s2")
    assert loaded is not None and loaded.awaiting_clarification is True


# --- visible-message filter -------------------------------------------------

def test_visible_filter_keeps_user_and_assistant_text():
    raw = [
        HumanMessage(content="what plan am I on?"),
        AIMessage(content="You're on Pro 599."),
    ]
    out = _messages_to_visible(raw)
    assert out == [
        HistoryMessage(role="user", text="what plan am I on?"),
        HistoryMessage(role="assistant", text="You're on Pro 599."),
    ]


def test_visible_filter_drops_tool_plumbing():
    """Assistant tool_call envelopes + ToolMessage rows must be hidden."""
    raw = [
        HumanMessage(content="what plan am I on?"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_x",
                "name": "get_current_plan",
                "args": {"customer_id": "CUST001"},
            }],
        ),
        ToolMessage(content='{"plan_id":"PRO_599"}', tool_call_id="call_x"),
        AIMessage(content="You're on Pro 599."),
    ]
    out = _messages_to_visible(raw)
    assert [(m.role, m.text) for m in out] == [
        ("user", "what plan am I on?"),
        ("assistant", "You're on Pro 599."),
    ]


def test_visible_filter_extracts_clarification_question():
    """Clarification turns store the question only inside the tool_call args.
    The history view must surface it as a normal assistant bubble."""
    raw = [
        HumanMessage(content="change my plan"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_clar",
                "name": "ask_clarification",
                "args": {"question": "Which plan would you like?", "expected": "plan_id"},
            }],
        ),
    ]
    out = _messages_to_visible(raw)
    assert [(m.role, m.text) for m in out] == [
        ("user", "change my plan"),
        ("assistant", "Which plan would you like?"),
    ]


def test_visible_filter_drops_system_messages():
    raw = [
        SystemMessage(content="You are an assistant."),
        HumanMessage(content="hi"),
        AIMessage(content="hello!"),
    ]
    out = _messages_to_visible(raw)
    assert [(m.role, m.text) for m in out] == [("user", "hi"), ("assistant", "hello!")]


def test_visible_filter_skips_assistant_with_unknown_tool_only():
    """Assistant tool_call envelopes for non-clarification tools must NOT
    surface as a bubble (they're internal — the next assistant message after
    the tool result is the user-visible reply)."""
    raw = [
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_x",
                "name": "get_current_plan",
                "args": {"customer_id": "CUST001"},
            }],
        ),
    ]
    assert _messages_to_visible(raw) == []
