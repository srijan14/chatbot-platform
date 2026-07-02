"""bytes -> text loaders.

Text passthrough (.md, .txt, .json, .html-ish), PDF via pypdf, and Word .docx
via python-docx. Adding another binary format later is "register another
`_extract_*` function and update `bytes_to_text`."
"""
from __future__ import annotations

import io
from pathlib import Path

# Word .docx MIME (OOXML). Legacy binary .doc is NOT supported — convert to
# .docx or PDF first.
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
        ".docx": DOCX_MIME,
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


def _extract_docx(data: bytes) -> str:
    from docx import Document as _Docx

    doc = _Docx(io.BytesIO(data))
    parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    # Tables carry real content in many Word docs — flatten each row to a line.
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                parts.append(line)
    return "\n\n".join(parts)


def bytes_to_text(data: bytes, mime_type: str) -> str:
    if mime_type == "application/pdf":
        return _extract_pdf(data)
    if mime_type == DOCX_MIME:
        return _extract_docx(data)
    # Everything else: decode best-effort utf-8.
    return data.decode("utf-8", errors="replace")
