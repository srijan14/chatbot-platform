"""Content-aware chunker: ask a `ChunkPlanner` how to split, then delegate.

Where `AutoChunker` routes purely on mime type, this routes on what a planner
(typically a single LLM call) decides after looking at a *sample* of the actual
content — useful when "everything that isn't markdown" hides FAQs, transcripts,
HTML, and PDF-extracted prose that each want different chunk sizes.

It is a drop-in `Chunker`: same `chunk(doc) -> list[Chunk]` contract, same chunk
shapes (it reuses `MarkdownHeaderChunker` / `RecursiveCharChunker`). Two
guarantees keep it safe to enable in production:

  * **Cheap heuristic gate** — markdown and short docs never reach the planner
    (their best strategy is already obvious), so we don't pay a model call for
    the common case.
  * **Fail-safe fallback** — any planner error, or an unknown strategy, falls
    back to `AutoChunker`. Indexing never fails because the planner is down.
"""
from __future__ import annotations

import logging

from rag_engine.chunking.auto import AutoChunker
from rag_engine.chunking.base import Chunker
from rag_engine.chunking.planner import (
    KNOWN_STRATEGIES,
    MARKDOWN,
    ChunkPlan,
    ChunkPlanner,
)
from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.chunking.structural import MarkdownHeaderChunker
from rag_engine.models import Chunk, Document

log = logging.getLogger("rag_engine.chunking")

_MARKDOWN_MIMES = {"text/markdown"}


class LLMRoutingChunker(Chunker):
    def __init__(
        self,
        planner: ChunkPlanner,
        *,
        default_size: int = 800,
        default_overlap: int = 120,
        sample_chars: int = 4000,
        min_chars: int = 1500,
    ):
        self._planner = planner
        self._sample_chars = sample_chars
        self._min_chars = min_chars
        # Fallback for the heuristic gate and for any planner failure. Reusing
        # AutoChunker keeps "no planner" behaviour identical to today's default.
        self._auto = AutoChunker(size=default_size, overlap=default_overlap)

    def _skip_planner(self, doc: Document) -> bool:
        """Obvious cases where the planner adds nothing: markdown already gets
        heading-aware chunking, and tiny docs barely fragment at all."""
        return doc.mime_type in _MARKDOWN_MIMES or len(doc.content) <= self._min_chars

    def _build_chunker(self, plan: ChunkPlan) -> Chunker:
        if plan.strategy == MARKDOWN:
            return MarkdownHeaderChunker(max_size=plan.size, overlap=plan.overlap)
        return RecursiveCharChunker(size=plan.size, overlap=plan.overlap)

    async def chunk(self, doc: Document) -> list[Chunk]:
        if self._skip_planner(doc):
            return await self._auto.chunk(doc)

        try:
            plan = await self._planner.plan(
                sample=doc.content[: self._sample_chars],
                mime_type=doc.mime_type,
                metadata=doc.metadata,
            )
            if plan.strategy not in KNOWN_STRATEGIES:
                raise ValueError(f"unknown strategy {plan.strategy!r}")
            chunker = self._build_chunker(plan)
        except Exception as e:
            log.warning(
                "chunk planner failed for %s (%s: %s); falling back to AutoChunker",
                doc.source_uri, type(e).__name__, e,
            )
            return await self._auto.chunk(doc)

        log.info(
            "[chunk-plan] doc=%s strategy=%s size=%d overlap=%d reason=%s",
            doc.source_uri, plan.strategy, plan.size, plan.overlap, plan.reason,
        )
        # Record the chosen strategy on the doc so it propagates into every
        # chunk's metadata (the concrete chunkers spread doc.metadata) — handy
        # for debugging retrieval quality per strategy.
        doc.metadata.setdefault("chunk_strategy", plan.strategy)
        return await chunker.chunk(doc)
