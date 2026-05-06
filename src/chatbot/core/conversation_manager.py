"""Conversation Manager — in-process session store for the POC.

Holds the message history per session_id. History stores the raw Anthropic content-block
shape (text + tool_use + tool_result) so multi-turn conversations preserve state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    session_id: str
    customer_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class ConversationManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str, customer_id: str | None = None) -> Session:
        sess = self._sessions.get(session_id)
        if sess is None:
            sess = Session(session_id=session_id, customer_id=customer_id)
            self._sessions[session_id] = sess
        elif customer_id and sess.customer_id != customer_id:
            # Customer switched mid-session — start fresh history (a different user).
            sess = Session(session_id=session_id, customer_id=customer_id)
            self._sessions[session_id] = sess
        return sess

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
