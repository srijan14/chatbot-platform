"""Character-budget chunker with overlap.

Splits text by trying separators in order (paragraph → line → sentence → word
→ char) and stops at the first that yields pieces small enough. Keeps an
`overlap` carry-over between adjacent chunks so a question whose answer spans
a chunk boundary is still retrievable.

This is the format-neutral default. `MarkdownHeaderChunker` runs first for
.md so chunks align with semantic boundaries; the recursive chunker is then
used to keep any oversized section under budget.
"""
from __future__ import annotations

from rag_engine.chunking.base import Chunker
from rag_engine.models import Chunk, Document

DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _split(text: str, size: int, overlap: int, separators: list[str]) -> list[str]:
    if len(text) <= size:
        return [text]

    # Pick the coarsest separator that actually fragments the text into
    # pieces all <= size. The empty-string sentinel ("") is the char-level
    # floor and we must NOT pass it to str.split (it raises ValueError);
    # exclude it from the candidate generator and fall through to "" only as
    # the default.
    sep = next(
        (
            s for s in separators
            if s != "" and s in text
            and all(len(p) <= size for p in text.split(s) if p)
        ),
        "",
    )

    if sep == "":
        # Hard char-level split with overlap; guaranteed to terminate.
        out: list[str] = []
        step = max(1, size - overlap)
        for i in range(0, len(text), step):
            out.append(text[i : i + size])
        return out

    pieces = [p for p in text.split(sep) if p]
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = (buf + sep + piece) if buf else piece
        if len(candidate) <= size:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            # Carry the tail of the previous chunk as overlap.
            buf = (buf[-overlap:] + sep + piece) if overlap and len(buf) > overlap else piece
        else:
            # Single piece bigger than size — recurse with finer separators.
            chunks.extend(_split(piece, size, overlap, separators[separators.index(sep) + 1 :]))
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


class RecursiveCharChunker(Chunker):
    def __init__(self, size: int = 800, overlap: int = 120,
                 separators: list[str] | None = None):
        if overlap >= size:
            raise ValueError("overlap must be smaller than size")
        self.size = size
        self.overlap = overlap
        self.separators = separators or DEFAULT_SEPARATORS

    async def chunk(self, doc: Document) -> list[Chunk]:
        pieces = _split(doc.content, self.size, self.overlap, self.separators)
        chunks: list[Chunk] = []
        for ord_, text in enumerate(pieces):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}:{ord_:04d}",
                    doc_id=doc.doc_id,
                    text=text,
                    ordinal=ord_,
                    # Mandatory keys callers and retrievers rely on:
                    metadata={
                        "tenant_id": doc.tenant_id,
                        "doc_id": doc.doc_id,
                        "source_uri": doc.source_uri,
                        "ordinal": ord_,
                        "mime_type": doc.mime_type,
                        **doc.metadata,
                    },
                )
            )
        return chunks
