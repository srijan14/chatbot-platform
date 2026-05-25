from fastapi import APIRouter, Depends, HTTPException

from rag_api.deps import get_engine, get_tenant
from rag_api.schemas import CollectionCreate, CollectionOut
from rag_engine import RagEngine
from rag_engine.models import CollectionSpec

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionOut)
async def create_collection(
    body: CollectionCreate,
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> CollectionOut:
    spec = CollectionSpec(
        name=body.name,
        tenant_id=tenant_id,
        embedding_model=body.embedding_model,
        dimensions=body.dimensions,
        description=body.description,
    )
    spec = await engine.ensure_collection(spec)
    return CollectionOut(
        name=spec.name,
        tenant_id=spec.tenant_id,
        embedding_model=spec.embedding_model,
        dimensions=spec.dimensions,
        description=spec.description,
    )


@router.get("", response_model=list[CollectionOut])
async def list_collections(
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> list[CollectionOut]:
    specs = await engine.list_collections(tenant_id)
    return [
        CollectionOut(
            name=s.name,
            tenant_id=s.tenant_id,
            embedding_model=s.embedding_model,
            dimensions=s.dimensions,
            description=s.description,
        )
        for s in specs
    ]


@router.delete("/{name}", status_code=204)
async def drop_collection(
    name: str,
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> None:
    dropped = await engine.drop_collection(tenant_id, name)
    if not dropped:
        raise HTTPException(404, detail=f"collection {name!r} not found")
