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


class ChatResponse(BaseModel):
    session_id: str
    trace_id: str
    text: str
    iterations: int
    capped: bool
    tool_calls: list[ToolCallTraceOut]
    latency_ms: int
    tokens: dict
    awaiting_clarification: bool = False
    clarification: Optional[ClarificationOut] = None
