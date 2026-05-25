"""Connector registry.

Connectors register by name so `routes/ingest.py` can dispatch on
`source: "file_loader" | "confluence" | "notion" | ...` without growing a
hard-coded if/elif tree. New connector = subclass + `registry.register(cls)`.
"""
from __future__ import annotations

from typing import Type

from rag_engine.connectors.base import SourceConnector
from rag_engine.connectors.file_loader import FileLoaderConnector


class ConnectorRegistry:
    def __init__(self):
        self._by_name: dict[str, Type[SourceConnector]] = {}

    def register(self, cls: Type[SourceConnector]) -> None:
        name = getattr(cls, "connector_name", None)
        if not name:
            raise ValueError(f"{cls.__name__} must set `connector_name`")
        self._by_name[name] = cls

    def get(self, name: str) -> Type[SourceConnector]:
        if name not in self._by_name:
            raise KeyError(
                f"unknown connector {name!r}; registered: {sorted(self._by_name)}"
            )
        return self._by_name[name]

    def names(self) -> list[str]:
        return sorted(self._by_name)


def _build_default() -> ConnectorRegistry:
    reg = ConnectorRegistry()
    reg.register(FileLoaderConnector)
    # Confluence / Notion register themselves when imported (avoid hard import
    # cost if their SDKs aren't installed). See connectors/confluence.py.
    try:
        from rag_engine.connectors.confluence import ConfluenceConnector  # noqa: WPS433
        reg.register(ConfluenceConnector)
    except Exception:
        pass
    try:
        from rag_engine.connectors.notion import NotionConnector  # noqa: WPS433
        reg.register(NotionConnector)
    except Exception:
        pass
    return reg


default_registry = _build_default()
