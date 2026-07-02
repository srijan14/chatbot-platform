"""Turn-scoped collector for structured artifacts skills produce mid-turn.

Tools run inside LangGraph and can only *return a string* to the model — there's
no channel for structured data (like the source documents a RAG search used) to
reach the chat response. This module provides that channel: a `ContextVar`
holding a per-turn list. `run_turn` opens `capture_sources()` around the graph
invocation; the skill→tool adapter calls `add_sources(...)` as tools run; the
appended items are visible on the same list after the graph returns.

`ContextVar` (not a plain global) so concurrent turns for different sessions —
which share the same cached Skill instances — never cross-contaminate: each
asyncio task carries its own binding, and appends land on the task's own list.
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_turn_sources: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("turn_sources", default=None)
)


@contextmanager
def capture_sources() -> Iterator[list[dict[str, Any]]]:
    """Collect sources added during the block. Yields the (growing) list."""
    buffer: list[dict[str, Any]] = []
    token = _turn_sources.set(buffer)
    try:
        yield buffer
    finally:
        _turn_sources.reset(token)


def add_sources(sources: list[dict[str, Any]] | None) -> None:
    """Append source records to the active turn's collector (no-op if none is
    active or `sources` is empty)."""
    if not sources:
        return
    buffer = _turn_sources.get()
    if buffer is not None:
        buffer.extend(sources)
