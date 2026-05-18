"""API request/response schemas for the chatbot service."""
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Stable session ID per browser tab / chat thread.")
    customer_id: str = Field(..., description="Demo customer to authenticate as (CUST001..CUST005).")
    message: str = Field(..., min_length=1)
    bot_id: str = "telecom_support"


class ToolCallTraceOut(BaseModel):
    name: str
    input: dict
    duration_ms: int
    ok: bool


class ClarificationOut(BaseModel):
    question: str
    expected: str = "free_text"
    suggested_replies: list[str] = Field(default_factory=list)


class TurnSignalOut(BaseModel):
    """Generic structured event the bot wants surfaced to the caller / UI.

    Type-specific payloads (no platform-enforced schema beyond type+payload):
      - "clarification":         {question, expected, suggested_replies}
      - "confirmation_required": {summary, action, options}
      - "handoff":               {reason, queue}
      - "end_conversation":      {reason}
    New types slot in without core changes.
    """
    type: str
    payload: dict


class ChatResponse(BaseModel):
    session_id: str
    trace_id: str
    text: str
    iterations: int
    capped: bool
    tool_calls: list[ToolCallTraceOut]
    latency_ms: int
    tokens: dict
    # Generic surface: every TurnSignal a skill emitted during this turn.
    signals: list[TurnSignalOut] = Field(default_factory=list)
    # Backward-compat fields, derived from `signals` (clarification type).
    # New clients should iterate `signals` directly.
    awaiting_clarification: bool = False
    clarification: Optional[ClarificationOut] = None


class HistoryMessage(BaseModel):
    role: str  # "user" | "assistant"
    text: str


class HistoryResponse(BaseModel):
    """Read model for GET /chat/history.

    Strips the internal LLM plumbing (tool_call envelopes, role:"tool"
    messages, intermediate assistant tool-only turns) and surfaces just the
    visible chat bubbles. When the bot is awaiting a clarification, the
    question is extracted from the corresponding tool_call args so it appears
    as a normal assistant bubble on reload.
    """
    session_id: str
    customer_id: Optional[str] = None
    bot_id: Optional[str] = None
    awaiting_clarification: bool = False
    messages: list[HistoryMessage] = Field(default_factory=list)
