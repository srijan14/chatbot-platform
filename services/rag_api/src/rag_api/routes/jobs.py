from fastapi import APIRouter, Depends, HTTPException

from rag_api.deps import get_engine, get_tenant
from rag_api.schemas import JobOut
from rag_engine import RagEngine

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> JobOut:
    job = await engine.job_status(job_id)
    if job is None or job.tenant_id != tenant_id:
        # Same 404 either way — never confirm a job's existence to the wrong
        # tenant. Cross-tenant fishing should fail closed.
        raise HTTPException(404, detail=f"job {job_id!r} not found")
    return JobOut(
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        collection=job.collection,
        source_name=job.source_name,
        status=job.status.value,
        counts=job.counts,
        errors=job.errors,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
