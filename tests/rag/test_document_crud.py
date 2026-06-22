"""RagEngine single-document CRUD: add / update / unchanged / delete / list.

Uses the real MilvusVectorStore (Milvus Lite, tmp_path) + the fake embedder, mirroring
test_engine_facade.py.
"""
from __future__ import annotations

import pytest

from rag_engine import RagEngine
from rag_engine.chunking.auto import AutoChunker
from rag_engine.models import CollectionSpec
from rag_engine.vector_store.milvus_store import MilvusVectorStore


async def _engine(tmp_path, rag_sm, fake_embedder) -> RagEngine:
    engine = RagEngine(
        vector_store=MilvusVectorStore(uri=str(tmp_path / "milvus.db")),
        embedder=fake_embedder,
        chunker=AutoChunker(size=200, overlap=20),
        sessionmaker=rag_sm,
    )
    await engine.start()
    await engine.ensure_collection(
        CollectionSpec(name="kb", tenant_id="t1", embedding_model="fake", dimensions=8)
    )
    return engine


@pytest.mark.asyncio
async def test_add_update_unchanged_delete(tmp_path, rag_sm, fake_embedder):
    engine = await _engine(tmp_path, rag_sm, fake_embedder)
    try:
        # ADD
        r = await engine.upsert_document(
            "t1", "kb", "refund-policy.md",
            "Refund within 7 business days of cancellation.",
        )
        assert r["status"] == "created"
        assert r["counts"]["upserted"] >= 1
        assert not r["errors"]

        # It's searchable + listed
        results = await engine.search("refund timeline", "kb", "t1", top_k=3)
        assert any("Refund" in x.text for x in results)
        docs = await engine.list_documents("t1", "kb")
        assert [d["source_uri"] for d in docs] == ["refund-policy.md"]

        # UNCHANGED (same content → skipped, no re-embed)
        r2 = await engine.upsert_document(
            "t1", "kb", "refund-policy.md",
            "Refund within 7 business days of cancellation.",
        )
        assert r2["status"] == "unchanged"
        assert r2["counts"]["upserted"] == 0
        assert r2["counts"]["skipped"] == 1

        # UPDATE (new content → re-indexed)
        r3 = await engine.upsert_document(
            "t1", "kb", "refund-policy.md",
            "Refund within 14 business days. Roaming refunds are excluded.",
        )
        assert r3["status"] == "updated"
        assert r3["counts"]["upserted"] >= 1
        # Still exactly one document (updated in place, not duplicated)
        docs = await engine.list_documents("t1", "kb")
        assert len(docs) == 1

        # DELETE
        d = await engine.delete_document("t1", "kb", source_uri="refund-policy.md")
        assert d["deleted"] is True
        assert d["chunks_removed"] >= 1
        assert await engine.list_documents("t1", "kb") == []
        # Gone from the index too
        assert await engine.search("refund timeline", "kb", "t1", top_k=3) == []

        # DELETE again → not found
        d2 = await engine.delete_document("t1", "kb", source_uri="refund-policy.md")
        assert d2["deleted"] is False
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_update_shrinking_doc_drops_stale_chunks(tmp_path, rag_sm, fake_embedder):
    """A shorter update must not leave orphaned high-ordinal chunks behind."""
    engine = await _engine(tmp_path, rag_sm, fake_embedder)
    try:
        big = " ".join(f"sentence {i} about data plan." for i in range(60))
        await engine.upsert_document("t1", "kb", "doc1", big)
        before = (await engine.list_documents("t1", "kb"))[0]["chunk_count"]
        assert before > 1

        await engine.upsert_document("t1", "kb", "doc1", "tiny plan note.")
        after = (await engine.list_documents("t1", "kb"))[0]["chunk_count"]
        assert after < before

        # No stale chunks: total vectors for this doc == its new chunk_count.
        hits = await engine.search("plan", "kb", "t1", top_k=50)
        assert len(hits) == after
    finally:
        await engine.stop()
