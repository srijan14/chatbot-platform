"""Shared return-type dataclasses for the orchestrator → chat handler boundary.

Lifted out of the (now-deleted) legacy `llm_orchestrator.py` so the new
LangGraph orchestrator and any future orchestrator implementations share the
same shape. The fields here are what the chat handler renders into the
`ChatResponse` and what the TurnLog row stores for observability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.chatbot.skills.base import TurnSignal


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
    # Generic signal surface populated by skills (handoff, end_conversation, …).
    # Clarification is special-cased into the dedicated fields below.
    signals: list[TurnSignal] = field(default_factory=list)
    awaiting_clarification: bool = False
    clarification: ClarificationData | None = None
    # Conversation state is now owned by LangGraph's checkpointer; this list
    # stays empty in the LangGraph path. Kept for API parity with the old
    # ConversationManager.persist_turn signature.
    new_messages: list[dict[str, Any]] = field(default_factory=list)
    # Kwargs for inserting a TurnLog row (analytics).
    log_payload: dict[str, Any] = field(default_factory=dict)
