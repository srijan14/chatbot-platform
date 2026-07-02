"""API request/response schemas for the chatbot service."""
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Stable session ID per browser tab / chat thread.")
    customer_id: str = Field(
        ...,
        description="End-user identity for this session. Used for the per-user "
        "daily token budget and for audit/turn-log attribution. Opaque to the "
        "platform — any stable per-user string works.",
    )
    message: str = Field(..., min_length=1)
    bot_id: str = "am_marketplace"


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


class SourceRef(BaseModel):
    """A source document the assistant's answer was grounded in this turn."""
    document_id: str = Field(..., description="The document's id (source URI).")
    title: Optional[str] = Field(
        default=None, description="Display title — section heading or filename."
    )
    url: Optional[str] = Field(
        default=None,
        description="Link to the source document (a presigned URL when object "
        "storage is configured), or null if none is available.",
    )


class ChatResponse(BaseModel):
    session_id: str
    trace_id: str
    text: str
    iterations: int
    capped: bool
    tool_calls: list[ToolCallTraceOut]
    latency_ms: int
    tokens: dict
    # Source documents the answer drew on (RAG citations), de-duplicated.
    sources: list[SourceRef] = Field(default_factory=list)
    # Generic surface: every TurnSignal a skill emitted during this turn.
    signals: list[TurnSignalOut] = Field(default_factory=list)
    # Backward-compat fields, derived from `signals` (clarification type).
    # New clients should iterate `signals` directly.
    awaiting_clarification: bool = False
    clarification: Optional[ClarificationOut] = None


class DocumentUpsertRequest(BaseModel):
    """Add or update one knowledge-base document for a bot."""
    id: str = Field(
        ...,
        min_length=1,
        description="Stable, caller-chosen document identifier (also stored as "
        "the source URI). Re-using it updates the existing document in place. "
        "Use a URL-safe string, e.g. 'refund-policy' or 'policies/roaming.md'.",
    )
    content: str = Field(..., min_length=1, description="The full document text.")
    mime_type: Optional[str] = Field(
        default=None,
        description="Optional MIME type. Defaults to inference from the id's "
        "extension ('.md' → markdown chunking with headings; otherwise plain).",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Optional metadata stored alongside the document's chunks.",
    )


class DocumentUpsertResponse(BaseModel):
    bot_id: str
    collection: str
    document_id: str          # the caller's `id` / source URI
    doc_id: str               # internal deterministic id
    status: str               # "created" | "updated" | "unchanged"
    chunks: int
    embedded: int
    upserted: int
    # Stored original artifact + link to fetch it back.
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    filename: Optional[str] = None
    download_url: Optional[str] = None


class DocumentInfo(BaseModel):
    document_id: str          # source URI (the caller's `id`)
    doc_id: str
    chunk_count: int
    ingested_at: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    # Stored original artifact + link to fetch it back.
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    download_url: Optional[str] = None


class DocumentListResponse(BaseModel):
    bot_id: str
    collection: str
    count: int
    documents: list[DocumentInfo] = Field(default_factory=list)


class DocumentDeleteResponse(BaseModel):
    bot_id: str
    collection: str
    document_id: str
    doc_id: str
    deleted: bool
    chunks_removed: int
    blob_deleted: bool = False


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
