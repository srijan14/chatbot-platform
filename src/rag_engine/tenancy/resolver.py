"""Tenant -> physical collection name resolution.

Two reasons this is a single tiny module:
  - centralizes the naming convention so callers can't accidentally bypass it
  - tests can monkeypatch in a fake convention without poking every site
"""
from __future__ import annotations

import re

_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,62}$")


def validate_identifier(s: str, label: str) -> None:
    """Reject names that would land us in URL/SQL/Chroma escaping trouble."""
    if not s or not _SAFE.match(s):
        raise ValueError(
            f"invalid {label} {s!r}: must match {_SAFE.pattern}"
        )


def physical_collection_name(tenant_id: str, logical_name: str) -> str:
    validate_identifier(tenant_id, "tenant_id")
    validate_identifier(logical_name, "collection name")
    return f"{tenant_id}__{logical_name}"
