"""SQLAlchemy 2.x models for chatbot persistence.

Three tables:
- sessions: one row per chat session_id.
- messages: full OpenAI-shape message JSON per row, ordered by `ordinal`.
- turn_logs: one row per LLM-driven turn, used for analytics/debugging.

`messages.payload` stores the message verbatim as JSON. Justification: the
OpenAI SDK already defines the shape we round-trip on every turn (assistant
content + tool_calls envelope, role:"tool" + tool_call_id). Normalizing those
into columns would force us to re-emit a perfect envelope on read and would
break on SDK shape tweaks. We add `_v` inside the payload so future migrations
are tractable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    bot_id: Mapped[str] = mapped_column(String, default="am_marketplace")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    awaiting_clarification: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list[MessageRow]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageRow.ordinal",
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)  # 'user'|'assistant'|'tool'|'system'
    payload: Mapped[str] = mapped_column(Text)  # full OpenAI-shape JSON, includes {_v:1}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[SessionRow] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_messages_session_ordinal"),
    )


class TurnLog(Base):
    __tablename__ = "turn_logs"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    bot_id: Mapped[str] = mapped_column(String)
    customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    capped: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    response_chars: Mapped[int] = mapped_column(Integer, default=0)
    awaiting_clarification: Mapped[bool] = mapped_column(Boolean, default=False)
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")


Index("ix_turn_logs_session_ts", TurnLog.session_id, TurnLog.ts)
