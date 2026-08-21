"""SQLite-backed structured store. Not implemented."""

from __future__ import annotations

from pathlib import Path


class SqliteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def connect(self):
        raise NotImplementedError("SqliteStore not wired yet")
