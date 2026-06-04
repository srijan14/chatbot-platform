"""Document-level dedupe helper.

`should_reingest(...)` decides whether a freshly fetched Document is new,
unchanged, or changed since the last ingest by comparing its `content_hash`
against the row in the `documents` table.
"""
from __future__ import annotations

from dataclasses import dataclass

from rag_engine.models import Document, content_hash
from rag_engine.storage.models import DocumentRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class DedupeDecision:
    is_new: bool
    is_changed: bool
    hash: str

    @property
    def should_index(self) -> bool:
        return self.is_new or self.is_changed


async def decide(session: AsyncSession, doc: Document) -> DedupeDecision:
    h = content_hash(doc.content)
    row = (
        await session.execute(
            select(DocumentRow).where(DocumentRow.doc_id == doc.doc_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return DedupeDecision(is_new=True, is_changed=False, hash=h)
    return DedupeDecision(is_new=False, is_changed=(row.content_hash != h), hash=h)
