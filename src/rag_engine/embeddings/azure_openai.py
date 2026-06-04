"""Azure OpenAI implementation of `Embedder`.

Reuses the same env vars the chatbot already needs (`AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`) plus
`AZURE_OPENAI_EMBEDDING_DEPLOYMENT` for the embedding deployment name.

Batching: text-embedding-3-small accepts up to 2048 inputs per request; we
batch in groups of 64 to keep latency predictable and avoid hitting any
per-request payload size limits on long chunks.
"""
from __future__ import annotations

import os

from openai import AsyncAzureOpenAI

from rag_engine.embeddings.base import Embedder

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMS = 1536
BATCH_SIZE = 64


class AzureOpenAIEmbedder(Embedder):
    def __init__(
        self,
        client: AsyncAzureOpenAI | None = None,
        deployment: str | None = None,
        dimensions: int = DEFAULT_DIMS,
    ):
        self._client = client or AsyncAzureOpenAI(
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        self.model = deployment or os.getenv(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", DEFAULT_MODEL
        )
        self.dimensions = dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            resp = await self._client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out

    async def embed_query(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding
