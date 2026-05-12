"""Conversation persistence: round-trip session history through SQLite."""
from __future__ import annotations

import pytest

from src.chatbot.core.conversation_manager import ConversationManager
from src.chatbot.persistence.models import MessageRow, SessionRow
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_then_load_restores_history(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)

    sess = await cm.get_or_create("s1", customer_id="CUST001")
    assert sess.history == []

    new_msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
    ]
    await cm.persist_turn(sess, new_msgs, awaiting_clarification=False)

    # Reload via a fresh manager instance — exercises actual DB path, not state.
    cm2 = ConversationManager(db_sessionmaker)
    reloaded = await cm2.get_or_create("s1", customer_id="CUST001")
    assert reloaded.customer_id == "CUST001"
    assert [m["role"] for m in reloaded.history] == ["user", "assistant"]
    assert reloaded.history[0]["content"] == "hello"
    assert reloaded.history[1]["content"] == "hi back"
    # The _v marker must NOT bleed into the in-memory history shape.
    assert "_v" not in reloaded.history[0]


@pytest.mark.asyncio
async def test_appends_continue_ordinals(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s2", customer_id="CUST001")

    await cm.persist_turn(sess, [{"role": "user", "content": "one"}])
    await cm.persist_turn(sess, [{"role": "assistant", "content": "two"}])

    async with db_sessionmaker() as s:
        rows = (await s.execute(
            select(MessageRow).where(MessageRow.session_id == "s2").order_by(MessageRow.ordinal)
        )).scalars().all()
    assert [r.ordinal for r in rows] == [0, 1]
    assert [r.role for r in rows] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_customer_switch_wipes_history(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s3", customer_id="CUST001")
    await cm.persist_turn(sess, [{"role": "user", "content": "first user"}])

    switched = await cm.get_or_create("s3", customer_id="CUST002")
    assert switched.customer_id == "CUST002"
    assert switched.history == []

    async with db_sessionmaker() as s:
        rows = (await s.execute(
            select(MessageRow).where(MessageRow.session_id == "s3")
        )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_reset_deletes_session_and_messages(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s4", customer_id="CUST001")
    await cm.persist_turn(sess, [{"role": "user", "content": "x"}])

    await cm.reset("s4")
    async with db_sessionmaker() as s:
        assert await s.get(SessionRow, "s4") is None


@pytest.mark.asyncio
async def test_awaiting_clarification_flag_persisted(db_sessionmaker):
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s5", customer_id="CUST001")

    await cm.persist_turn(
        sess,
        [{"role": "user", "content": "change my plan"}],
        awaiting_clarification=True,
    )
    async with db_sessionmaker() as s:
        row = await s.get(SessionRow, "s5")
    assert row is not None and row.awaiting_clarification is True

    # Subsequent turn clears it.
    await cm.persist_turn(
        sess,
        [{"role": "assistant", "content": "done"}],
        awaiting_clarification=False,
    )
    async with db_sessionmaker() as s:
        row = await s.get(SessionRow, "s5")
    assert row is not None and row.awaiting_clarification is False


@pytest.mark.asyncio
async def test_tool_call_envelope_round_trips(db_sessionmaker):
    """Assistant messages with tool_calls must survive round-trip verbatim,
    or the next LLM call will 400 with an envelope mismatch."""
    cm = ConversationManager(db_sessionmaker)
    sess = await cm.get_or_create("s6", customer_id="CUST001")

    assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "get_customer_profile",
                             "arguments": '{"customer_id":"CUST001"}'},
            }
        ],
    }
    tool_msg = {
        "role": "tool",
        "tool_call_id": "call_abc",
        "content": "{\"name\":\"Aarav\"}",
    }
    await cm.persist_turn(sess, [assistant_msg, tool_msg])

    cm2 = ConversationManager(db_sessionmaker)
    reloaded = await cm2.get_or_create("s6", customer_id="CUST001")
    assert reloaded.history[0]["tool_calls"][0]["id"] == "call_abc"
    assert reloaded.history[0]["tool_calls"][0]["function"]["name"] == "get_customer_profile"
    assert reloaded.history[1]["tool_call_id"] == "call_abc"
