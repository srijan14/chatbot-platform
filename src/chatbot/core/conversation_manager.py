"""Conversation Manager — DB-backed session store (async SQLAlchemy).

Holds the message history per session_id. History stores the raw OpenAI
content-block shape (text + tool_calls + tool_call_id) so multi-turn
conversations preserve state across restarts and across chatbot replicas.

Persistence contract:
- `get_or_create`: loads the session row and its messages, returns a `Session`
  with an in-memory `history` list that the orchestrator mutates.
- `persist_turn`: writes only the messages that were appended during the turn
  (slice from `pre_len` onward), plus the awaiting_clarification flag.
- `reset`: deletes the session row; cascade drops messages.

If a `session_id` reappears with a *different* `customer_id` we wipe the
messages and reset the row's customer_id — same semantic the in-memory
implementation had (a new user took the seat).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.chatbot.observability.logger import get_logger
from src.chatbot.persistence.models import MessageRow, SessionRow

_log = get_logger("conv")

# Payload schema version stored inside MessageRow.payload as `_v`.
PAYLOAD_VERSION = 1


@dataclass
class Session:
    session_id: str
    customer_id: str | None = None
    bot_id: str = "am_marketplace"
    history: list[dict[str, Any]] = field(default_factory=list)
    awaiting_clarification: bool = False


def _wrap_payload(msg: dict[str, Any]) -> str:
    out = dict(msg)
    out["_v"] = PAYLOAD_VERSION
    return json.dumps(out, default=str)


def _unwrap_payload(raw: str) -> dict[str, Any]:
    obj = json.loads(raw)
    obj.pop("_v", None)
    return obj


class ConversationManager:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sm = sessionmaker

    async def get_or_create(
        self,
        session_id: str,
        customer_id: str | None = None,
        bot_id: str = "am_marketplace",
    ) -> Session:
        async with self._sm() as s:
            row = await s.get(SessionRow, session_id)

            if row is None:
                row = SessionRow(
                    session_id=session_id,
                    customer_id=customer_id,
                    bot_id=bot_id,
                )
                s.add(row)
                await s.commit()
                _log.info(
                    "[conv] NEW session=%s customer=%s bot=%s",
                    session_id, customer_id, bot_id,
                )
                return Session(
                    session_id=session_id,
                    customer_id=customer_id,
                    bot_id=bot_id,
                    history=[],
                )

            # Customer switched mid-session — wipe history (different user).
            if customer_id and row.customer_id != customer_id:
                await s.execute(
                    delete(MessageRow).where(MessageRow.session_id == session_id)
                )
                old_customer = row.customer_id
                row.customer_id = customer_id
                row.awaiting_clarification = False
                await s.commit()
                _log.info(
                    "[conv] CUSTOMER-SWITCH session=%s old=%s new=%s (history wiped)",
                    session_id, old_customer, customer_id,
                )
                return Session(
                    session_id=session_id,
                    customer_id=customer_id,
                    bot_id=row.bot_id,
                    history=[],
                )

            stmt = (
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .order_by(MessageRow.ordinal)
            )
            result = await s.execute(stmt)
            history = [_unwrap_payload(m.payload) for m in result.scalars().all()]
            _log.info(
                "[conv] LOAD session=%s customer=%s history_len=%d awaiting_clarification=%s",
                session_id, row.customer_id, len(history), row.awaiting_clarification,
            )
            return Session(
                session_id=session_id,
                customer_id=row.customer_id,
                bot_id=row.bot_id,
                history=history,
                awaiting_clarification=row.awaiting_clarification,
            )

    async def load_session(self, session_id: str) -> Session | None:
        """Read-only: return the Session for `session_id`, or None if absent.

        Unlike `get_or_create`, this never inserts a row — used by the UI's
        page-load hydration so opening a tab doesn't conjure empty sessions.
        """
        async with self._sm() as s:
            row = await s.get(SessionRow, session_id)
            if row is None:
                _log.debug("[conv] LOAD-RO session=%s → not found", session_id)
                return None
            stmt = (
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .order_by(MessageRow.ordinal)
            )
            result = await s.execute(stmt)
            history = [_unwrap_payload(m.payload) for m in result.scalars().all()]
            _log.info(
                "[conv] LOAD-RO session=%s customer=%s history_len=%d awaiting=%s",
                session_id, row.customer_id, len(history), row.awaiting_clarification,
            )
            return Session(
                session_id=session_id,
                customer_id=row.customer_id,
                bot_id=row.bot_id,
                history=history,
                awaiting_clarification=row.awaiting_clarification,
            )

    async def persist_turn(
        self,
        session: Session,
        new_messages: list[dict[str, Any]],
        awaiting_clarification: bool = False,
    ) -> None:
        """Append the messages produced during a single turn and update the
        session row's flags. Single transaction.
        """
        if not new_messages and not awaiting_clarification:
            # Nothing to persist beyond flag changes — still update the session row.
            pass

        async with self._sm() as s:
            row = await s.get(SessionRow, session.session_id)
            if row is None:
                row = SessionRow(
                    session_id=session.session_id,
                    customer_id=session.customer_id,
                    bot_id=session.bot_id,
                )
                s.add(row)
                await s.flush()
                next_ordinal = 0
            else:
                stmt = (
                    select(MessageRow.ordinal)
                    .where(MessageRow.session_id == session.session_id)
                    .order_by(MessageRow.ordinal.desc())
                    .limit(1)
                )
                last = (await s.execute(stmt)).scalar_one_or_none()
                next_ordinal = (last + 1) if last is not None else 0

            for i, msg in enumerate(new_messages):
                s.add(
                    MessageRow(
                        session_id=session.session_id,
                        ordinal=next_ordinal + i,
                        role=str(msg.get("role", "")),
                        payload=_wrap_payload(msg),
                    )
                )
            row.awaiting_clarification = awaiting_clarification
            await s.commit()

        roles = [str(m.get("role", "")) for m in new_messages]
        _log.info(
            "[conv] PERSIST session=%s wrote=%d ordinals=%s roles=%s awaiting_clarification=%s",
            session.session_id, len(new_messages),
            f"[{next_ordinal}..{next_ordinal + len(new_messages) - 1}]" if new_messages else "[]",
            roles, awaiting_clarification,
        )

    async def reset(self, session_id: str) -> None:
        async with self._sm() as s:
            row = await s.get(SessionRow, session_id)
            if row is not None:
                await s.delete(row)
                await s.commit()
                _log.info("[conv] RESET session=%s (row + messages deleted)", session_id)
            else:
                _log.info("[conv] RESET session=%s (no row found, noop)", session_id)
