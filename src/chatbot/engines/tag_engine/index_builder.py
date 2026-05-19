"""Build LlamaIndex artefacts for the TAG pipeline.

Two pieces of LlamaIndex used:
  • `SQLDatabase` — thin wrapper over a SQLAlchemy engine that gives the rest
    of LlamaIndex tabular introspection helpers (schema, sample rows). We
    open with a read-only `?mode=ro` sqlite URI so even if downstream code
    forgets to validate, the connection itself cannot mutate.
  • `ObjectIndex` over `SQLTableSchema` — embeds each table's description
    (from the semantic layer) so the retriever can pick the most relevant
    tables for a given user question. Required for schemas with >~20 tables;
    cheap insurance even for our 6-table demo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llama_index.core import SQLDatabase, VectorStoreIndex
from llama_index.core.objects import ObjectIndex, SQLTableNodeMapping, SQLTableSchema
from sqlalchemy import create_engine

from src.chatbot.engines.tag_engine.semantic_layer import SemanticLayer


@dataclass
class TagIndex:
    """Pre-built LlamaIndex artefacts used by the TAG pipeline."""
    sql_database: SQLDatabase
    object_index: ObjectIndex
    semantic_layer: SemanticLayer


def build_tag_index(
    semantic_layer: SemanticLayer,
    embed_model: Any,
) -> TagIndex:
    """Construct LlamaIndex's SQLDatabase + ObjectIndex for this warehouse.

    `embed_model` is a LlamaIndex `BaseEmbedding`-shaped object (e.g.
    AzureOpenAIEmbedding). We pass it explicitly so callers control which
    embedding deployment is used, and tests can inject a fake.
    """
    # Resolve to absolute so the URI doesn't depend on cwd; sqlite mode=ro
    # refuses to create the file if missing, so check up front for a
    # human-readable error instead of SQLAlchemy's generic
    # "unable to open database file".
    db_path = semantic_layer.database_path.resolve()
    if not db_path.exists():
        raise FileNotFoundError(
            f"BI warehouse not found at {db_path}. Run `make bi-seed` "
            f"(or `bi-seed --reset`) to create and populate it."
        )

    # Read-only sqlite URI: file is opened in mode=ro so a forgotten safety
    # check downstream cannot write to it.
    ro_uri = f"sqlite:///file:{db_path}?mode=ro&uri=true"
    engine = create_engine(ro_uri, future=True)

    table_names = [t.name for t in semantic_layer.tables]
    sql_database = SQLDatabase(engine, include_tables=table_names)

    # ObjectIndex over per-table summaries — the retriever returns the most
    # relevant SQLTableSchema(s) for a given NL question.
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

    return TagIndex(
        sql_database=sql_database,
        object_index=object_index,
        semantic_layer=semantic_layer,
    )
