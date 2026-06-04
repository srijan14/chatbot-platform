"""Chunker Protocol — splits a Document into Chunks ready for embedding."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from rag_engine.models import Chunk, Document


@runtime_checkable
class Chunker(Protocol):
    async def chunk(self, doc: Document) -> list[Chunk]: ...
