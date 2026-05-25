"""End-to-end TAG pipeline test against the seeded BI warehouse.

The SQL-generation stage runs through LlamaIndex's `NLSQLRetriever`, so we
inject a `CannedLLM` (LlamaIndex `CustomLLM` subclass that returns
pre-set strings) as the SQL-gen model. The summarizer is still a LangChain
LLM so we use `GenericFakeChatModel` for that. Embeddings use LlamaIndex's
`MockEmbedding`.

If `data/bi_warehouse.db` doesn't exist (i.e. `make bi-seed` hasn't run),
the tests skip rather than fail.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from llama_index.core import Settings
from llama_index.core.embeddings.mock_embed_model import MockEmbedding
from llama_index.core.llms import CompletionResponse, CustomLLM, LLMMetadata

from src.chatbot.engines.tag_engine.index_builder import build_tag_index
from src.chatbot.engines.tag_engine.pipeline import TagConfig, TagPipeline
from src.chatbot.engines.tag_engine.semantic_layer import SemanticLayer

WAREHOUSE = Path("data/bi_warehouse.db")
SEMANTIC_LAYER = "configs/semantic_layers/ecommerce.yaml"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="run `make bi-seed` to populate data/bi_warehouse.db first",
)


class CannedLLM(CustomLLM):
    """LlamaIndex LLM that returns each item from `responses` in turn.

    NLSQLRetriever calls `complete()` once per retrieve(); we use that for
    the canned SQL strings. `stream_complete` is unused by NLSQLRetriever.
    """
    responses: list[str]
    idx: int = 0

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(context_window=4096, num_output=512, model_name="canned")

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        text = self.responses[self.idx]
        self.idx += 1
        return CompletionResponse(text=text)

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        raise NotImplementedError()


def _build_pipeline(
    sql_responses: list[str],
    summary_responses: list[str],
    *,
    use_embeddings: bool = True,
) -> TagPipeline:
    sl = SemanticLayer.from_yaml(SEMANTIC_LAYER)
    sql_gen_llm = CannedLLM(responses=sql_responses)
    summarizer = GenericFakeChatModel(messages=iter([AIMessage(content=c) for c in summary_responses]))
    mock_embed = MockEmbedding(embed_dim=8) if use_embeddings else None
    # NLSQLRetriever and ObjectIndex both consult the global Settings.embed_model
    # in places where the explicit param doesn't propagate; setting the global
    # avoids "no OPENAI_API_KEY" errors when LlamaIndex hits its default.
    if mock_embed is not None:
        Settings.embed_model = mock_embed
    Settings.llm = sql_gen_llm
    index = build_tag_index(
        sl,
        llm=sql_gen_llm,
        embed_model=mock_embed,
        schema_top_k=4,
    )
    cfg = TagConfig(
        semantic_layer_path=SEMANTIC_LAYER,
        schema_top_k=4,
        row_limit=20,
        repair_max_attempts=3,
        query_timeout_seconds=2.0,
    )
    return TagPipeline(index, summarizer_llm=summarizer, config=cfg)


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
    assert "LIMIT" in result.sql.upper()
    assert result.columns == ["country", "COUNT(*)"]
    countries = {row[0] for row in result.rows}
    assert countries & {"IN", "US", "UK"}
    assert "Customer counts" in result.summary
    assert "| country | COUNT(*) |" in result.summary


@pytest.mark.asyncio
async def test_repair_loop_kicks_in_on_invalid_sql():
    """First SQL gen returns a non-SELECT (sqlglot rejects); the pipeline
    must feed the error back into the next NLSQLRetriever call and succeed
    on the second attempt."""
    pipeline = _build_pipeline(
        sql_responses=[
            "DROP TABLE customers",                # rejected by validator
            "SELECT COUNT(*) FROM customers",      # accepted
        ],
        summary_responses=["120 customers in the warehouse."],
    )
    result = await pipeline.answer("How many customers do we have?")
    assert result.repair_attempts == 1
    assert "SELECT" in result.sql.upper()
    assert result.rows[0][0] >= 1


@pytest.mark.asyncio
async def test_repair_loop_exhausts_and_raises():
    """All three attempts return invalid SQL → pipeline raises RuntimeError."""
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


@pytest.mark.asyncio
async def test_no_embeddings_mode_still_answers():
    """The schema-RAG path is optional; without an embeddings deployment we
    fall back to passing every table directly to NLSQLRetriever. The
    pipeline must keep working end-to-end (validation + execution + summary)
    against the same warehouse."""
    pipeline = _build_pipeline(
        sql_responses=["SELECT COUNT(*) FROM customers"],
        summary_responses=["There are some number of customers."],
        use_embeddings=False,
    )
    result = await pipeline.answer("How many customers?")
    assert result.rows[0][0] >= 1
    assert "SELECT" in result.sql.upper()
