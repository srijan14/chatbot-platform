"""Dedicated Azure OpenAI call that decides how to chunk one document.

Separate from the bot LLM (same rationale as `tag_engine/summarizer.py`):
  • it can run on a smaller/cheaper deployment (env override)
  • its system prompt is tuned for one job — picking a chunking strategy — and
    never pollutes the bot's persona

This is the concrete `ChunkPlanner` the provider-neutral `LLMRoutingChunker`
(in `rag_engine`) depends on. It looks at a sample of the document and returns a
`ChunkPlan`; the router maps that onto the existing chunkers.
"""
from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from rag_engine.chunking.planner import ChunkPlan

SYSTEM_PROMPT = """You decide how to split ONE document for a retrieval (RAG) index.

You are given a sample from the start of the document plus its MIME type. Pick
the chunking strategy that keeps semantically-related text together so a search
hit returns a self-contained passage.

Strategies:
  - "markdown": the text is organised under #/##/### headings (or clearly
    section-titled). Split on those headings so each chunk is one section.
  - "recursive": free-flowing prose, HTML, transcripts, FAQs, or PDF-extracted
    text with no reliable heading structure. Split on paragraph/sentence
    boundaries within a character budget.

Parameters:
  - size: target characters per chunk. Use SMALLER (~400-600) for dense,
    fact-dump or Q&A content where answers are short and local; use LARGER
    (~900-1200) for narrative prose where context spans several sentences.
  - overlap: characters carried between adjacent chunks (~10-20% of size) so an
    answer straddling a boundary stays retrievable.

Return only the structured decision, with a one-line reason."""


class _PlanOut(BaseModel):
    """Structured planner output. Bounds keep a hallucinated value from
    producing a pathological chunker config."""
    strategy: Literal["markdown", "recursive"] = Field(
        ..., description="Chunking strategy to use."
    )
    size: int = Field(800, ge=200, le=2000, description="Target chars per chunk.")
    overlap: int = Field(120, ge=0, le=400, description="Overlap chars between chunks.")
    reason: str = Field("", description="One-line justification (for logging).")


class AzureChunkPlanner:
    """`ChunkPlanner` backed by a structured Azure OpenAI call."""

    def __init__(self, llm: AzureChatOpenAI):
        # with_structured_output binds the schema as a tool/json-schema so we get
        # a parsed _PlanOut back instead of free text to parse ourselves.
        self._llm = llm.with_structured_output(_PlanOut)

    async def plan(
        self, *, sample: str, mime_type: str, metadata: dict
    ) -> ChunkPlan:
        user_payload = (
            f"MIME type: {mime_type}\n"
            f"Metadata: {metadata or '{}'}\n\n"
            f"Document sample (first chars):\n{sample}\n\n"
            f"Decide the chunking strategy and parameters."
        )
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_payload),
        ]
        out: _PlanOut = await self._llm.ainvoke(messages)
        return ChunkPlan(
            strategy=out.strategy,
            size=out.size,
            overlap=out.overlap,
            reason=out.reason,
        )


def make_chunk_planner(
    *,
    azure_endpoint: str,
    azure_api_key: str,
    azure_api_version: str,
    deployment: str,
    max_tokens: int = 512,
    temperature: float | None = 0.0,
) -> AzureChunkPlanner:
    """Build the dedicated chunk-planner LLM.

    The decision payload is tiny, but `max_tokens` is generous (512) so the
    structured output is never truncated — and so a reasoning-class deployment
    (which spends tokens on hidden reasoning before emitting the JSON) still has
    headroom. Cheap for non-reasoning models, which stop as soon as the small
    tool call is complete.

    Pass `temperature=None` to omit the kwarg entirely — required when the
    deployment is a reasoning-class model (o-series, gpt-5+) which rejects any
    non-default temperature. (Same handling as `make_summarizer`.)
    """
    kwargs: dict = {
        "azure_endpoint": azure_endpoint,
        "api_key": azure_api_key,
        "api_version": azure_api_version,
        "azure_deployment": deployment,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return AzureChunkPlanner(AzureChatOpenAI(**kwargs))
