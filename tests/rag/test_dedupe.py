"""Dedupe: same content_hash -> skip; changed -> re-index."""
from __future__ import annotations

import pytest

from rag_engine.ingestion.dedupe import decide
from rag_engine.jobs.store import DocumentsRepo
from rag_engine.models import Document, content_hash, doc_id_for


@pytest.mark.asyncio
async def test_new_doc_is_marked_new(rag_sm):
    docs = DocumentsRepo(rag_sm)
    doc = Document(
        doc_id=doc_id_for("u://1"), source_uri="u://1", content="hi",
        mime_type="text/plain", tenant_id="t", collection="c",
    )
    async with (await docs.session()) as s:
        d = await decide(s, doc)
    assert d.is_new is True
    assert d.is_changed is False
    assert d.should_index is True
    assert d.hash == content_hash("hi")


@pytest.mark.asyncio
async def test_unchanged_doc_should_not_reindex(rag_sm):
    docs = DocumentsRepo(rag_sm)
    doc = Document(
        doc_id=doc_id_for("u://1"), source_uri="u://1", content="same body",
        mime_type="text/plain", tenant_id="t", collection="c",
    )
    await docs.upsert(
        doc_id=doc.doc_id, tenant_id="t", collection="c",
        source_uri=doc.source_uri, text=doc.content, chunk_count=1,
    )
    async with (await docs.session()) as s:
        d = await decide(s, doc)
    assert d.is_new is False
    assert d.is_changed is False
    assert d.should_index is False


@pytest.mark.asyncio
async def test_changed_doc_should_reindex(rag_sm):
    docs = DocumentsRepo(rag_sm)
    doc = Document(
        doc_id=doc_id_for("u://1"), source_uri="u://1", content="first body",
        mime_type="text/plain", tenant_id="t", collection="c",
    )
    await docs.upsert(
        doc_id=doc.doc_id, tenant_id="t", collection="c",
        source_uri=doc.source_uri, text=doc.content, chunk_count=1,
    )
    doc.content = "second body"
    async with (await docs.session()) as s:
        d = await decide(s, doc)
    assert d.is_new is False
    assert d.is_changed is True
    assert d.should_index is True
