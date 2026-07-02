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

import json
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile

from rag_engine.ingestion.loaders import bytes_to_text, mime_for_path
from rag_engine.models import CollectionSpec

from src.chatbot.api.schemas import (
    DocumentDeleteResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUpsertRequest,
    DocumentUpsertResponse,
)
from src.chatbot.api.security import require_bot_api_key
from src.chatbot.observability.logger import get_logger, truncate

# Every document endpoint is the RAG control plane — gate the whole router on the
# bot's API key (no-op for bots that configure none). See api/security.py.
router = APIRouter(
    prefix="/bots/{bot_id}/documents",
    tags=["documents"],
    dependencies=[Depends(require_bot_api_key)],
)
_log = get_logger("docs")

# Size caps (bytes). Exceeding either → 413 Payload Too Large (see the partner
# API reference). Configurable so ops can tune per deployment.
MAX_UPLOAD_BYTES = int(os.getenv("RAG_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_TEXT_BYTES = int(os.getenv("RAG_MAX_TEXT_BYTES", str(10 * 1024 * 1024)))


def _mb(n: int) -> int:
    return n // (1024 * 1024)


def _download_url(request: Request, bot_id: str, document_id: str) -> str:
    """Absolute URL of the document's content (download) endpoint.

    `document_id` may contain slashes (e.g. 'policies/roaming.md'); the route
    captures it as a `{path}` segment, so keep '/' literal and only escape other
    unsafe characters.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/bots/{bot_id}/documents/{quote(document_id, safe='/')}/content"


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

    if len(req.content.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(413, f"content exceeds the {_mb(MAX_TEXT_BYTES)} MB limit.")

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
    return _upsert_response(request, bot_id, collection, result)


@router.post("/upload", response_model=DocumentUpsertResponse)
async def upload_document(
    bot_id: str,
    request: Request,
    file: UploadFile = File(..., description="The document file (pdf, txt, md, html, json, …)."),
    id: str | None = Form(
        default=None,
        description="Stable document id / source URI. Defaults to the uploaded filename.",
    ),
    metadata: str | None = Form(
        default=None, description="Optional JSON object stored with the document."
    ),
) -> DocumentUpsertResponse:
    """Add or update a document from a raw uploaded file (multipart/form-data).

    The original bytes are stored for download; text is extracted (PDF via pypdf,
    everything else decoded) and indexed through the same pipeline as the JSON
    upsert.
    """
    engine, tenant_id, cfg = _rag_context(request.app.state, bot_id)
    collection = await _ensure_collection(engine, bot_id, cfg)

    document_id = id or file.filename
    if not document_id:
        raise HTTPException(422, "Provide a document 'id' or upload a named file.")
    try:
        meta = json.loads(metadata) if metadata else {}
        if not isinstance(meta, dict):
            raise ValueError("metadata must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(422, f"Invalid metadata: {e}")

    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File exceeds the {_mb(MAX_UPLOAD_BYTES)} MB upload limit."
        )
    mime = mime_for_path(document_id)
    try:
        text = bytes_to_text(raw, mime)
    except Exception as e:
        # Malformed / corrupt file (e.g. a truncated PDF) — surface a clear 422
        # instead of a 500 stack trace. The raw bytes are never stored since we
        # extract before calling the engine.
        raise HTTPException(422, f"Could not extract text from '{file.filename}': {e}")
    if not text.strip():
        raise HTTPException(
            422,
            f"No extractable text in '{file.filename}'. For scanned PDFs/images, "
            "run OCR first or upload the text directly.",
        )

    _log.info(
        "[docs] UPLOAD bot=%s collection=%s id=%s filename=%s bytes=%d mime=%s",
        bot_id, collection, document_id, file.filename, len(raw), mime,
    )
    result = await engine.upsert_document(
        tenant_id=tenant_id,
        collection=collection,
        source_uri=document_id,
        content=text,
        mime_type=mime,
        metadata=meta,
        raw_bytes=raw,
        filename=file.filename,
    )
    return _upsert_response(request, bot_id, collection, result)


def _upsert_response(
    request: Request, bot_id: str, collection: str, result: dict
) -> DocumentUpsertResponse:
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
        content_type=result.get("content_type"),
        size_bytes=result.get("size_bytes"),
        filename=result.get("filename"),
        # Presigned object-store link when available (S3/MinIO); otherwise the
        # API's own /content download proxy (filesystem backend).
        download_url=result.get("download_url")
        or _download_url(request, bot_id, result["source_uri"]),
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
            filename=r.get("filename"),
            content_type=r.get("content_type"),
            size_bytes=r.get("size_bytes"),
            # Presigned object-store link if the backend gave one; else the
            # /content proxy — but only when a blob is actually stored.
            download_url=(
                r.get("download_url")
                or (_download_url(request, bot_id, r["source_uri"]) if r.get("blob_key") else None)
            ),
        )
        for r in rows
    ]
    _log.info("[docs] LIST bot=%s collection=%s count=%d", bot_id, collection, len(docs))
    return DocumentListResponse(
        bot_id=bot_id, collection=collection, count=len(docs), documents=docs
    )


@router.get("/{document_id:path}/content")
async def get_document_content(
    bot_id: str, document_id: str, request: Request
) -> Response:
    """Download a document's original stored artifact (the uploaded file or
    the text that was upserted)."""
    engine, tenant_id, cfg = _rag_context(request.app.state, bot_id)
    collection = cfg.rag.collection
    blob = await engine.get_document_blob(
        tenant_id, collection, source_uri=document_id
    )
    if blob is None:
        raise HTTPException(404, f"No stored file for document '{document_id}'.")
    _log.info(
        "[docs] CONTENT bot=%s collection=%s id=%s bytes=%d",
        bot_id, collection, truncate(document_id, 120), len(blob["data"]),
    )
    return Response(
        content=blob["data"],
        media_type=blob["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{blob["filename"]}"',
        },
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
        blob_deleted=result.get("blob_deleted", False),
    )
