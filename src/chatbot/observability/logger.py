"""Structured JSON-line logger for chatbot turns and tool calls.

Writes one JSONL line per turn to LOG_DIR/turns.jsonl plus stdout.
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


def log_turn(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, default=str)
    with TURNS_FILE.open("a") as f:
        f.write(line + "\n")
    _logger.info("turn", extra={"payload": payload})


@contextmanager
def time_ms():
    start = time.perf_counter()
    holder = {"ms": 0}
    try:
        yield holder
    finally:
        holder["ms"] = int((time.perf_counter() - start) * 1000)
