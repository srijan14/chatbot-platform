from fastapi import APIRouter, Depends, HTTPException

from rag_api.deps import get_engine, get_tenant
from rag_api.schemas import SearchHit, SearchRequest, SearchResponse
from rag_engine import RagEngine

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    tenant_id: str = Depends(get_tenant),
    engine: RagEngine = Depends(get_engine),
) -> SearchResponse:
    try:
        results = await engine.search(
            query=body.query,
            collection=body.collection,
            tenant_id=tenant_id,
            top_k=body.top_k,
            filters=body.filters,
        )
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    return SearchResponse(
        collection=body.collection,
        results=[
            SearchHit(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                text=r.text,
                score=r.score,
                source_uri=r.source_uri,
                metadata=r.metadata,
            )
            for r in results
        ],
    )
