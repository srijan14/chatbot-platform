"""TAG engine — text-to-analytics: NL → SQL → exec → summary.

Built on LlamaIndex (schema retrieval + NL-SQL generation) with our own
sqlglot AST validation, read-only executor, and dedicated summarizer LLM
layered on top so we never trust the model's output to be safe.
"""
