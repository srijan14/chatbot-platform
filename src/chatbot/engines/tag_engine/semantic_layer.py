"""Parse the semantic-layer YAML into typed records.

The YAML is the source of truth for:
  • which warehouse file we open (database.path)
  • which tables exist and how the LlamaIndex retriever describes them
  • the list of metrics + dimensions the `list_business_metrics` tool returns
  • a few NL → SQL examples we slot into the SQL generator's prompt
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class TableDoc:
    name: str
    description: str


@dataclass
class NamedDoc:
    name: str
    description: str


@dataclass
class FewShot:
    question: str
    sql: str


@dataclass
class SemanticLayer:
    database_path: Path
    dialect: str
    tables: list[TableDoc] = field(default_factory=list)
    metrics: list[NamedDoc] = field(default_factory=list)
    dimensions: list[NamedDoc] = field(default_factory=list)
    few_shot_examples: list[FewShot] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SemanticLayer":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        db = raw.get("database", {}) or {}
        return cls(
            database_path=Path(db.get("path", "data/bi_warehouse.db")),
            dialect=db.get("dialect", "sqlite"),
            tables=[TableDoc(t["name"], t.get("description", "").strip())
                    for t in raw.get("tables", []) or []],
            metrics=[NamedDoc(m["name"], m.get("description", "").strip())
                     for m in raw.get("metrics", []) or []],
            dimensions=[NamedDoc(d["name"], d.get("description", "").strip())
                        for d in raw.get("dimensions", []) or []],
            few_shot_examples=[FewShot(e["question"].strip(), e["sql"].strip())
                               for e in raw.get("few_shot_examples", []) or []],
        )

    def summary_for_user(self) -> str:
        """The plain-English `list_business_metrics` payload."""
        lines = ["Available metrics:"]
        for m in self.metrics:
            lines.append(f"  - {m.name}: {m.description}")
        lines.append("\nAvailable dimensions:")
        for d in self.dimensions:
            lines.append(f"  - {d.name}: {d.description}")
        return "\n".join(lines)
