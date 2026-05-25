"""Ingestion package — loaders, chunkers (re-exported from chunking), and the
ingestion pipeline.

We deliberately do NOT re-export `IngestionPipeline` from this package
init: doing so creates a circular import via jobs → connectors → ingestion.
Import the submodule directly (`from rag_engine.ingestion.pipeline import …`).
"""
