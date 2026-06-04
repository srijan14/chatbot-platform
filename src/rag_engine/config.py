"""Declarative configuration loaders for RAG platform deployments.

Two YAMLs control the platform's day-2 behavior; both are optional. If
they're absent the service still boots, just without any pre-seeded
collections or scheduled connector syncs.

  configs/rag/collections.yaml
    tenants:
      <tenant_id>:
        collections:
          - name: <logical_name>
            embedding_model: text-embedding-3-small
            dimensions: 1536
            description: ...

  configs/rag/sources.yaml
    sources:
      - name: <unique_label>
        tenant: <tenant_id>
        collection: <logical_name>
        connector: <connector_name>      # registered in connectors.registry
        config: { ... connector-specific ... }
        schedule: { cron: "*/15 * * * *" }    # optional
        metadata: { ... }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rag_engine.models import CollectionSpec
from rag_engine.scheduler.scheduler import SourceSpec


@dataclass
class CollectionsConfig:
    specs: list[CollectionSpec] = field(default_factory=list)


def load_collections_yaml(path: str | Path) -> CollectionsConfig:
    p = Path(path)
    if not p.exists():
        return CollectionsConfig()
    data = yaml.safe_load(p.read_text()) or {}
    out: list[CollectionSpec] = []
    for tenant_id, tcfg in (data.get("tenants") or {}).items():
        for c in (tcfg or {}).get("collections", []):
            out.append(
                CollectionSpec(
                    name=c["name"],
                    tenant_id=tenant_id,
                    embedding_model=c.get("embedding_model", "text-embedding-3-small"),
                    dimensions=int(c.get("dimensions", 1536)),
                    description=c.get("description"),
                )
            )
    return CollectionsConfig(specs=out)


def load_sources_yaml(path: str | Path) -> list[SourceSpec]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    out: list[SourceSpec] = []
    for raw in data.get("sources") or []:
        schedule = raw.get("schedule") or {}
        out.append(
            SourceSpec(
                name=raw["name"],
                tenant=raw["tenant"],
                collection=raw["collection"],
                connector=raw["connector"],
                config=raw.get("config") or {},
                cron=schedule.get("cron"),
                metadata=raw.get("metadata") or {},
            )
        )
    return out
