"""TAG pipeline — NL question → analyst answer.

Sequence per query:
  1. Schema RAG          — retrieve the K most relevant tables via the
                           LlamaIndex ObjectIndex over per-table summaries.
  2. SQL generation      — ask the SQL-gen LLM for a single SQLite SELECT
                           grounded in those tables (with the semantic
                           layer's few-shot examples).
  3. AST validation      — sqlglot rejects non-SELECT, multi-statement,
                           DDL/DML, PRAGMA, ATTACH. LIMIT injected if missing.
  4. Read-only execution — file:?mode=ro sqlite handle; statement_timeout
                           cap so a stray cross-join can't hang.
  5. Repair loop         — on validator OR execution error, feed the error
                           back to the SQL-gen LLM and retry (up to N=3).
  6. Summarisation       — dedicated cheaper LLM (own deployment) turns
                           (question, sql, rows) → analyst answer + markdown.

The pipeline is fully async; it's safe to call from the LangGraph tool node.
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI

from src.chatbot.engines.tag_engine.index_builder import TagIndex
from src.chatbot.engines.tag_engine.sql_validator import (
    ValidationResult,
    validate_and_prepare,
)
from src.chatbot.engines.tag_engine.summarizer import summarize
from src.chatbot.observability.logger import get_logger, truncate

_log = get_logger("tag")


SQL_GEN_SYSTEM_PROMPT = """You are a careful SQL author. Translate the user's
business question into ONE SQLite SELECT statement that answers it.

Rules:
  - Output ONLY the SQL. No prose, no markdown fences, no leading text.
  - SELECT only. Never INSERT/UPDATE/DELETE/DROP/PRAGMA/ATTACH.
  - Always join through the FK columns shown in the table descriptions.
  - For "completed" revenue, filter orders.status = 'completed'.
  - For "last N days", use `date('now', '-N days')`.
  - For "this month" / "last month", use `date('now', 'start of month')` etc.
  - Aggregate properly: use SUM/COUNT/AVG and a GROUP BY when grouping.
  - Include an ORDER BY + LIMIT when the question implies "top N".
"""


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
        sql_gen_llm: AzureChatOpenAI,
        summarizer_llm: AzureChatOpenAI,
        config: TagConfig,
    ):
        self._index = index
        self._sql_gen = sql_gen_llm
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

        retrieved = await self._retrieve_tables(full_question)
        _log.info("[tag] SCHEMA-RETRIEVED tables=%s", [t.table_name for t in retrieved])

        sql = ""
        repair_attempts = 0
        validation: ValidationResult | None = None
        error_feedback: str | None = None
        columns: list[str] = []
        rows: list[tuple] = []

        for attempt in range(1, self._config.repair_max_attempts + 1):
            sql = await self._generate_sql(full_question, retrieved, error_feedback)
            _log.info(
                "[tag] NL2SQL-GENERATED attempt=%d sql=%s",
                attempt, truncate(sql, 240),
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
                error_feedback = f"The SQL was rejected by the safety validator: {validation.reason}. Fix and try again."
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
                    f"The SQL failed with a {type(exc).__name__}: {exc}. "
                    f"Rewrite it to avoid the error."
                )
                repair_attempts = attempt
                continue
        else:
            # Exhausted attempts.
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
            retrieved_tables=[t.table_name for t in retrieved],
        )

    # -- Internals -----------------------------------------------------------

    async def _retrieve_tables(self, question: str):
        retriever = self._index.object_index.as_retriever(
            similarity_top_k=self._config.schema_top_k,
        )
        # LlamaIndex's retriever is sync; offload so we don't block the event loop.
        return await asyncio.to_thread(retriever.retrieve, question)

    async def _generate_sql(
        self,
        question: str,
        retrieved_tables: list,
        error_feedback: str | None,
    ) -> str:
        # Build a focused prompt using the retrieved table schemas + few-shots.
        table_blocks = []
        for ts in retrieved_tables:
            ddl = self._index.sql_database.get_single_table_info(ts.table_name)
            description = ts.context_str or ""
            table_blocks.append(
                f"### Table: {ts.table_name}\n{description}\n\nDDL:\n{ddl}"
            )

        few_shot_lines: list[str] = []
        for ex in self._index.semantic_layer.few_shot_examples:
            few_shot_lines.append(f"Q: {ex.question}\nSQL:\n{ex.sql}\n")
        few_shots = "\n".join(few_shot_lines)

        user_payload = (
            f"Tables in scope:\n\n" + "\n\n".join(table_blocks) +
            (f"\n\nReference examples:\n\n{few_shots}" if few_shots else "") +
            (f"\n\nPREVIOUS ATTEMPT ERROR:\n{error_feedback}\nTry again."
             if error_feedback else "") +
            f"\n\nUser question:\n{question}\n\nSQL:"
        )

        response = await self._sql_gen.ainvoke([
            SystemMessage(content=SQL_GEN_SYSTEM_PROMPT),
            HumanMessage(content=user_payload),
        ])
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _strip_fences(content.strip())

    async def _execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Execute the SQL via a fresh read-only sqlite3 connection.

        Uses sqlite3 directly (not the SQLAlchemy engine) because the URI-mode
        read-only flag + per-connection statement timeout are easier to pin
        down at the DBAPI level. Offloaded to a thread so we don't block.
        """
        db_path = self._index.semantic_layer.database_path
        timeout = self._config.query_timeout_seconds
        return await asyncio.to_thread(_run_sqlite_query, db_path, sql, timeout)


def _run_sqlite_query(path: Path, sql: str, timeout_s: float) -> tuple[list[str], list[tuple]]:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_s)
    try:
        # Statement timeout via a busy timeout doesn't bound CPU-bound queries
        # but does bound waits on locks; for our demo it's sufficient. A
        # production deployment would use APSW or an async wrapper for hard
        # statement timeouts.
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
        # Drop the first fence line and everything after a trailing ```.
        first_newline = t.find("\n")
        if first_newline != -1:
            t = t[first_newline + 1 :]
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
