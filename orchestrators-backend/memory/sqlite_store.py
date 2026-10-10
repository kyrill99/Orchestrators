"""Memory in the shared SQLite database (see db.py).

`SqliteMemoryStore` has the same interface and behaviour as
`MarkdownMemoryStore`: `load()` returns memories in the same order (sorted by
their markdown path), bodies are stored stripped like the markdown files, and
`read_log()` returns the last `limit` log entries oldest first.

    memories    one row per memory; `data` is the full memory as JSON, the
                other columns are copies for querying by hand
    memory_log  the append-only log (used / created / updated / feedback ...)

`locked()` is a write transaction (BEGIN IMMEDIATE), exclusive across threads
and processes; writes inside it commit together.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterator

from db import Database
from taskgraph.sqlite_store import create_schema, join_tx

from .models import Memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    scope      TEXT NOT NULL,
    project_id TEXT,
    user_id    TEXT,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL,
    confidence REAL NOT NULL,
    updated_at TEXT,
    data       TEXT NOT NULL   -- Memory.dump() as JSON, including the body
);
CREATE TABLE IF NOT EXISTS memory_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT,
    event TEXT,
    data  TEXT NOT NULL        -- the whole entry as JSON
);
"""


class SqliteMemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        create_schema(db, SCHEMA)

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusive across threads and processes."""
        with join_tx(self.db):
            yield

    def load(self) -> dict[str, Memory]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT data FROM memories").fetchall()
        memories = [Memory.model_validate_json(r["data"]) for r in rows]
        # Same order as MarkdownMemoryStore, which reads files sorted by path.
        memories.sort(key=lambda m: PurePosixPath(m.path))
        return {m.id: m for m in memories}

    def write(self, memory: Memory) -> None:
        # The markdown store strips the body when it renders the file.
        m = memory.model_copy(update={"body": memory.body.strip()})
        with join_tx(self.db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m.id, m.scope, m.project_id, m.user_id, m.kind, m.title, m.status,
                 m.confidence, m.updated_at, json.dumps(m.dump(), ensure_ascii=False)),
            )

    def log(self, entry: dict[str, Any]) -> None:
        self.log_many([entry])

    def log_many(self, entries: list[dict[str, Any]]) -> None:
        with join_tx(self.db) as conn:
            conn.executemany(
                "INSERT INTO memory_log (ts, event, data) VALUES (?, ?, ?)",
                [(e.get("ts"), e.get("event"), json.dumps(e, ensure_ascii=False))
                 for e in entries],
            )

    def read_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.read() as conn:
            if limit > 0:
                rows = conn.execute(
                    "SELECT data FROM memory_log ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()[::-1]
            else:  # match the markdown store's lines[-limit:] for 0 / negative
                rows = conn.execute("SELECT data FROM memory_log ORDER BY id").fetchall()
                rows = rows[-limit:]
        return [json.loads(r["data"]) for r in rows]

    def count(self) -> int:
        with self.db.read() as conn:
            return conn.execute("SELECT count(*) FROM memories").fetchone()[0]
