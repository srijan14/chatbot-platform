"""rag_api end-to-end through TestClient — collections → ingest → poll → search.

Stubs out Azure embeddings via the same FakeEmbedder used in unit tests;
points the lifespan at a tmp-dir Chroma and an in-memory SQLite.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_fake_engine(tmp_path, fake_embedder, monkeypatch):
    """Build a rag_api app whose lifespan uses a FakeEmbedder + tmp Chroma."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://stub.example/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "stub")
    monkeypatch.setenv("RAG_DB_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("RAG_CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_UPLOAD_DIR", str(tmp_path / "uploads"))
    # Make absolutely sure the bootstrap yamls aren't accidentally picked up.
    monkeypatch.setenv("RAG_COLLECTIONS_YAML", str(tmp_path / "no-collections.yaml"))
    monkeypatch.setenv("RAG_SOURCES_YAML", str(tmp_path / "no-sources.yaml"))

    from fastapi import FastAPI

    from rag_api.routes import collections, health, ingest, jobs, search
    from rag_engine import RagEngine
    from rag_engine.chunking import RecursiveCharChunker
    from rag_engine.storage.db import create_engine_and_sessionmaker, init_schema
    from rag_engine.vector_store.chroma_store import ChromaVectorStore

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_engine, sm = create_engine_and_sessionmaker()
        await init_schema(db_engine)
        engine = RagEngine(
            vector_store=ChromaVectorStore(persist_path=str(tmp_path / "chroma")),
            embedder=fake_embedder,
            chunker=RecursiveCharChunker(size=400, overlap=50),
            sessionmaker=sm,
        )
        await engine.start()
        app.state.engine = engine
        app.state.db_engine = db_engine
        try:
            yield
        finally:
            await engine.stop()
            await db_engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(collections.router)
    app.include_router(ingest.router)
    app.include_router(jobs.router)
    app.include_router(search.router)
    return app


def test_ingest_then_search_via_http(tmp_path, app_with_fake_engine):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "cancel.md").write_text(
        "Postpaid customers may cancel; refunds within 7 days."
    )
    (corpus / "roaming.md").write_text(
        "Roaming international data is capped at 500 MB per day."
    )

    headers = {"X-Tenant-Id": "t1"}
    with TestClient(app_with_fake_engine) as c:
        # Create collection
        r = c.post("/collections", headers=headers, json={
            "name": "kb", "embedding_model": "fake", "dimensions": 8,
        })
        assert r.status_code == 200, r.text

        # Enqueue ingestion
        r = c.post("/ingest", headers=headers, json={
            "collection": "kb",
            "source": "file_path",
            "source_config": {"path": str(corpus), "glob": "**/*.md"},
        })
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        # Poll
        for _ in range(50):
            r = c.get(f"/jobs/{job_id}", headers=headers)
            assert r.status_code == 200
            status = r.json()["status"]
            if status in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        body = r.json()
        assert body["status"] == "succeeded", body
        assert body["counts"]["documents"] == 2

        # Cross-tenant can't see the job
        r = c.get(f"/jobs/{job_id}", headers={"X-Tenant-Id": "other"})
        assert r.status_code == 404

        # Search
        r = c.post("/search", headers=headers, json={
            "query": "refund timing",
            "collection": "kb",
        })
        assert r.status_code == 200, r.text
        hits = r.json()["results"]
        assert hits
        assert all(h["metadata"]["tenant_id"] == "t1" for h in hits)


def test_missing_tenant_400(app_with_fake_engine):
    with TestClient(app_with_fake_engine) as c:
        r = c.get("/collections")
        assert r.status_code == 400
