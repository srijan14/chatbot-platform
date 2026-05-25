"""Heading-aware splitter for Markdown.

Splits on `#`/`##`/`###` headings so each chunk corresponds to a section the
author already considered self-contained. Oversized sections are passed to a
fallback chunker (`RecursiveCharChunker` by default). The heading path is
stashed under `metadata.heading` for nicer citations.
"""
from __future__ import annotations

import re

from rag_engine.chunking.base import Chunker
from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.models import Chunk, Document

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Return [(heading_path, section_text)] preserving document order.

    Content before the first heading goes under heading "" (empty).
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]

    sections: list[tuple[str, str]] = []
    stack: list[str] = []   # heading-path stack indexed by level (1-based)

    # Preamble (before first heading)
    pre = text[: matches[0].start()].strip()
    if pre:
        sections.append(("", pre))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        # Maintain stack
        while len(stack) >= level:
            stack.pop()
        while len(stack) < level - 1:
            stack.append("")   # filler for skipped levels (rare, but stable)
        stack.append(title)
        path = " > ".join(s for s in stack if s)
        sections.append((path, body))
    return sections


class MarkdownHeaderChunker(Chunker):
    def __init__(self, max_size: int = 800, overlap: int = 120):
        self._fallback = RecursiveCharChunker(size=max_size, overlap=overlap)
        self.max_size = max_size

    async def chunk(self, doc: Document) -> list[Chunk]:
        out: list[Chunk] = []
        sections = _split_by_headings(doc.content)
        ord_ = 0
        for path, body in sections:
            if not body:
                continue
            if len(body) <= self.max_size:
                out.append(
                    Chunk(
                        chunk_id=f"{doc.doc_id}:{ord_:04d}",
                        doc_id=doc.doc_id,
                        text=body,
                        ordinal=ord_,
                        metadata={
                            "tenant_id": doc.tenant_id,
                            "doc_id": doc.doc_id,
                            "source_uri": doc.source_uri,
                            "ordinal": ord_,
                            "mime_type": doc.mime_type,
                            "heading": path,
                            **doc.metadata,
                        },
                    )
                )
                ord_ += 1
            else:
                # Oversized section: hand to recursive chunker, then carry the
                # heading down into each produced sub-chunk.
                synthetic = Document(
                    doc_id=doc.doc_id,
                    source_uri=doc.source_uri,
                    content=body,
                    mime_type=doc.mime_type,
                    tenant_id=doc.tenant_id,
                    collection=doc.collection,
                    metadata={**doc.metadata, "heading": path},
                )
                sub = await self._fallback.chunk(synthetic)
                for s in sub:
                    s.chunk_id = f"{doc.doc_id}:{ord_:04d}"
                    s.ordinal = ord_
                    s.metadata["ordinal"] = ord_
                    s.metadata["heading"] = path
                    out.append(s)
                    ord_ += 1
        return out
