"""Ingestion pipeline: connector -> chunker -> embedder -> vector store.

Stays decoupled from the job queue / DB: it takes its dependencies as args
and returns counts. The JobRunner is what wires it into the persistence layer
and updates `ingestion_jobs` rows around it.

Dedupe + re-index strategy:
  - new doc:     embed + upsert all chunks
  - unchanged:   skip entirely
  - changed:     delete_by_filter(doc_id) then re-upsert. Combined with our
                 deterministic chunk ids this is idempotent even if a crash
                 interrupts re-upsert mid-flight.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rag_engine.chunking.base import Chunker
from rag_engine.connectors.base import SourceConnector
from rag_engine.embeddings.base import Embedder
from rag_engine.ingestion.dedupe import DedupeDecision, decide
from rag_engine.jobs.store import DocumentsRepo
from rag_engine.models import CollectionSpec
from rag_engine.vector_store.base import UpsertItem, VectorStore

log = logging.getLogger("rag_engine.ingestion")


@dataclass
class IngestionCounts:
    documents: int = 0
    chunks: int = 0
    embedded: int = 0
    upserted: int = 0
    skipped: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)

    def asdict(self) -> dict[str, int]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "upserted": self.upserted,
            "skipped": self.skipped,
            "errors": self.errors,
        }


class IngestionPipeline:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        chunker: Chunker,
        documents_repo: DocumentsRepo,
    ):
        self.vstore = vector_store
        self.embedder = embedder
        self.chunker = chunker
        self.docs = documents_repo

    async def run(
        self, connector: SourceConnector, spec: CollectionSpec
    ) -> IngestionCounts:
        counts = IngestionCounts()

        async for ref in connector.list_documents():
            try:
                doc = await connector.fetch_document(ref, spec.tenant_id, spec.name)
            except Exception as e:
                counts.errors += 1
                counts.error_messages.append(f"fetch {ref.source_uri}: {e}")
                log.exception("connector.fetch failed for %s", ref.source_uri)
                continue

            await self._index_document(doc, spec, counts)
        return counts

    async def ingest_one(
        self, doc: Document, spec: CollectionSpec
    ) -> tuple[IngestionCounts, DedupeDecision | None]:
        """Index a single, already-fetched Document (synchronous CRUD path).

        Shares the exact new/changed/unchanged logic the batch `run` uses, so
        an API-driven single-document upsert behaves identically to a connector
        sync. Returns the counts plus the dedupe decision so callers can report
        created vs. updated vs. unchanged.
        """
        counts = IngestionCounts()
        decision = await self._index_document(doc, spec, counts)
        return counts, decision

    async def _index_document(
        self, doc: Document, spec: CollectionSpec, counts: IngestionCounts
    ) -> DedupeDecision | None:
        """Dedupe → chunk → embed → (delete-if-changed) → upsert one document.

        Mutates `counts` in place and returns the DedupeDecision (or None if the
        document errored before it could be indexed).
        """
        physical = spec.physical_name()
        counts.documents += 1

        # Dedupe — open a session for the read; the write happens after
        # successful upsert below (so a failed embed doesn't update the hash).
        session_ctx = await self.docs.session()
        async with session_ctx as session:
            decision = await decide(session, doc)

        if not decision.should_index:
            counts.skipped += 1
            return decision

        chunks = await self.chunker.chunk(doc)
        counts.chunks += len(chunks)

        if not chunks:
            # Empty doc — record it so we don't keep re-processing. If it
            # previously had chunks (now emptied), drop the stale ones first.
            if decision.is_changed:
                await self.vstore.delete_by_filter(physical, where={"doc_id": doc.doc_id})
            await self.docs.upsert(
                doc_id=doc.doc_id, tenant_id=spec.tenant_id, collection=spec.name,
                source_uri=doc.source_uri, text=doc.content,
                chunk_count=0, metadata=doc.metadata,
            )
            return decision

        try:
            embeddings = await self.embedder.embed_documents([c.text for c in chunks])
            counts.embedded += len(embeddings)
        except Exception as e:
            counts.errors += 1
            counts.error_messages.append(f"embed {doc.source_uri}: {e}")
            log.exception("embedder failed for %s", doc.source_uri)
            return decision

        # If this doc previously had chunks, drop them first. Combined
        # with deterministic `chunk_id`s this is safe under crashes.
        if decision.is_changed:
            await self.vstore.delete_by_filter(physical, where={"doc_id": doc.doc_id})

        items = [
            UpsertItem(id=c.chunk_id, embedding=e, document=c.text, metadata=c.metadata)
            for c, e in zip(chunks, embeddings)
        ]
        try:
            await self.vstore.upsert(physical, items)
            counts.upserted += len(items)
        except Exception as e:
            counts.errors += 1
            counts.error_messages.append(f"upsert {doc.source_uri}: {e}")
            log.exception("vector_store.upsert failed for %s", doc.source_uri)
            return decision

        await self.docs.upsert(
            doc_id=doc.doc_id,
            tenant_id=spec.tenant_id,
            collection=spec.name,
            source_uri=doc.source_uri,
            text=doc.content,
            chunk_count=len(chunks),
            metadata=doc.metadata,
        )
        return decision
