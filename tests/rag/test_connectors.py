"""Connector unit tests — mock the HTTP layer; assert shape + dispatch."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_engine.connectors.confluence import ConfluenceConnector, _html_to_text
from rag_engine.connectors.file_loader import FileLoaderConnector
from rag_engine.connectors.notion import NotionConnector
from rag_engine.connectors.registry import default_registry


def test_registry_has_default_connectors():
    names = default_registry.names()
    assert "file_loader" in names
    # confluence and notion register opportunistically — they should be
    # present because their modules have no hard external dep (httpx is
    # always installed).
    assert "confluence" in names
    assert "notion" in names


@pytest.mark.asyncio
async def test_file_loader_lists_and_fetches_markdown(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("# A\nbody A")
    (d / "b.txt").write_text("plain b")
    sub = d / "sub"
    sub.mkdir()
    (sub / "c.md").write_text("# C\nbody C")

    conn = FileLoaderConnector({"path": str(d), "glob": "**/*.md"})
    refs = []
    async for ref in conn.list_documents():
        refs.append(ref)
    assert len(refs) == 2
    assert all(r.mime_type == "text/markdown" for r in refs)

    doc = await conn.fetch_document(refs[0], "t1", "kb")
    assert doc.tenant_id == "t1"
    assert doc.collection == "kb"
    assert "body A" in doc.content or "body C" in doc.content
    assert doc.metadata["filename"].endswith(".md")


@pytest.mark.asyncio
async def test_file_loader_single_file(tmp_path):
    p = tmp_path / "only.txt"
    p.write_text("hi")
    conn = FileLoaderConnector({"path": str(p)})
    refs = [r async for r in conn.list_documents()]
    assert len(refs) == 1
    doc = await conn.fetch_document(refs[0], "t", "c")
    assert doc.content == "hi"
    assert doc.mime_type == "text/plain"


def test_html_to_text_strips_tags():
    assert _html_to_text("<p>hello <b>world</b></p>") == "hello world"
    assert "\n" in _html_to_text("<h1>A</h1>\n<p>b</p>")


@pytest.mark.asyncio
async def test_confluence_requires_credentials(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_USER_EMAIL", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    with pytest.raises(ValueError):
        ConfluenceConnector({"base_url": "https://x.atlassian.net/wiki",
                             "space_key": "TELCO"})


@pytest.mark.asyncio
async def test_confluence_list_and_fetch(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "u@e")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")

    search_resp = {
        "results": [
            {"content": {"id": "100", "title": "Cancellation policy"}},
        ],
        "_links": {},
    }
    page_resp = {
        "body": {"storage": {"value": "<p>Cancellation in <b>7 days</b>.</p>"}},
        "version": {"number": 3},
    }

    fake = MagicMock()
    fake.get = AsyncMock()
    fake.get.side_effect = [
        MagicMock(status_code=200, raise_for_status=lambda: None,
                  json=lambda: search_resp),
        MagicMock(status_code=200, raise_for_status=lambda: None,
                  json=lambda: page_resp),
    ]
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)

    conn = ConfluenceConnector({
        "base_url": "https://x.atlassian.net/wiki", "space_key": "TELCO",
    })
    with patch.object(conn, "_client", return_value=fake):
        refs = [r async for r in conn.list_documents()]
        assert len(refs) == 1
        assert refs[0].metadata["page_id"] == "100"

        doc = await conn.fetch_document(refs[0], "t1", "kb")
        assert "Cancellation in 7 days" in doc.content
        assert doc.metadata["version"] == 3
        assert doc.metadata["space"] == "TELCO"


@pytest.mark.asyncio
async def test_notion_requires_id_or_token(monkeypatch):
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    with pytest.raises(ValueError):
        NotionConnector({"page_id": "abc"})
    monkeypatch.setenv("NOTION_API_TOKEN", "tok")
    with pytest.raises(ValueError):
        NotionConnector({})   # neither database_id nor page_id


@pytest.mark.asyncio
async def test_notion_fetch_flattens_blocks(monkeypatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "tok")
    page_id = "abc-123"
    blocks_resp = {
        "results": [
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Refunds"}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Within 7 days."}]}},
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "UPI"}]}},
        ],
        "has_more": False,
    }
    fake = MagicMock()
    fake.get = AsyncMock(return_value=MagicMock(
        status_code=200, raise_for_status=lambda: None, json=lambda: blocks_resp
    ))
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)

    conn = NotionConnector({"page_id": page_id})
    with patch.object(conn, "_client", return_value=fake):
        refs = [r async for r in conn.list_documents()]
        assert refs[0].metadata["page_id"] == page_id
        doc = await conn.fetch_document(refs[0], "t", "kb")
        assert "## Refunds" in doc.content
        assert "Within 7 days." in doc.content
        assert "- UPI" in doc.content
