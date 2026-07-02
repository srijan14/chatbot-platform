"""S3-compatible BlobStore — targets MinIO (and any S3 API) for production.

Behind the same `BlobStore` Protocol as `LocalBlobStore`, so switching backends
is a wiring change (see `chatbot.core.rag_runtime`), not a code change. boto3 is
synchronous, so each call is pushed to a thread to keep the event loop free.

`url()` returns a **presigned GET URL** — a time-limited, directly-fetchable link
to the object. When the internal endpoint the service uses (e.g. `minio:9000` or
`localhost:9000`) differs from the host a partner can actually reach, set
`public_endpoint` so the *signed* link points at the reachable host.
"""
from __future__ import annotations

import asyncio
import logging

from rag_engine.storage.blobs.base import BlobRef

log = logging.getLogger("rag_engine.blobs")


class S3BlobStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str = "rag-documents",
        region: str = "us-east-1",
        public_endpoint: str | None = None,
        url_expiry: int = 3600,
    ):
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._expiry = url_expiry
        # Path-style addressing + s3v4 signing is what MinIO expects.
        cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        common = dict(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=cfg,
        )
        # Ops client talks to the endpoint the service can reach internally.
        self._client = boto3.client("s3", endpoint_url=endpoint_url, **common)
        # Presign against the public endpoint if the internal one isn't reachable
        # by whoever opens the link; otherwise reuse the ops client.
        if public_endpoint and public_endpoint != endpoint_url:
            self._url_client = boto3.client("s3", endpoint_url=public_endpoint, **common)
        else:
            self._url_client = self._client
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Create the bucket if missing. Best-effort: if the endpoint is
        unreachable at construction we log and carry on — later operations will
        surface the real error rather than crashing engine startup."""
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except Exception as exc:  # pragma: no cover - depends on live MinIO
                log.warning("could not create bucket %s: %s", self._bucket, exc)
        except Exception as exc:  # pragma: no cover - unreachable endpoint
            log.warning("S3 endpoint unavailable at startup (%s); document "
                        "storage will fail until it is reachable.", exc)

    async def put(self, key: str, data: bytes, content_type: str) -> BlobRef:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data,
                ContentType=content_type or "application/octet-stream",
            )
        await asyncio.to_thread(_put)
        return BlobRef(key=key, content_type=content_type, size_bytes=len(data))

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()

        try:
            return await asyncio.to_thread(_get)
        except self._client.exceptions.NoSuchKey:
            raise FileNotFoundError(key)

    async def delete(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        def _del() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError:
                return False
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True

        return await asyncio.to_thread(_del)

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        def _exists() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError:
                return False

        return await asyncio.to_thread(_exists)

    async def url(self, key: str, *, expires: int | None = None) -> str | None:
        exp = int(expires if expires is not None else self._expiry)

        def _url() -> str:
            return self._url_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=exp,
            )

        # Presigning is an offline crypto operation — no network round-trip.
        return await asyncio.to_thread(_url)
