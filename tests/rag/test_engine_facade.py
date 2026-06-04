"""End-to-end smoke through RagEngine with the file_loader connector and a
fake embedder, against a real Chroma persistent client in tmp_path."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_engine import RagEngine
from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.models import CollectionSpec, JobStatus
from rag_engine.vector_store.chroma_store import ChromaVectorStore


@pytest.mark.asyncio
async def test_ingest_then_search(tmp_path, rag_sm, fake_embedder):
    # Set up a tiny corpus
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "cancel.md").write_text(
        "Cancellation applies at end of cycle. Refund within 7 business days."
    )
    (corpus / "roaming.md").write_text(
        "Roaming international data is capped at 500 MB per day."
    )

    engine = RagEngine(
        vector_store=ChromaVectorStore(persist_path=str(tmp_path / "chroma")),
        embedder=fake_embedder,
        chunker=RecursiveCharChunker(size=200, overlap=20),
        sessionmaker=rag_sm,
    )
    await engine.start()

    spec = await engine.ensure_collection(
        CollectionSpec(
            name="kb", tenant_id="t1", embedding_model="fake", dimensions=8,
        )
    )
    assert spec.physical_name() == "t1__kb"

    job_id = await engine.ingest(
        source_name="file_loader",
        collection="kb",
        tenant_id="t1",
        source_config={"path": str(corpus), "glob": "**/*.md"},
    )

    # Wait for the worker to drain (bounded — fail fast if it never does)
    for _ in range(50):
        job = await engine.job_status(job_id)
        if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            break
        await asyncio.sleep(0.05)
    assert job.status == JobStatus.SUCCEEDED, f"job failed: {job.errors}"
    assert job.counts["documents"] == 2
    assert job.counts["upserted"] >= 2

    results = await engine.search("refund timeline", "kb", "t1", top_k=3)
    assert results
    assert any("Refund" in r.text or "Cancellation" in r.text for r in results)
    assert all(r.metadata.get("tenant_id") == "t1" for r in results)

    # Re-ingestion should be idempotent: docs counted, but chunks skipped.
    job2_id = await engine.ingest(
        source_name="file_loader", collection="kb", tenant_id="t1",
        source_config={"path": str(corpus), "glob": "**/*.md"},
    )
    for _ in range(50):
        job2 = await engine.job_status(job2_id)
        if job2.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            break
        await asyncio.sleep(0.05)
    assert job2.status == JobStatus.SUCCEEDED
    assert job2.counts["skipped"] == 2
    assert job2.counts["upserted"] == 0

    await engine.stop()


@pytest.mark.asyncio
async def test_ingest_unknown_collection_raises(tmp_path, rag_sm, fake_embedder):
    engine = RagEngine(
        vector_store=ChromaVectorStore(persist_path=str(tmp_path / "chroma")),
        embedder=fake_embedder,
        chunker=RecursiveCharChunker(),
        sessionmaker=rag_sm,
    )
    await engine.start()
    try:
        with pytest.raises(KeyError):
            await engine.ingest(
                source_name="file_loader", collection="missing", tenant_id="t1",
                source_config={"path": str(tmp_path)},
            )
    finally:
        await engine.stop()
