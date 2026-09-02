"""Fail-closed SQLite helpers for repository and dashboard read paths."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite database without creating or mutating it."""
    path = Path(db_path)
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return whether ``table`` exists without changing the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None
