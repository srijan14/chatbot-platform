"""ChunkPlanner — the seam an LLM (or any heuristic) plugs into to decide *how*
a document should be chunked, before `LLMRoutingChunker` does the splitting.

Provider-neutral on purpose: like `Embedder`, `VectorStore`, and `Chunker`, no
Azure/OpenAI/FastAPI types leak in here. The concrete, Azure-backed planner lives
in the chatbot layer (`src/chatbot/core/chunk_planner.py`) and is injected.

A planner inspects a *sample* of the document plus its mime type / metadata and
returns a `ChunkPlan` (strategy + size/overlap). `LLMRoutingChunker` maps that
plan onto the existing concrete chunkers — so adding a planner never changes the
chunk shapes the rest of the pipeline already understands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Strategy names the router knows how to build. Keep in sync with
# `LLMRoutingChunker._build_chunker` and the planner's allowed outputs.
MARKDOWN = "markdown"
RECURSIVE = "recursive"
KNOWN_STRATEGIES = frozenset({MARKDOWN, RECURSIVE})


@dataclass
class ChunkPlan:
    """A planner's decision for one document.

    `strategy` selects the concrete chunker; `size`/`overlap` tune it. `reason`
    is free text for logging/observability only — it never affects splitting.
    """
    strategy: str                  # one of KNOWN_STRATEGIES
    size: int = 800
    overlap: int = 120
    reason: str = ""


@runtime_checkable
class ChunkPlanner(Protocol):
    async def plan(
        self, *, sample: str, mime_type: str, metadata: dict
    ) -> ChunkPlan: ...
