"""Task graph events in the shared SQLite database (see db.py).

`SqliteEventStore` has the same interface as `JsonlEventStore`. The table
`taskgraph_events` is the append-only history of graph updates; the service
replays it into memory on start and reads only newer rows afterwards
(`read_since(state.seq)`), so several processes (the FastAPI app and the MCP
server) stay in sync just like with the shared JSONL file.

`locked()` is a write transaction (BEGIN IMMEDIATE): it excludes writers in
other threads and processes, and `append` inside it commits together with
everything else in the block. That replaces the JSONL store's file lock.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator

from db import Database

from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS taskgraph_events (
    seq          INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    actor_type   TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    type         TEXT NOT NULL,
    payload      TEXT NOT NULL,   -- JSON object
    task_ids     TEXT NOT NULL,   -- JSON array
    changeset_id TEXT,
    reason       TEXT,
    summary      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS taskgraph_events_changeset ON taskgraph_events(changeset_id);
CREATE INDEX IF NOT EXISTS taskgraph_events_type ON taskgraph_events(type);
"""


@contextlib.contextmanager
def join_tx(db: Database) -> Iterator[sqlite3.Connection]:
    """A write transaction, or the one this thread already has open.

    Database.tx() is not reentrant; stores call this instead so that, e.g., a
    memory write inside the memory store's lock joins that transaction.
    """
    with db.read() as conn:  # holds the connection lock, so no other thread is mid-tx
        if conn.in_transaction:
            yield conn
            return
        with db.tx() as conn:
            yield conn


def _row(e: Event) -> tuple:
    d = e.dump()  # JSON-ready, same as a JSONL line
    return (
        e.seq,
        e.ts,
        e.actor.type,
        e.actor.id,
        e.type,
        json.dumps(d["payload"], ensure_ascii=False),
        json.dumps(d["taskIds"], ensure_ascii=False),
        e.changeset_id,
        e.reason,
        e.summary,
    )


def _event(r: sqlite3.Row) -> Event:
    return Event(
        seq=r["seq"],
        ts=r["ts"],
        actor={"type": r["actor_type"], "id": r["actor_id"]},
        type=r["type"],
        payload=json.loads(r["payload"]),
        task_ids=json.loads(r["task_ids"]),
        changeset_id=r["changeset_id"],
        reason=r["reason"],
        summary=r["summary"],
    )


class SqliteEventStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        create_schema(db, SCHEMA)

    def read_since(self, seq: int) -> list[Event]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM taskgraph_events WHERE seq > ? ORDER BY seq", (seq,)
            ).fetchall()
        return [_event(r) for r in rows]

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        with join_tx(self.db):
            yield

    def append(self, events: list[Event]) -> None:
        if not events:
            return
        with join_tx(self.db) as conn:
            conn.executemany(
                "INSERT INTO taskgraph_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_row(e) for e in events],
            )

    def count(self) -> int:
        with self.db.read() as conn:
            return conn.execute("SELECT count(*) FROM taskgraph_events").fetchone()[0]


def create_schema(db: Database, schema: str) -> None:
    """Run `;`-separated CREATE ... IF NOT EXISTS statements in one transaction.
    (executescript() would commit the open transaction, so run them one by one.)"""
    with join_tx(db) as conn:
        for statement in schema.split(";"):
            if statement.strip():
                conn.execute(statement)
