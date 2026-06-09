"""Mime-dispatching chunker.

Picks the right strategy per document so callers can hand the engine a single
`Chunker` and still get format-aware splitting:

  - `text/markdown` -> `MarkdownHeaderChunker` (chunks align to `#`/`##`
    sections, and each chunk carries `metadata["heading"]` for nicer citations).
  - everything else  -> `RecursiveCharChunker` (format-neutral character budget).

This is what the chatbot's in-process RAG engine wires in by default. Without
it, markdown would be chunked with the recursive splitter and never populate the
`heading` metadata that `RagSkill` renders in citations.
"""
from __future__ import annotations

from rag_engine.chunking.base import Chunker
from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.chunking.structural import MarkdownHeaderChunker
from rag_engine.models import Chunk, Document

_MARKDOWN_MIMES = {"text/markdown"}


class AutoChunker(Chunker):
    def __init__(self, size: int = 800, overlap: int = 120):
        self._recursive = RecursiveCharChunker(size=size, overlap=overlap)
        self._markdown = MarkdownHeaderChunker(max_size=size, overlap=overlap)

    def _for(self, mime_type: str) -> Chunker:
        if mime_type in _MARKDOWN_MIMES:
            return self._markdown
        return self._recursive

    async def chunk(self, doc: Document) -> list[Chunk]:
        return await self._for(doc.mime_type).chunk(doc)
