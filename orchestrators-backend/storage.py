"""Where the task graph and memory are stored. The one place stores are built.

    TASKGRAPH_STORE  sqlite (default) | jsonl
    MEMORY_STORE     sqlite (default) | markdown
    DATABASE_PATH    SQLite file (default data/orchestrators.db), see db.py
    TASKGRAPH_PATH   JSONL event log (default data/taskgraph.jsonl)
    MEMORY_PATH      markdown memory directory (default data/memory)

With the SQLite backends, the first start imports TASKGRAPH_PATH / MEMORY_PATH
if they exist (once; the files are left in place). An import only runs into
empty tables and is recorded in `storage_imports`, so restarts never duplicate.

    from storage import open_taskgraph_store, open_memory_store
    taskgraph = TaskGraphService(open_taskgraph_store(db))

CLI (from orchestrators-backend/):

    python -m storage backup <dest>   consistent copy via SQLite's online backup API
    python -m storage info            row counts and imports
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from db import Database
from memory.sqlite_store import SqliteMemoryStore
from memory.store import MarkdownMemoryStore
from taskgraph.models import Event
from taskgraph.sqlite_store import SqliteEventStore, create_schema, join_tx
from taskgraph.store import EventStore, JsonlEventStore

log = logging.getLogger(__name__)

IMPORTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS storage_imports (
    source      TEXT PRIMARY KEY,   -- e.g. taskgraph-jsonl:/abs/path
    imported_at TEXT NOT NULL,
    items       INTEGER NOT NULL
);
"""

_default_db: Database | None = None
_default_lock = threading.Lock()


def default_db() -> Database:
    """One Database per process, for entry points that don't build their own."""
    global _default_db
    with _default_lock:
        if _default_db is None:
            _default_db = Database()
        return _default_db


def taskgraph_path() -> str:
    return os.environ.get("TASKGRAPH_PATH", "data/taskgraph.jsonl")


def memory_path() -> str:
    return os.environ.get("MEMORY_PATH", "data/memory")


def _choice(var: str, options: tuple[str, ...]) -> str:
    value = os.environ.get(var, options[0]).strip().lower()
    if value not in options:
        raise ValueError(f"{var}={value!r}; expected one of {', '.join(options)}")
    return value


def open_taskgraph_store(db: Database | None = None) -> EventStore:
    if _choice("TASKGRAPH_STORE", ("sqlite", "jsonl")) == "jsonl":
        return JsonlEventStore(taskgraph_path())
    store = SqliteEventStore(db or default_db())
    import_jsonl(store, taskgraph_path())
    return store


def open_memory_store(db: Database | None = None) -> MarkdownMemoryStore | SqliteMemoryStore:
    if _choice("MEMORY_STORE", ("sqlite", "markdown")) == "markdown":
        return MarkdownMemoryStore(memory_path())
    store = SqliteMemoryStore(db or default_db())
    import_markdown(store, memory_path())
    return store


# ----------------------------------------------------------------------
# one-time imports
# ----------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _should_import(conn: sqlite3.Connection, source: str, table: str) -> bool:
    if conn.execute("SELECT 1 FROM storage_imports WHERE source = ?", (source,)).fetchone():
        return False
    if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
        log.warning("not importing %s: %s already has data", source, table)
        return False
    return True


def _record(conn: sqlite3.Connection, source: str, items: int) -> None:
    conn.execute("INSERT INTO storage_imports VALUES (?, ?, ?)", (source, _now(), items))


def import_jsonl(store: SqliteEventStore, path: str | os.PathLike[str]) -> int:
    """Copy a JSONL event log into an empty events table. Returns events imported."""
    path = Path(path)
    if not path.is_file():
        return 0
    source = f"taskgraph-jsonl:{path.resolve()}"
    create_schema(store.db, IMPORTS_SCHEMA)
    with join_tx(store.db) as conn:
        if not _should_import(conn, source, "taskgraph_events"):
            return 0
        # Same parsing as JsonlEventStore: complete lines only, blanks skipped.
        events: list[Event] = []
        with open(path, "rb") as fh:
            for line in fh:
                if line.endswith(b"\n") and line.strip():
                    events.append(Event.model_validate_json(line))
        store.append(events)
        _record(conn, source, len(events))
    log.info("imported %d task graph events from %s", len(events), path)
    return len(events)


def import_markdown(store: SqliteMemoryStore, root: str | os.PathLike[str]) -> int:
    """Copy markdown memories and log.jsonl into empty memory tables."""
    md = MarkdownMemoryStore(root)
    if not md.root.is_dir():
        return 0
    source = f"memory-markdown:{md.root.resolve()}"
    create_schema(store.db, IMPORTS_SCHEMA)
    with join_tx(store.db) as conn:
        if not _should_import(conn, source, "memories"):
            return 0
        memories = md.load()
        for m in memories.values():
            store.write(m)
        store.log_many(md.read_log(limit=0))
        _record(conn, source, len(memories))
    log.info("imported %d memories from %s", len(memories), root)
    return len(memories)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def backup(dest: str | os.PathLike[str], src: str | None = None) -> Path:
    """Online, consistent copy of the database (safe while the app runs)."""
    src = src or Database().path
    dest = Path(dest)
    if dest.is_dir():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = dest / f"orchestrators-{stamp}.db"
    if dest.exists():
        raise FileExistsError(f"{dest} exists; pick a new name")
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{Path(src).resolve()}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return dest


def info(db: Database) -> dict[str, object]:
    out: dict[str, object] = {"path": db.path}
    with db.read() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            out[t] = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        if "storage_imports" in tables:
            out["imports"] = [dict(r) for r in conn.execute("SELECT * FROM storage_imports")]
    return out


def main(argv: list[str]) -> int:
    usage = "usage: python -m storage backup <dest> | info"
    if argv[:1] == ["backup"] and len(argv) == 2:
        print(backup(argv[1]))
        return 0
    if argv == ["info"]:
        for k, v in info(Database()).items():
            print(f"{k}: {v}")
        return 0
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
