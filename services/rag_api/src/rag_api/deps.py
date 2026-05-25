"""FastAPI dependencies — tenant resolution + engine accessor."""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from rag_engine import RagEngine


def get_engine(request: Request) -> RagEngine:
    engine: RagEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, detail="rag engine not initialized")
    return engine


def get_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    """Every mutating + retrieval request must declare its tenant.

    We don't fall back to a default — silent cross-tenant access is exactly
    the bug class we want to make impossible.
    """
    if not x_tenant_id:
        raise HTTPException(400, detail="X-Tenant-Id header is required")
    return x_tenant_id
