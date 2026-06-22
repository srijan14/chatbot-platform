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

from sqlalchemy.ext.asyncio import AsyncEngine

from rag_engine import RagEngine
from rag_engine.chunking import AutoChunker
from rag_engine.embeddings import AzureOpenAIEmbedder
from rag_engine.models import CollectionSpec, IngestionJob, JobStatus
from rag_engine.storage.db import create_engine_and_sessionmaker, init_schema
from rag_engine.vector_store.milvus_store import MilvusVectorStore

from src.chatbot.core.bot_config_store import BotConfig

log = logging.getLogger("chatbot.rag")


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
        chunker=AutoChunker(),
        sessionmaker=sessionmaker,
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
