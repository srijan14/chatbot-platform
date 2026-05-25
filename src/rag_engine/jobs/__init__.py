"""Jobs package — queue, runner, store.

We deliberately do NOT re-export `JobRunner` here: it transitively imports the
connector registry, which imports loaders from rag_engine.ingestion, which
imports `DocumentsRepo` from this package. The circle resolves if we keep
__init__ side-effect-free and ask callers to use the submodule path.
"""
