"""Build LlamaIndex artefacts for the TAG pipeline.

Two NLSQLRetriever shapes depending on whether the deployment has an
embeddings model:

  • With embeddings  — build an `ObjectIndex` over per-table descriptions
    (the LlamaIndex schema-RAG pattern). NLSQLRetriever's `table_retriever`
    picks the top-K relevant tables per question. Required for schemas
    with many tables (don't want to dump every DDL into the prompt).

  • Without embeddings — pass `tables=[<all table names>]` to NLSQLRetriever
    directly. No retrieval, the full DDL of every configured table goes
    into the SQL-gen prompt. Fine for the 6-table demo warehouse; would
    be wasteful for a 500-table production schema.

This split exists because most Azure OpenAI resources only deploy a chat
model (e.g. o4-mini) and don't have an embeddings deployment. The TAG
skill should work either way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llama_index.core import SQLDatabase, VectorStoreIndex
from llama_index.core.objects import ObjectIndex, SQLTableNodeMapping, SQLTableSchema
from llama_index.core.retrievers import NLSQLRetriever
from sqlalchemy import create_engine

from src.chatbot.engines.tag_engine.semantic_layer import SemanticLayer
from src.chatbot.observability.logger import get_logger

_log = get_logger("tag")


@dataclass
class TagIndex:
    """Pre-built LlamaIndex artefacts used by the TAG pipeline."""
    sql_database: SQLDatabase
    object_index: ObjectIndex | None     # None when embeddings aren't available
    nl_sql_retriever: NLSQLRetriever
    semantic_layer: SemanticLayer


def build_tag_index(
    semantic_layer: SemanticLayer,
    llm: Any,
    *,
    embed_model: Any | None = None,
    schema_top_k: int = 4,
) -> TagIndex:
    """Construct LlamaIndex's SQLDatabase + (optional ObjectIndex) + NLSQLRetriever.

    If `embed_model` is provided, builds an ObjectIndex over per-table
    descriptions and wires it as the NLSQLRetriever's `table_retriever`
    (schema-RAG mode). If `embed_model` is None, skips the ObjectIndex
    and configures NLSQLRetriever with the full table list (no retrieval).

    Either way, `llm` (a LlamaIndex `LLM`-shaped object) drives the
    NL→SQL generation.
    """
    db_path = semantic_layer.database_path.resolve()
    if not db_path.exists():
        raise FileNotFoundError(
            f"BI warehouse not found at {db_path}. Run `make bi-seed` "
            f"(or `bi-seed --reset`) to create and populate it."
        )

    ro_uri = f"sqlite:///file:{db_path}?mode=ro&uri=true"
    engine = create_engine(ro_uri, future=True)

    table_names = [t.name for t in semantic_layer.tables]
    sql_database = SQLDatabase(engine, include_tables=table_names)

    object_index: ObjectIndex | None = None
    if embed_model is not None:
        # Schema-RAG path: embed per-table descriptions; the retriever picks
        # the top-K most relevant tables per question.
        table_node_mapping = SQLTableNodeMapping(sql_database)
        table_schema_objs = [
            SQLTableSchema(table_name=t.name, context_str=t.description)
            for t in semantic_layer.tables
        ]
        object_index = ObjectIndex.from_objects(
            table_schema_objs,
            table_node_mapping,
            VectorStoreIndex,
            embed_model=embed_model,
        )
        nl_sql_retriever = NLSQLRetriever(
            sql_database=sql_database,
            table_retriever=object_index.as_retriever(similarity_top_k=schema_top_k),
            llm=llm,
            sql_only=True,
            return_raw=True,
            verbose=False,
        )
        _log.info(
            "[tag] INDEX-BUILT mode=schema_rag tables=%d top_k=%d",
            len(table_names), schema_top_k,
        )
    else:
        # No-embeddings fallback: pass all tables to NLSQLRetriever. The
        # retriever's text-to-sql prompt will see every table's DDL — fine
        # for small schemas; expensive in tokens for large ones.
        nl_sql_retriever = NLSQLRetriever(
            sql_database=sql_database,
            tables=table_names,
            llm=llm,
            sql_only=True,
            return_raw=True,
            verbose=False,
        )
        _log.info(
            "[tag] INDEX-BUILT mode=no_embeddings tables=%d "
            "(full DDL goes into prompt; set AZURE_OPENAI_EMBED_DEPLOYMENT "
            "to enable schema RAG)",
            len(table_names),
        )

    return TagIndex(
        sql_database=sql_database,
        object_index=object_index,
        nl_sql_retriever=nl_sql_retriever,
        semantic_layer=semantic_layer,
    )
