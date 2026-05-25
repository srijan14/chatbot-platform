"""TAG pipeline — NL question → analyst answer.

Sequence per query:
  1. Schema RAG + SQL gen — LlamaIndex's `NLSQLRetriever(sql_only=True)`
                            picks relevant tables via the ObjectIndex
                            retriever and emits a SELECT (we configured
                            it with our Azure LLM at index-build time).
  2. AST validation      — sqlglot rejects non-SELECT, multi-statement,
                           DDL/DML, PRAGMA, ATTACH. LIMIT injected if missing.
  3. Read-only execution — file:?mode=ro sqlite handle; statement_timeout
                           cap so a stray cross-join can't hang.
  4. Repair loop         — on validator OR execution error, prepend the
                           error to the next NLSQLRetriever query string
                           (the retriever has no first-class feedback
                           param) and retry up to N=3 attempts.
  5. Summarisation       — dedicated LangChain Azure LLM (own deployment)
                           turns (question, sql, rows) → analyst answer
                           + markdown table.

The pipeline is fully async; LlamaIndex's retriever is sync so we offload
its calls to a thread.
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from langchain_openai import AzureChatOpenAI

from src.chatbot.engines.tag_engine.index_builder import TagIndex
from src.chatbot.engines.tag_engine.sql_validator import (
    ValidationResult,
    validate_and_prepare,
)
from src.chatbot.engines.tag_engine.summarizer import summarize
from src.chatbot.observability.logger import get_logger, truncate

_log = get_logger("tag")


@dataclass
class TagConfig:
    """Runtime knobs for the TAG pipeline; populated from a bot's YAML."""
    semantic_layer_path: str
    # LLM deployments
    sql_gen_deployment: str = "gpt-4o"
    sql_gen_temperature: float = 0.0
    sql_gen_max_tokens: int = 512
    summarizer_deployment: str = "gpt-4o-mini"
    summarizer_temperature: float = 0.2
    summarizer_max_tokens: int = 400
    # Retrieval + safety
    schema_top_k: int = 4
    row_limit: int = 100
    repair_max_attempts: int = 3
    # SQLite query timeout (whole connection). 2s is enough for the demo.
    query_timeout_seconds: float = 2.0


@dataclass
class TagResult:
    """Output of one TAG query."""
    question: str
    sql: str
    columns: list[str]
    rows: list[tuple]
    summary: str
    repair_attempts: int = 0
    retrieved_tables: list[str] = field(default_factory=list)


class TagPipeline:
    def __init__(
        self,
        index: TagIndex,
        *,
        summarizer_llm: AzureChatOpenAI,
        config: TagConfig,
    ):
        self._index = index
        self._summarizer = summarizer_llm
        self._config = config

    # -- Public API ----------------------------------------------------------

    async def list_metrics_text(self) -> str:
        return self._index.semantic_layer.summary_for_user()

    async def answer(self, question: str, *, time_range: str | None = None) -> TagResult:
        full_question = (
            f"{question.strip()} (time range: {time_range})"
            if time_range else question.strip()
        )
        _log.info("[tag] NL2SQL-START question=%r", truncate(full_question, 200))

        sql = ""
        repair_attempts = 0
        validation: ValidationResult | None = None
        error_feedback: str | None = None
        columns: list[str] = []
        rows: list[tuple] = []
        retrieved_tables: list[str] = []

        for attempt in range(1, self._config.repair_max_attempts + 1):
            sql, retrieved_tables = await self._generate_sql(full_question, error_feedback)
            _log.info(
                "[tag] NL2SQL-GENERATED attempt=%d tables=%s sql=%s",
                attempt, retrieved_tables, truncate(sql, 240),
            )
            validation = validate_and_prepare(
                sql,
                dialect=self._index.semantic_layer.dialect,
                row_limit=self._config.row_limit,
            )
            if not validation.ok:
                _log.info(
                    "[tag] SQL-VALIDATED ok=false attempt=%d reason=%s",
                    attempt, validation.reason,
                )
                error_feedback = (
                    f"Previous attempt SQL was rejected by the safety "
                    f"validator: {validation.reason}. Rewrite to avoid it."
                )
                repair_attempts = attempt
                continue
            _log.info("[tag] SQL-VALIDATED ok=true attempt=%d", attempt)

            try:
                columns, rows = await self._execute(validation.sql)
                _log.info(
                    "[tag] SQL-EXECUTED attempt=%d rows=%d", attempt, len(rows),
                )
                sql = validation.sql  # the LIMIT-injected version
                break
            except sqlite3.Error as exc:
                _log.info(
                    "[tag] SQL-EXEC-FAILED attempt=%d error=%s",
                    attempt, type(exc).__name__,
                )
                error_feedback = (
                    f"Previous SQL failed with a {type(exc).__name__}: {exc}. "
                    f"Rewrite to avoid the error."
                )
                repair_attempts = attempt
                continue
        else:
            final_reason = (validation.reason if validation else "no validation")
            raise RuntimeError(
                f"TAG repair loop exhausted ({repair_attempts} attempts). "
                f"Last reason: {final_reason or 'unknown'}"
            )

        summary = await summarize(
            self._summarizer,
            question=question,
            sql=sql,
            columns=columns,
            rows=rows,
        )
        _log.info("[tag] SUMMARIZER-DONE chars=%d", len(summary))

        return TagResult(
            question=question,
            sql=sql,
            columns=columns,
            rows=rows,
            summary=summary,
            repair_attempts=repair_attempts,
            retrieved_tables=retrieved_tables,
        )

    # -- Internals -----------------------------------------------------------

    async def _generate_sql(
        self,
        question: str,
        error_feedback: str | None,
    ) -> tuple[str, list[str]]:
        """Drive LlamaIndex's NLSQLRetriever; return (sql, retrieved_tables).

        On repair-loop retries we prepend the previous error to the query
        string. LlamaIndex's NLSQLRetriever doesn't expose a first-class
        feedback parameter, so this is the documented workaround — the
        retriever sees the error context as part of the user question.
        """
        if error_feedback:
            query_str = f"{error_feedback}\n\nUser question: {question}"
        else:
            query_str = question

        # NLSQLRetriever.retrieve is sync; offload so we don't block the loop.
        nodes = await asyncio.to_thread(
            self._index.nl_sql_retriever.retrieve, query_str
        )
        if not nodes:
            return "", []

        # In sql_only=True mode, the first node's text IS the SQL string.
        first = nodes[0]
        sql_text = (first.text or "").strip()
        # Retrieved table names come back in the node metadata (key varies by
        # llama-index version; we look at both common spellings).
        metadata = getattr(first, "metadata", None) or {}
        tables = (
            metadata.get("table_names")
            or metadata.get("tables")
            or []
        )
        return _strip_fences(sql_text), list(tables)

    async def _execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Execute the SQL via a fresh read-only sqlite3 connection."""
        db_path = self._index.semantic_layer.database_path
        timeout = self._config.query_timeout_seconds
        return await asyncio.to_thread(_run_sqlite_query, db_path, sql, timeout)


def _run_sqlite_query(path: Path, sql: str, timeout_s: float) -> tuple[list[str], list[tuple]]:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_s)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        return columns, rows
    finally:
        conn.close()


def _strip_fences(text: str) -> str:
    """Remove ```sql ... ``` wrappers if the model added them despite the prompt."""
    t = text.strip()
    if t.startswith("```"):
        first_newline = t.find("\n")
        if first_newline != -1:
            t = t[first_newline + 1 :]
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
