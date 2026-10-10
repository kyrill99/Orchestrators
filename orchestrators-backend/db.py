"""Local SQLite database shared by the backend modules.

One file (DATABASE_PATH, default data/orchestrators.db) in WAL mode, so the
FastAPI app and the MCP server process can use it at the same time. Each
module owns its tables and creates them itself (CREATE TABLE IF NOT EXISTS)
when it is constructed. Share one Database per process:

    db = Database()
    with db.tx() as conn:      # write transaction (BEGIN IMMEDIATE)
        conn.execute("INSERT ...")
    with db.read() as conn:    # plain reads
        conn.execute("SELECT ...").fetchall()
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PATH = "data/orchestrators.db"


def database_path() -> str:
    return os.environ.get("DATABASE_PATH", DEFAULT_PATH)


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or database_path())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # One connection guarded by a lock: FastAPI runs sync endpoints in a
        # thread pool, and the MCP server calls tools from worker threads.
        self._conn = sqlite3.connect(
            self.path, timeout=10, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        for pragma in (
            "journal_mode=WAL",
            "busy_timeout=10000",
            "foreign_keys=ON",
            "synchronous=NORMAL",
        ):
            self._conn.execute(f"PRAGMA {pragma}")
        self._lock = threading.RLock()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
