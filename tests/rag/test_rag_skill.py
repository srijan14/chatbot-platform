"""RagSkill — in-process engine variant.

Two layers of coverage:
  * Unit: a fake RagEngine verifies tool schema, scoping (tenant/collection),
    default top_k injection, and the [N] citation formatting.
  * Integration: the real RagEngine (fake embedder + tmp Chroma) proves the
    skill returns a grounded, cited passage end-to-end without any services.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from rag_engine import RagEngine
from rag_engine.chunking.recursive import RecursiveCharChunker
from rag_engine.models import CollectionSpec, JobStatus, SearchResult
from rag_engine.vector_store.chroma_store import ChromaVectorStore

from src.chatbot.skills.rag_skill import RagSkill


# --- unit ------------------------------------------------------------------

def _fake_engine(results: list[SearchResult] | None = None) -> AsyncMock:
    engine = AsyncMock(spec=RagEngine)
    engine.search.return_value = results or []
    return engine


@pytest.mark.asyncio
async def test_prepare_tools_exposes_search_and_list():
    skill = RagSkill(_fake_engine(), tenant_id="bot1", collection="kb")
    tools = await skill.prepare_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == {"search_knowledge_base", "list_collections"}
    assert skill.owns_tool("search_knowledge_base")
    assert not skill.owns_tool("irrelevant_tool")


@pytest.mark.asyncio
async def test_execute_scopes_to_bot_collection_and_default_top_k():
    engine = _fake_engine()
    skill = RagSkill(engine, tenant_id="telecom_support", collection="telecom_policies", top_k=7)

    await skill.execute_tool("search_knowledge_base", {"query": "cancel"})

    engine.search.assert_awaited_once()
    kwargs = engine.search.call_args.kwargs
    assert kwargs["query"] == "cancel"
    assert kwargs["collection"] == "telecom_policies"
    assert kwargs["tenant_id"] == "telecom_support"
    assert kwargs["top_k"] == 7


@pytest.mark.asyncio
async def test_execute_honors_caller_top_k():
    engine = _fake_engine()
    skill = RagSkill(engine, tenant_id="b", collection="kb", top_k=5)
    await skill.execute_tool("search_knowledge_base", {"query": "x", "top_k": 2})
    assert engine.search.call_args.kwargs["top_k"] == 2


@pytest.mark.asyncio
async def test_execute_formats_citations():
    results = [
        SearchResult(
            chunk_id="d:0", doc_id="d", text="Refund within 7 days.", score=0.9,
            source_uri="file:///policies/refunds.md",
            metadata={"heading": "Refunds"},
        )
    ]
    skill = RagSkill(_fake_engine(results), tenant_id="b", collection="kb")
    out = await skill.execute_tool("search_knowledge_base", {"query": "refund"})
    assert not out.is_error
    assert "[1]" in out.text
    assert "file:///policies/refunds.md" in out.text
    assert "[Refunds]" in out.text


@pytest.mark.asyncio
async def test_execute_empty_query_is_error():
    skill = RagSkill(_fake_engine(), tenant_id="b", collection="kb")
    out = await skill.execute_tool("search_knowledge_base", {"query": "  "})
    assert out.is_error


def test_system_prompt_addition_override():
    assert "search_knowledge_base" in RagSkill(
        _fake_engine(), tenant_id="b", collection="kb"
    ).system_prompt_addition()
    assert RagSkill(
        _fake_engine(), tenant_id="b", collection="kb", search_instructions="custom hint"
    ).system_prompt_addition() == "custom hint"


# --- integration (real engine, no services) --------------------------------

@pytest.mark.asyncio
async def test_skill_over_real_engine_returns_grounded_passage(tmp_path, rag_sm, fake_embedder):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "cancel.md").write_text(
        "# Cancellation\nCancellation applies at end of cycle. Refund within 7 business days."
    )

    engine = RagEngine(
        vector_store=ChromaVectorStore(persist_path=str(tmp_path / "chroma")),
        embedder=fake_embedder,
        chunker=RecursiveCharChunker(size=200, overlap=20),
        sessionmaker=rag_sm,
    )
    await engine.start()
    try:
        await engine.ensure_collection(
            CollectionSpec(name="kb", tenant_id="telecom_support",
                           embedding_model="fake", dimensions=8)
        )
        job_id = await engine.ingest(
            source_name="file_loader", collection="kb", tenant_id="telecom_support",
            source_config={"path": str(corpus), "glob": "**/*.md"},
        )
        for _ in range(50):
            job = await engine.job_status(job_id)
            if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                break
            await asyncio.sleep(0.05)
        assert job.status == JobStatus.SUCCEEDED, f"job failed: {job.errors}"

        skill = RagSkill(engine, tenant_id="telecom_support", collection="kb", top_k=3)
        out = await skill.execute_tool("search_knowledge_base", {"query": "refund timeline"})
        assert not out.is_error
        assert "[1]" in out.text
        assert "Refund" in out.text or "Cancellation" in out.text
    finally:
        await engine.stop()
