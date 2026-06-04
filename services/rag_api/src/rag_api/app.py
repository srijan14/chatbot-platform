"""RAG control plane — FastAPI on :8002.

Endpoints:
  POST   /collections                       create
  GET    /collections                       list (tenant-scoped)
  DELETE /collections/{name}                drop
  POST   /ingest                            enqueue ingestion job
  POST   /ingest/upload                     multipart variant
  GET    /jobs/{id}                         poll status
  POST   /search                            admin/debug retrieval
  GET    /health
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402

from rag_api.routes import collections, health, ingest, jobs, search  # noqa: E402
from rag_engine import RagEngine  # noqa: E402
from rag_engine.chunking import AutoChunker  # noqa: E402
from rag_engine.config import load_collections_yaml, load_sources_yaml  # noqa: E402
from rag_engine.embeddings import AzureOpenAIEmbedder  # noqa: E402
from rag_engine.storage.db import create_engine_and_sessionmaker, init_schema  # noqa: E402
from rag_engine.vector_store.chroma_store import ChromaVectorStore  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_engine, sessionmaker = create_engine_and_sessionmaker()
    await init_schema(db_engine)

    vstore = ChromaVectorStore()
    embedder = AzureOpenAIEmbedder()
    # AutoChunker dispatches on mime type: markdown -> heading-aware chunks
    # (populates metadata["heading"] for citations), everything else ->
    # recursive character budget.
    chunker = AutoChunker()

    engine = RagEngine(
        vector_store=vstore,
        embedder=embedder,
        chunker=chunker,
        sessionmaker=sessionmaker,
    )
    await engine.start()

    # Idempotent bootstrap from declarative YAMLs. Both files are optional —
    # operators that don't want config-as-code can use the REST API instead.
    collections_yaml = os.getenv("RAG_COLLECTIONS_YAML", "configs/rag/collections.yaml")
    sources_yaml = os.getenv("RAG_SOURCES_YAML", "configs/rag/sources.yaml")
    cfg = load_collections_yaml(collections_yaml)
    if cfg.specs:
        await engine.bootstrap_collections(cfg.specs)
    sources = load_sources_yaml(sources_yaml)
    if sources:
        engine.attach_scheduler(sources)

    app.state.db_engine = db_engine
    app.state.engine = engine

    try:
        yield
    finally:
        await engine.stop()
        await db_engine.dispose()


app = FastAPI(
    title="RAG Control Plane",
    version="0.1.0",
    description="Ingestion, jobs, collections and admin search for the RAG sub-platform.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(collections.router)
app.include_router(ingest.router)
app.include_router(jobs.router)
app.include_router(search.router)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "rag_api.app:app",
        host=os.getenv("RAG_API_HOST", "127.0.0.1"),
        port=int(os.getenv("RAG_API_PORT", "8002")),
        reload=os.getenv("RAG_API_RELOAD", "0") == "1",
    )
