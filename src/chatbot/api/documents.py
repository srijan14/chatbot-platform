"""Knowledge-base document management API (RAG admin surface).

These endpoints are the *control plane* for a bot's RAG knowledge base: add,
update, list, and remove documents. They are separate from `/chat` (the data
plane the LLM uses) on purpose — only operators/integrations should mutate the
corpus, never the model.

Scope: every endpoint is per-bot. The bot's tenant is its `bot_id` and the
collection is taken from the bot's YAML (`rag.collection`), so a caller can
never address another bot's knowledge base.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from rag_engine.models import CollectionSpec

from src.chatbot.api.schemas import (
    DocumentDeleteResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUpsertRequest,
    DocumentUpsertResponse,
)
from src.chatbot.observability.logger import get_logger, truncate

router = APIRouter(prefix="/bots/{bot_id}/documents", tags=["documents"])
_log = get_logger("docs")


def _rag_context(state, bot_id: str):
    """Resolve (engine, tenant_id, collection) for a RAG-enabled bot.

    Raises 404 if the bot has no knowledge base, 503 if the RAG engine failed
    to initialise at boot.
    """
    try:
        cfg = state.router.get_config(bot_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Unknown bot '{bot_id}'.")
    if "rag" not in cfg.enabled_skills or not cfg.rag.collection:
        raise HTTPException(404, f"Bot '{bot_id}' has no RAG knowledge base.")
    engine = getattr(state, "rag_engine", None)
    if engine is None:
        raise HTTPException(503, "RAG engine is not available.")
    return engine, bot_id, cfg


async def _ensure_collection(engine, bot_id: str, cfg) -> str:
    """Idempotently ensure the bot's collection exists; return its logical name."""
    spec = CollectionSpec(
        name=cfg.rag.collection,
        tenant_id=bot_id,
        embedding_model=cfg.rag.embedding_model,
        dimensions=cfg.rag.dimensions,
        description=f"Knowledge base for bot {bot_id}",
    )
    await engine.ensure_collection(spec)
    return cfg.rag.collection


@router.put("", response_model=DocumentUpsertResponse)
@router.post("", response_model=DocumentUpsertResponse)
async def upsert_document(
    bot_id: str, req: DocumentUpsertRequest, request: Request
) -> DocumentUpsertResponse:
    """Add a new document or update an existing one (idempotent by `id`)."""
    engine, tenant_id, cfg = _rag_context(request.app.state, bot_id)
    collection = await _ensure_collection(engine, bot_id, cfg)

    _log.info(
        "[docs] UPSERT bot=%s collection=%s id=%s chars=%d",
        bot_id, collection, req.id, len(req.content),
    )
    result = await engine.upsert_document(
        tenant_id=tenant_id,
        collection=collection,
        source_uri=req.id,
        content=req.content,
        mime_type=req.mime_type,
        metadata=req.metadata,
    )
    if result["errors"]:
        # Single-doc synchronous API: any per-doc error is a failure.
        raise HTTPException(502, f"Ingestion failed: {'; '.join(result['errors'])}")

    counts = result["counts"]
    return DocumentUpsertResponse(
        bot_id=bot_id,
        collection=collection,
        document_id=result["source_uri"],
        doc_id=result["doc_id"],
        status=result["status"],
        chunks=counts.get("chunks", 0),
        embedded=counts.get("embedded", 0),
        upserted=counts.get("upserted", 0),
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(bot_id: str, request: Request) -> DocumentListResponse:
    """List the documents currently in the bot's knowledge base."""
    engine, tenant_id, cfg = _rag_context(request.app.state, bot_id)
    collection = cfg.rag.collection
    rows = await engine.list_documents(tenant_id, collection)
    docs = [
        DocumentInfo(
            document_id=r["source_uri"],
            doc_id=r["doc_id"],
            chunk_count=r["chunk_count"],
            ingested_at=r["ingested_at"].isoformat() if r.get("ingested_at") else None,
            metadata=r.get("metadata") or {},
        )
        for r in rows
    ]
    _log.info("[docs] LIST bot=%s collection=%s count=%d", bot_id, collection, len(docs))
    return DocumentListResponse(
        bot_id=bot_id, collection=collection, count=len(docs), documents=docs
    )


@router.delete("/{document_id:path}", response_model=DocumentDeleteResponse)
async def delete_document(
    bot_id: str, document_id: str, request: Request
) -> DocumentDeleteResponse:
    """Remove a document (its chunks + bookkeeping) by its `id`."""
    engine, tenant_id, cfg = _rag_context(request.app.state, bot_id)
    collection = cfg.rag.collection
    _log.info(
        "[docs] DELETE bot=%s collection=%s id=%s",
        bot_id, collection, truncate(document_id, 120),
    )
    try:
        result = await engine.delete_document(
            tenant_id, collection, source_uri=document_id
        )
    except KeyError:
        # Collection doesn't exist for this tenant → nothing to delete.
        raise HTTPException(404, f"No knowledge base initialised for bot '{bot_id}'.")
    if not result["deleted"]:
        raise HTTPException(404, f"Document '{document_id}' not found.")
    return DocumentDeleteResponse(
        bot_id=bot_id,
        collection=collection,
        document_id=document_id,
        doc_id=result["doc_id"],
        deleted=result["deleted"],
        chunks_removed=result["chunks_removed"],
    )
