"""Engine + path helpers for the BI warehouse SQLite file."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DEFAULT_PATH = "data/bi_warehouse.db"


def warehouse_path() -> Path:
    return Path(os.getenv("BI_DB_PATH", DEFAULT_PATH))


def create_writable_engine(path: Path | None = None):
    p = path or warehouse_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{p}", future=True)
    return engine, sessionmaker(engine, expire_on_commit=False)
