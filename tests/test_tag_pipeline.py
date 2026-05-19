"""End-to-end TAG pipeline test against the seeded BI warehouse.

We use fake LLMs (deterministic canned responses) and a mock embedding so
the test runs offline. The warehouse + sqlglot validator + read-only sqlite
executor are real — this catches schema-RAG plumbing bugs and SQL execution
bugs that pure-unit tests would miss.

If `data/bi_warehouse.db` doesn't exist (i.e. `make bi-seed` hasn't run),
the tests skip rather than fail — keeps CI from failing on dev machines
that haven't seeded yet.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from llama_index.core.embeddings.mock_embed_model import MockEmbedding

from src.chatbot.engines.tag_engine.index_builder import build_tag_index
from src.chatbot.engines.tag_engine.pipeline import TagConfig, TagPipeline
from src.chatbot.engines.tag_engine.semantic_layer import SemanticLayer

WAREHOUSE = Path("data/bi_warehouse.db")
SEMANTIC_LAYER = "configs/semantic_layers/ecommerce.yaml"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="run `make bi-seed` to populate data/bi_warehouse.db first",
)


def _build_pipeline(sql_responses: list[str], summary_responses: list[str]) -> TagPipeline:
    sl = SemanticLayer.from_yaml(SEMANTIC_LAYER)
    index = build_tag_index(sl, embed_model=MockEmbedding(embed_dim=8))
    sql_gen = GenericFakeChatModel(messages=iter([AIMessage(content=c) for c in sql_responses]))
    summarizer = GenericFakeChatModel(messages=iter([AIMessage(content=c) for c in summary_responses]))
    cfg = TagConfig(
        semantic_layer_path=SEMANTIC_LAYER,
        schema_top_k=4,
        row_limit=20,
        repair_max_attempts=3,
        query_timeout_seconds=2.0,
    )
    return TagPipeline(index, sql_gen_llm=sql_gen, summarizer_llm=summarizer, config=cfg)


@pytest.mark.asyncio
async def test_happy_path_select_executes_and_summarises():
    """Valid SELECT → validator passes → executor returns real rows →
    summarizer produces the analyst answer + markdown table."""
    pipeline = _build_pipeline(
        sql_responses=["SELECT country, COUNT(*) FROM customers GROUP BY country"],
        summary_responses=["Customer counts by country."],
    )
    result = await pipeline.answer("How many customers per country?")
    assert result.repair_attempts == 0
    assert "LIMIT" in result.sql.upper()         # auto-injected by validator
    assert result.columns == ["country", "COUNT(*)"]
    countries = {row[0] for row in result.rows}
    # Seed uses IN/US/UK → at least one of these must come back.
    assert countries & {"IN", "US", "UK"}
    assert "Customer counts" in result.summary    # summarizer output present
    assert "| country | COUNT(*) |" in result.summary  # markdown table appended


@pytest.mark.asyncio
async def test_repair_loop_kicks_in_on_invalid_sql():
    """First SQL gen returns a non-SELECT (sqlglot rejects); the pipeline
    must feed the error back, re-prompt, and succeed on the second attempt."""
    pipeline = _build_pipeline(
        sql_responses=[
            "DROP TABLE customers",                # rejected by validator
            "SELECT COUNT(*) FROM customers",      # accepted
        ],
        summary_responses=["120 customers in the warehouse."],
    )
    result = await pipeline.answer("How many customers do we have?")
    assert result.repair_attempts == 1, "validator rejection should count as one repair"
    assert "SELECT" in result.sql.upper()
    assert result.rows[0][0] >= 1                  # real count from warehouse


@pytest.mark.asyncio
async def test_repair_loop_exhausts_and_raises():
    """All three attempts return invalid SQL → pipeline raises RuntimeError
    rather than silently returning nonsense."""
    pipeline = _build_pipeline(
        sql_responses=["DROP TABLE customers"] * 3,
        summary_responses=[],
    )
    with pytest.raises(RuntimeError, match="repair loop exhausted"):
        await pipeline.answer("Boom")


@pytest.mark.asyncio
async def test_list_metrics_returns_semantic_summary():
    pipeline = _build_pipeline(sql_responses=[], summary_responses=[])
    text = await pipeline.list_metrics_text()
    assert "metrics:" in text.lower()
    assert "dimensions:" in text.lower()
    assert "gross_revenue" in text
    assert "customer_segment" in text
