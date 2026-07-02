"""In-process RAG runtime for the chatbot platform.

The platform owns RAG directly: it constructs one `RagEngine` (Milvus + Azure
embeddings + AutoChunker + the rag bookkeeping DB) and keeps it on
`app.state`. Each bot gets its own vector-DB collection — physical name
`{bot_id}__{logical}` — so tenancy is per-bot with zero extra config.

Two entry points, shared by the FastAPI lifespan and the `rag-ingest` CLI so
both build the engine identically:

  * `build_rag_engine()`    — construct the engine + its DB (caller disposes).
  * `bootstrap_bot_rag(...)` — ensure a bot's collection exists and (optionally)
                               ingest its declared sources.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine

from rag_engine import RagEngine
from rag_engine.chunking import AutoChunker, LLMRoutingChunker
from rag_engine.chunking.base import Chunker
from rag_engine.embeddings import AzureOpenAIEmbedder
from rag_engine.models import CollectionSpec, IngestionJob, JobStatus
from rag_engine.storage.blobs import BlobStore, LocalBlobStore
from rag_engine.storage.db import create_engine_and_sessionmaker, init_schema
from rag_engine.vector_store.milvus_store import MilvusVectorStore

from src.chatbot.core.bot_config_store import BotConfig, is_reasoning_deployment
from src.chatbot.core.chunk_planner import make_chunk_planner

log = logging.getLogger("chatbot.rag")

# Best-fit model for the chunk-planner: deciding "markdown vs. recursive + size"
# is a structured classification, not a reasoning task. A fast, capable model
# nails it cheaply, so we default here rather than reusing the bot's main
# (possibly reasoning-class) deployment. Override with
# AZURE_OPENAI_CHUNKING_DEPLOYMENT to match your Azure deployment name.
DEFAULT_CHUNKING_DEPLOYMENT = "gpt-4o-mini"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_chunker() -> Chunker:
    """Pick the document chunker for the engine.

    Default is the mime-dispatching `AutoChunker` — today's behaviour. When
    `RAG_LLM_CHUNKING` is enabled we wrap a single-call LLM planner in
    `LLMRoutingChunker` so chunking adapts to each document's actual structure.
    Building the planner is best-effort: if Azure config is missing or the SDK
    construction fails, we log and fall back to `AutoChunker` so RAG still works.
    """
    if not _truthy(os.getenv("RAG_LLM_CHUNKING")):
        return AutoChunker()

    # Prefer an explicit chunking deployment; otherwise use the best-fit default
    # (NOT the bot's main model, which may be a slow reasoning deployment).
    deployment = (
        os.getenv("AZURE_OPENAI_CHUNKING_DEPLOYMENT")
        or DEFAULT_CHUNKING_DEPLOYMENT
    )

    try:
        planner = make_chunk_planner(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            azure_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            deployment=deployment,
            # Reasoning deployments reject a custom temperature — omit it.
            temperature=None if is_reasoning_deployment(deployment) else 0.0,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "LLM chunk planner unavailable (%s: %s); using AutoChunker.",
            type(exc).__name__, exc,
        )
        return AutoChunker()

    log.info("[rag] LLM-assisted chunking enabled (deployment=%s)", deployment)
    return LLMRoutingChunker(planner)


def _build_blob_store() -> BlobStore:
    """Pick where original document artifacts are stored.

    Default is the filesystem `LocalBlobStore` (zero-infra). Set
    `RAG_BLOB_BACKEND=s3` to persist to MinIO / any S3-compatible store and
    return **presigned** download links. Construction is best-effort: if the S3
    client/config is unusable we log and fall back to the filesystem so RAG never
    fails to start over blob storage.
    """
    backend = (os.getenv("RAG_BLOB_BACKEND") or "local").strip().lower()
    if backend in {"s3", "minio"}:
        try:
            from rag_engine.storage.blobs import S3BlobStore

            store = S3BlobStore(
                endpoint_url=os.getenv("RAG_S3_ENDPOINT", "http://localhost:9000"),
                access_key=os.getenv("RAG_S3_ACCESS_KEY", "minioadmin"),
                secret_key=os.getenv("RAG_S3_SECRET_KEY", "minioadmin"),
                bucket=os.getenv("RAG_S3_BUCKET", "rag-documents"),
                region=os.getenv("RAG_S3_REGION", "us-east-1"),
                public_endpoint=os.getenv("RAG_S3_PUBLIC_ENDPOINT") or None,
                url_expiry=int(os.getenv("RAG_S3_URL_EXPIRY", "3600")),
            )
            log.info("[rag] document blob store: S3/MinIO bucket=%s",
                     os.getenv("RAG_S3_BUCKET", "rag-documents"))
            return store
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "S3 blob store unavailable (%s: %s); using filesystem store.",
                type(exc).__name__, exc,
            )
    return LocalBlobStore(os.getenv("RAG_BLOB_DIR", "data/blobs"))


async def build_rag_engine() -> tuple[RagEngine, AsyncEngine]:
    """Construct the in-process RagEngine + its bookkeeping DB.

    Mirrors what the (now-removed) rag_api lifespan did, so the engine behaves
    identically — just hosted inside the chatbot process. Returns the SQLAlchemy
    engine too so the caller can dispose it on shutdown.
    """
    db_engine, sessionmaker = create_engine_and_sessionmaker()
    await init_schema(db_engine)

    engine = RagEngine(
        vector_store=MilvusVectorStore(),
        embedder=AzureOpenAIEmbedder(),
        chunker=_build_chunker(),
        sessionmaker=sessionmaker,
        # Original uploaded files / text are persisted here so documents can be
        # listed with a download link and fetched back. Filesystem by default;
        # RAG_BLOB_BACKEND=s3 switches to MinIO / S3 with presigned links.
        blob_store=_build_blob_store(),
    )
    return engine, db_engine


def _collection_spec(bot_config: BotConfig) -> CollectionSpec:
    """One collection per bot: tenant_id == bot_id, logical name from YAML."""
    rag = bot_config.rag
    return CollectionSpec(
        name=rag.collection,
        tenant_id=bot_config.bot_id,
        embedding_model=rag.embedding_model,
        dimensions=rag.dimensions,
        description=f"Knowledge base for bot {bot_config.bot_id}",
    )


async def bootstrap_bot_rag(
    engine: RagEngine,
    bot_config: BotConfig,
    *,
    ingest: bool,
    wait: bool = False,
    poll_interval: float = 0.5,
    timeout: float = 120.0,
) -> list[str]:
    """Ensure the bot's collection exists and (optionally) ingest its sources.

    `ingest=False` only guarantees the collection (used when we just need search
    to work). `ingest=True` enqueues one job per declared source. `wait=True`
    polls each job to a terminal state — used by the CLI for a deterministic
    "index then demo" flow; the startup path leaves it False (fire-and-enqueue,
    the background JobRunner drains it).

    Returns the list of enqueued job ids.
    """
    spec = _collection_spec(bot_config)
    await engine.ensure_collection(spec)
    log.info(
        "[rag] collection ready bot=%s collection=%s (physical=%s)",
        bot_config.bot_id, spec.name, spec.physical_name(),
    )

    if not ingest:
        return []

    job_ids: list[str] = []
    for source in bot_config.rag.sources:
        connector = source.get("connector")
        if not connector:
            log.warning("[rag] skipping source without connector: %s", source)
            continue
        job_id = await engine.ingest(
            source_name=connector,
            collection=spec.name,
            tenant_id=bot_config.bot_id,
            source_config=source.get("config") or {},
            metadata=source.get("metadata") or {},
        )
        job_ids.append(job_id)
        log.info(
            "[rag] ingest enqueued bot=%s connector=%s job=%s",
            bot_config.bot_id, connector, job_id,
        )

    if wait:
        for job_id in job_ids:
            job = await _wait_for_job(engine, job_id, poll_interval, timeout)
            log.info(
                "[rag] ingest finished job=%s status=%s counts=%s",
                job_id, job.status.value if job else "<missing>",
                job.counts if job else {},
            )
    return job_ids


async def _wait_for_job(
    engine: RagEngine, job_id: str, poll_interval: float, timeout: float
) -> IngestionJob | None:
    elapsed = 0.0
    terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED}
    while elapsed < timeout:
        job = await engine.job_status(job_id)
        if job is not None and job.status in terminal:
            return job
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return await engine.job_status(job_id)
