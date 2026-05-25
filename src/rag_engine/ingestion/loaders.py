"""bytes -> text loaders.

Only the basics for v1: text passthrough (.md, .txt, .json, .html-ish) and
PDF via pypdf. Adding .docx/.pptx later is "register another `_extract_*`
function and update `bytes_to_text`."
"""
from __future__ import annotations

import io
from pathlib import Path


def mime_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".log": "text/plain",
        ".json": "application/json",
        ".html": "text/html",
        ".htm": "text/html",
        ".pdf": "application/pdf",
    }.get(suffix, "application/octet-stream")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        if txt.strip():
            pages.append(f"[page {i}]\n{txt.strip()}")
    return "\n\n".join(pages)


def bytes_to_text(data: bytes, mime_type: str) -> str:
    if mime_type == "application/pdf":
        return _extract_pdf(data)
    # Everything else: decode best-effort utf-8.
    return data.decode("utf-8", errors="replace")
