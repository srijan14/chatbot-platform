"""Chunker unit tests."""
from __future__ import annotations

import pytest

from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.chunking.structural import MarkdownHeaderChunker
from rag_engine.models import Document, doc_id_for


def _doc(text: str, mime: str = "text/plain") -> Document:
    return Document(
        doc_id=doc_id_for("test://doc"),
        source_uri="test://doc",
        content=text,
        mime_type=mime,
        tenant_id="t1",
        collection="c1",
        metadata={"author": "alice"},
    )


@pytest.mark.asyncio
async def test_recursive_short_text_returns_single_chunk():
    chunker = RecursiveCharChunker(size=100, overlap=20)
    chunks = await chunker.chunk(_doc("hello world"))
    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].metadata["tenant_id"] == "t1"
    assert chunks[0].metadata["source_uri"] == "test://doc"
    assert chunks[0].metadata["author"] == "alice"  # original metadata preserved
    assert chunks[0].ordinal == 0


@pytest.mark.asyncio
async def test_recursive_paragraph_split():
    chunker = RecursiveCharChunker(size=60, overlap=10)
    text = "para one is short.\n\npara two has more words to push it over.\n\npara three."
    chunks = await chunker.chunk(_doc(text))
    # Each chunk under budget
    for c in chunks:
        assert len(c.text) <= 60
    # Chunk ids are deterministic and ordered
    ids = [c.chunk_id for c in chunks]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_recursive_char_floor_no_separator():
    chunker = RecursiveCharChunker(size=10, overlap=3)
    text = "abcdefghijklmnopqrstuvwxyz"
    chunks = await chunker.chunk(_doc(text))
    assert len(chunks) > 1
    # Overlap should produce some shared characters between adjacent chunks
    assert chunks[0].text[-3:] == chunks[1].text[:3] or chunks[0].text[-1] == chunks[1].text[0]


@pytest.mark.asyncio
async def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        RecursiveCharChunker(size=10, overlap=10)


@pytest.mark.asyncio
async def test_markdown_header_chunker_splits_on_h1_h2():
    md = """# Intro
some intro text.

## Section A
content of A.

## Section B
content of B which is a bit longer to occupy a chunk on its own.
"""
    chunks = await MarkdownHeaderChunker(max_size=500).chunk(_doc(md, mime="text/markdown"))
    headings = [c.metadata.get("heading") for c in chunks]
    assert "Intro" in headings
    assert "Intro > Section A" in headings
    assert "Intro > Section B" in headings


@pytest.mark.asyncio
async def test_markdown_header_chunker_falls_back_on_oversize_section():
    big = "x" * 2000
    md = f"# H1\nsmall.\n\n## H2 big\n{big}\n"
    chunks = await MarkdownHeaderChunker(max_size=400, overlap=50).chunk(
        _doc(md, mime="text/markdown")
    )
    # The "H1 > H2 big" section should be split into multiple chunks
    sub = [c for c in chunks if c.metadata.get("heading") == "H1 > H2 big"]
    assert len(sub) > 1
    assert all(len(c.text) <= 400 for c in sub)
