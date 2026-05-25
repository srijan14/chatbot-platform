"""Ingestion entry points.

Two flavors:
  POST /ingest         — JSON body; source_config drives the connector.
  POST /ingest/upload  — multipart upload; stores file under RAG_UPLOAD_DIR
                          and dispatches via the file_loader connector.
Both return a job_id immediately; the worker drains the queue in the
background. Clients poll /jobs/{id}.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from rag_api.deps import get_engine, get_tenant
from rag_api.schemas import IngestRequest, IngestResponse
from rag_engine import RagEngine

router = APIRouter(prefix="/ingest", tags=["ingestion"])


def _resolve_connector_name(source: str) -> str:
    """REST `source` enum -> registered connector name.

    Both `upload` and `file_path` are served by the file_loader connector —
    they only differ in where the file came from.
    """
    if source in ("upload", "file_path"):
        return "file_loader"
    return source


@router.post("", response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> IngestResponse:
    try:
        job_id = await engine.ingest(
            source_name=_resolve_connector_name(body.source),
            collection=body.collection,
            tenant_id=tenant_id,
            source_config=body.source_config,
            metadata=body.metadata,
        )
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    return IngestResponse(job_id=job_id)


@router.post("/upload", response_model=IngestResponse)
async def ingest_upload(
    collection: str = Form(...),
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> IngestResponse:
    upload_dir = Path(os.getenv("RAG_UPLOAD_DIR", "./data/rag_uploads"))
    tenant_dir = upload_dir / tenant_id / collection
    tenant_dir.mkdir(parents=True, exist_ok=True)
    # Prefix uploads with a uuid so a re-upload of "policy.pdf" doesn't
    # collide with the previous one. Source_uri is what dedupe keys on, so
    # this also opts each upload into its own dedupe lane (intended).
    target = tenant_dir / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    try:
        job_id = await engine.ingest(
            source_name="file_loader",
            collection=collection,
            tenant_id=tenant_id,
            source_config={"path": str(target)},
            metadata={"filename": file.filename or "uploaded"},
        )
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    return IngestResponse(job_id=job_id)
