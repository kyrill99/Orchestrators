"""Memory storage: one markdown file per memory, plus an append-only log.

    <root>/global/<kind>/<slug>.md
    <root>/users/<userId>/<kind>/<slug>.md
    <root>/projects/<projectId>/<kind>/<slug>.md
    <root>/log.jsonl

Frontmatter is one `key: <json value>` per line, so files stay readable and
editable by hand without a YAML dependency. Several processes (the FastAPI
server and the MCP server) can share one directory: writers take a file lock
and every read goes to disk.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterator

from taskgraph.store import _file_lock

from .models import Memory

FENCE = "---"


def render(memory: Memory) -> str:
    meta = memory.dump()
    body = meta.pop("body")
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()]
    return "\n".join([FENCE, *lines, FENCE, "", body.strip(), ""])


def parse(text: str) -> Memory | None:
    """None for files without frontmatter (e.g. a README dropped in the folder)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None
    meta: dict[str, Any] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == FENCE:
            meta["body"] = "\n".join(lines[i + 1 :]).strip()
            return Memory.model_validate(meta)
        key, _, value = line.partition(":")
        meta[key.strip()] = json.loads(value)
    return None


class MarkdownMemoryStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self._lock_path = self.root / ".lock"
        self._log_path = self.root / "log.jsonl"
        self._thread_lock = threading.Lock()

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusive across threads and processes. Not reentrant."""
        with self._thread_lock, _file_lock(self._lock_path):
            yield

    def load(self) -> dict[str, Memory]:
        out: dict[str, Memory] = {}
        if not self.root.exists():
            return out
        for path in sorted(self.root.rglob("*.md")):
            memory = parse(path.read_text(encoding="utf-8"))
            if memory is not None:
                out[memory.id] = memory
        return out

    def write(self, memory: Memory) -> None:
        path = self.root / memory.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(render(memory), encoding="utf-8")
        os.replace(tmp, path)

    def log(self, entry: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_log(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._log_path.exists():
            return []
        lines = self._log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()]
