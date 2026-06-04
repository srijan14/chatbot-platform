"""Connector package.

Keep this thin: submodules (`file_loader`, `confluence`, `notion`) all
register themselves in `registry.default_registry`. Importing the package
imports the registry, which triggers those registrations.
"""
from rag_engine.connectors.base import DocRef, SourceConnector
from rag_engine.connectors.registry import ConnectorRegistry, default_registry

__all__ = ["SourceConnector", "DocRef", "ConnectorRegistry", "default_registry"]
