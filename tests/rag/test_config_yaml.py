"""Declarative YAML config loaders."""
from __future__ import annotations

from pathlib import Path

from rag_engine.config import load_collections_yaml, load_sources_yaml


def test_load_collections_yaml(tmp_path):
    p = tmp_path / "collections.yaml"
    p.write_text(
        """
tenants:
  alpha:
    collections:
      - name: kb
        embedding_model: text-embedding-3-small
        dimensions: 1536
        description: alpha KB
      - name: tickets
        dimensions: 768
"""
    )
    cfg = load_collections_yaml(p)
    assert len(cfg.specs) == 2
    names = {s.name: s for s in cfg.specs}
    assert names["kb"].tenant_id == "alpha"
    assert names["kb"].dimensions == 1536
    assert names["tickets"].dimensions == 768
    # Default model when omitted
    assert names["tickets"].embedding_model == "text-embedding-3-small"


def test_load_sources_yaml(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text(
        """
sources:
  - name: local_docs
    tenant: alpha
    collection: kb
    connector: file_loader
    config: {path: ./docs, glob: "**/*.md"}
    schedule: {cron: "*/15 * * * *"}
  - name: notes_no_cron
    tenant: alpha
    collection: kb
    connector: notion
    config: {database_id: deadbeef}
"""
    )
    sources = load_sources_yaml(p)
    assert len(sources) == 2
    assert sources[0].cron == "*/15 * * * *"
    assert sources[0].config["path"] == "./docs"
    assert sources[1].cron is None


def test_missing_files_return_empty():
    cfg = load_collections_yaml("/nonexistent/path.yaml")
    assert cfg.specs == []
    assert load_sources_yaml("/nonexistent/path.yaml") == []
