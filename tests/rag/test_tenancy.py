"""Tenant isolation — a query from tenant A must never return tenant B's vectors.

This is the security gate. If anyone removes the metadata filter in
`Retriever.search`, this test fires.
"""
from __future__ import annotations

import pytest

from rag_engine.models import CollectionSpec, Document, doc_id_for
from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.retrieval.retriever import Retriever
from rag_engine.tenancy.resolver import physical_collection_name, validate_identifier
from rag_engine.vector_store.chroma_store import ChromaVectorStore
from rag_engine.vector_store.base import UpsertItem


@pytest.mark.asyncio
async def test_retriever_filters_by_tenant(tmp_path, fake_embedder):
    vstore = ChromaVectorStore(persist_path=str(tmp_path / "chroma"))
    chunker = RecursiveCharChunker(size=300, overlap=20)

    spec_a = CollectionSpec(
        name="kb", tenant_id="alpha", embedding_model="fake", dimensions=8,
    )
    spec_b = CollectionSpec(
        name="kb", tenant_id="beta", embedding_model="fake", dimensions=8,
    )
    await vstore.create_collection(spec_a.physical_name(), dimensions=8)
    await vstore.create_collection(spec_b.physical_name(), dimensions=8)

    # Insert ONE doc per tenant; both share the same logical name "kb" so the
    # only thing keeping them apart is the physical naming + metadata filter.
    for spec, content in [(spec_a, "alpha secret about refund timing"),
                          (spec_b, "beta secret about refund timing")]:
        doc = Document(
            doc_id=doc_id_for(f"{spec.tenant_id}://only"),
            source_uri=f"{spec.tenant_id}://only",
            content=content,
            mime_type="text/plain",
            tenant_id=spec.tenant_id,
            collection=spec.name,
        )
        chunks = await chunker.chunk(doc)
        embs = await fake_embedder.embed_documents([c.text for c in chunks])
        items = [
            UpsertItem(id=c.chunk_id, embedding=e, document=c.text, metadata=c.metadata)
            for c, e in zip(chunks, embs)
        ]
        await vstore.upsert(spec.physical_name(), items)

    retriever = Retriever(vstore, fake_embedder)

    hits_a = await retriever.search("refund", spec_a, top_k=5)
    hits_b = await retriever.search("refund", spec_b, top_k=5)

    assert hits_a, "expected alpha to find its own doc"
    assert hits_b, "expected beta to find its own doc"
    assert all("alpha" in h.text for h in hits_a)
    assert all("beta" in h.text for h in hits_b)


@pytest.mark.asyncio
async def test_caller_cannot_override_tenant_id_via_filters(tmp_path, fake_embedder):
    """If a caller passes filters={"tenant_id":"alpha"} while querying as beta,
    the beta scoping must win — never the caller's value."""
    vstore = ChromaVectorStore(persist_path=str(tmp_path / "chroma"))
    spec_a = CollectionSpec(name="kb", tenant_id="alpha", embedding_model="fake", dimensions=8)
    spec_b = CollectionSpec(name="kb", tenant_id="beta", embedding_model="fake", dimensions=8)
    await vstore.create_collection(spec_a.physical_name(), dimensions=8)
    await vstore.create_collection(spec_b.physical_name(), dimensions=8)

    doc = Document(
        doc_id=doc_id_for("alpha://x"),
        source_uri="alpha://x",
        content="alpha secret content",
        mime_type="text/plain",
        tenant_id="alpha",
        collection="kb",
    )
    chunks = await RecursiveCharChunker(size=200, overlap=10).chunk(doc)
    embs = await fake_embedder.embed_documents([c.text for c in chunks])
    await vstore.upsert(
        spec_a.physical_name(),
        [UpsertItem(id=c.chunk_id, embedding=e, document=c.text, metadata=c.metadata)
         for c, e in zip(chunks, embs)],
    )

    retriever = Retriever(vstore, fake_embedder)
    hits = await retriever.search(
        "content", spec_b, top_k=5, filters={"tenant_id": "alpha"}
    )
    # beta's collection is empty and the override is ignored — no hits.
    assert hits == []


def test_physical_name_rejects_unsafe_identifiers():
    with pytest.raises(ValueError):
        physical_collection_name("foo$bar", "ok")
    with pytest.raises(ValueError):
        physical_collection_name("ok", "../escape")
    assert physical_collection_name("alpha_1", "kb-main") == "alpha_1__kb-main"


def test_identifier_validation_basics():
    validate_identifier("abc", "x")
    validate_identifier("a_b-c-1", "x")
    with pytest.raises(ValueError):
        validate_identifier("", "x")
    with pytest.raises(ValueError):
        validate_identifier("a" * 200, "x")
