"""Event storage.

`JsonlEventStore` keeps one JSON event per line. Several processes (the FastAPI
server for the GUI and the MCP server for the planner) can share one file: writes
take an exclusive file lock, and every reader catches up on new lines first.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import Iterator, Protocol

from .models import Event


class EventStore(Protocol):
    def read_since(self, seq: int) -> list[Event]: ...
    def locked(self) -> contextlib.AbstractContextManager[None]: ...
    def append(self, events: list[Event]) -> None: ...


class MemoryEventStore:
    """In-process store, for tests and throwaway sessions."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = threading.RLock()

    def read_since(self, seq: int) -> list[Event]:
        return [e for e in self._events if e.seq > seq]

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    def append(self, events: list[Event]) -> None:
        self._events.extend(events)


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if os.name == "nt":  # pragma: no cover - Windows
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class JsonlEventStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = threading.RLock()
        self._offset = 0  # byte offset already parsed
        self._cache: list[Event] = []

    def _catch_up(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            while True:
                line = fh.readline()
                if not line or not line.endswith(b"\n"):
                    break  # EOF, or a line still being written
                self._offset += len(line)
                if line.strip():
                    self._cache.append(Event.model_validate_json(line))

    def read_since(self, seq: int) -> list[Event]:
        with self._thread_lock:
            self._catch_up()
            return [e for e in self._cache if e.seq > seq]

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        with self._thread_lock, _file_lock(self._lock_path):
            yield

    def append(self, events: list[Event]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = "".join(
            json.dumps(e.dump(), ensure_ascii=False) + "\n" for e in events
        ).encode("utf-8")
        with open(self.path, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
