"""Structured logger for chatbot turns.

Default sink: a `turn_logs` row in the chatbot DB. Optionally mirrors to
`LOG_DIR/turns.jsonl` for grep-friendly tailing when `LOG_JSONL=1`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.chatbot.persistence.models import TurnLog

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
TURNS_FILE = LOG_DIR / "turns.jsonl"


class _StdoutFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            return f"[{record.levelname}] {json.dumps(payload, default=str)}"
        return super().format(record)


_logger = logging.getLogger("chatbot")
if not _logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_StdoutFormatter("%(message)s"))
    _logger.addHandler(h)
    _logger.setLevel(logging.INFO)


def new_trace_id() -> str:
    return "trace_" + uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _jsonl_enabled() -> bool:
    return os.getenv("LOG_JSONL", "0") == "1"


async def log_turn(
    sessionmaker: async_sessionmaker[AsyncSession],
    payload: dict[str, Any],
) -> None:
    """Persist one turn. `payload` keys follow `TurnLog` columns; `tool_calls`
    (if present) is JSON-encoded into `tool_calls_json`."""
    tool_calls = payload.pop("tool_calls", None)
    row_kwargs = dict(payload)
    if tool_calls is not None:
        row_kwargs["tool_calls_json"] = json.dumps(tool_calls, default=str)

    async with sessionmaker() as s:
        s.add(TurnLog(**row_kwargs))
        await s.commit()

    if _jsonl_enabled():
        mirror = dict(payload)
        if tool_calls is not None:
            mirror["tool_calls"] = tool_calls
        with TURNS_FILE.open("a") as f:
            f.write(json.dumps(mirror, default=str) + "\n")

    log_payload = dict(payload)
    if tool_calls is not None:
        log_payload["tool_calls"] = tool_calls
    _logger.info("turn", extra={"payload": log_payload})


@contextmanager
def time_ms():
    start = time.perf_counter()
    holder = {"ms": 0}
    try:
        yield holder
    finally:
        holder["ms"] = int((time.perf_counter() - start) * 1000)
