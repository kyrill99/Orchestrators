"""Ephemeral teammate presence: who is where, and who is typing.

In memory only (one uvicorn worker). Browsers POST a heartbeat every few
seconds:

    POST /api/presence  {"where": "agent" | "task:<id>" | ..., "typing": bool,
                         "conversation_id": str | null,
                         # optional, for multiplayer (see multiplayer.py):
                         "selection": task id selected on a graph | null,
                         "editing": field being edited (soft lock) | null,
                         "route": page path, for follow mode | null}

and get back every active entry (the caller included; the client filters
itself out). GET /api/presence returns the same list. Entries not refreshed
for TTL seconds disappear. Changes are also published on the broker channel
"presence" (event "presence", data = full list) for SSE listeners.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from identity import current_user

CHANNEL = "presence"
TTL = 15.0
# Optional multiplayer fields stored as given (strings, clipped).
EXTRA_FIELDS = ("selection", "editing", "route")


class PresenceStore:
    def __init__(
        self,
        ttl: float = TTL,
        clock: Callable[[], float] = time.time,
        on_change: Callable[[list[dict[str, Any]]], None] | None = None,
    ):
        self._ttl = ttl
        self._clock = clock
        self._on_change = on_change
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> bool:
        stale = [
            k for k, e in self._entries.items() if now - e["updated_at"] > self._ttl
        ]
        for k in stale:
            del self._entries[k]
        return bool(stale)

    def _snapshot(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(e) for e in self._entries.values()), key=lambda e: e["email"]
        )

    def update(
        self,
        email: str,
        name: str,
        where: str,
        typing: bool = False,
        conversation_id: str | None = None,
        project: str = "default",
        **extra: str | None,
    ) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            pruned = self._prune(now)
            old = self._entries.get(email)
            entry = {
                "email": email,
                "name": name,
                "where": where[:200],
                "typing": bool(typing),
                "conversation_id": conversation_id,
                "project": project,
                "updated_at": now,
            }
            for key in EXTRA_FIELDS:
                value = extra.get(key)
                entry[key] = str(value)[:500] if value else None
            changed = pruned or old is None or any(
                old.get(k) != entry[k]
                for k in ("where", "typing", "conversation_id", "project", *EXTRA_FIELDS)
            )
            self._entries[email] = entry
            snapshot = self._snapshot()
        if changed and self._on_change is not None:
            self._on_change(snapshot)
        return snapshot

    def leave(self, email: str) -> list[dict[str, Any]]:
        with self._lock:
            existed = self._entries.pop(email, None) is not None
            self._prune(self._clock())
            snapshot = self._snapshot()
        if existed and self._on_change is not None:
            self._on_change(snapshot)
        return snapshot

    def active(self) -> list[dict[str, Any]]:
        with self._lock:
            self._prune(self._clock())
            return self._snapshot()


# Module level, not inside build_router: with `from __future__ import
# annotations` FastAPI resolves `body: Heartbeat` in module globals, and a
# local class made POST /api/presence answer 422 to every heartbeat.
class Heartbeat(BaseModel):
    where: str = ""
    typing: bool = False
    conversation_id: str | None = None
    selection: str | None = None
    editing: str | None = None
    route: str | None = None


def build_router(store: PresenceStore):
    from projects import current_project

    r = APIRouter(prefix="/api", tags=["presence"])

    @r.get("/presence")
    def list_presence():
        return store.active()

    @r.post("/presence")
    def heartbeat(body: Heartbeat, request: Request):
        user = current_user(request)
        if not body.where:
            return store.leave(user.email)
        return store.update(
            user.email,
            user.name,
            body.where,
            body.typing,
            body.conversation_id,
            project=current_project.get(),
            **{k: getattr(body, k) for k in EXTRA_FIELDS},
        )

    return r
