"""Round-trip the Chroma adapter through the VectorStore protocol surface."""
from __future__ import annotations

import pytest

from rag_engine.vector_store.base import UpsertItem
from rag_engine.vector_store.chroma_store import ChromaVectorStore


@pytest.mark.asyncio
async def test_upsert_query_round_trip(tmp_path):
    vs = ChromaVectorStore(persist_path=str(tmp_path / "chroma"))
    await vs.create_collection("t1__kb", dimensions=4)

    items = [
        UpsertItem(id=f"d0:{i:04d}",
                   embedding=[1.0 if j == i else 0.0 for j in range(4)],
                   document=f"doc {i}",
                   metadata={"tenant_id": "t1", "doc_id": "d0", "i": i})
        for i in range(4)
    ]
    await vs.upsert("t1__kb", items)

    hits = await vs.query("t1__kb", query_embedding=[1.0, 0.0, 0.0, 0.0], top_k=2)
    assert len(hits) == 2
    assert hits[0].id == "d0:0000"


@pytest.mark.asyncio
async def test_delete_by_filter(tmp_path):
    vs = ChromaVectorStore(persist_path=str(tmp_path / "chroma"))
    await vs.create_collection("t1__kb", dimensions=2)

    items = [
        UpsertItem(id="a:0000", embedding=[1.0, 0.0], document="a", metadata={"doc_id": "a"}),
        UpsertItem(id="a:0001", embedding=[0.9, 0.1], document="a2", metadata={"doc_id": "a"}),
        UpsertItem(id="b:0000", embedding=[0.0, 1.0], document="b", metadata={"doc_id": "b"}),
    ]
    await vs.upsert("t1__kb", items)

    removed = await vs.delete_by_filter("t1__kb", where={"doc_id": "a"})
    assert removed == 2

    hits = await vs.query("t1__kb", query_embedding=[1.0, 0.0], top_k=5)
    assert all(h.metadata.get("doc_id") == "b" for h in hits)


@pytest.mark.asyncio
async def test_list_and_drop_collection(tmp_path):
    vs = ChromaVectorStore(persist_path=str(tmp_path / "chroma"))
    await vs.create_collection("alpha__kb", dimensions=2)
    await vs.create_collection("beta__kb", dimensions=2)
    names = await vs.list_collections()
    assert {"alpha__kb", "beta__kb"} <= set(names)

    await vs.drop_collection("beta__kb")
    names_after = await vs.list_collections()
    assert "beta__kb" not in names_after
