"""Azure OpenAI implementation of `Embedder`.

Azure only allows one model family per resource, so the embedding model
typically lives on its OWN Azure resource — separate endpoint + key from the
chat model. This embedder reads embedding-specific env vars and falls back to
the shared chat ones only when the embedding-specific value is unset:

  * `AZURE_OPENAI_EMBEDDING_ENDPOINT`    (falls back to `AZURE_OPENAI_ENDPOINT`)
  * `AZURE_OPENAI_EMBEDDING_API_KEY`     (falls back to `AZURE_OPENAI_API_KEY`)
  * `AZURE_OPENAI_EMBEDDING_API_VERSION` (falls back to `AZURE_OPENAI_API_VERSION`)
  * `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`  — the deployment name, used directly as
                                           the `model` on the wire.

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
        if client is None:
            # Prefer embedding-specific creds; fall back to the shared chat
            # resource so single-resource setups keep working unchanged.
            endpoint = (
                os.getenv("AZURE_OPENAI_EMBEDDING_ENDPOINT")
                or os.getenv("AZURE_OPENAI_ENDPOINT")
            )
            api_key = (
                os.getenv("AZURE_OPENAI_EMBEDDING_API_KEY")
                or os.getenv("AZURE_OPENAI_API_KEY")
            )
            api_version = (
                os.getenv("AZURE_OPENAI_EMBEDDING_API_VERSION")
                or os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
            )
            # Only pass non-empty creds, so the SDK's own env auto-read still
            # applies if something is left unset (mirrors the chat client).
            kwargs: dict = {"api_version": api_version}
            if endpoint:
                kwargs["azure_endpoint"] = endpoint
            if api_key:
                kwargs["api_key"] = api_key
            client = AsyncAzureOpenAI(**kwargs)
        self._client = client
        # The Azure deployment name IS the `model` we send on the wire.
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
