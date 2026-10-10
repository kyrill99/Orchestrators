"""Multiplayer: live cursors, follow-mode viewports and broker events over
one WebSocket per tab.

Mount with:

    app.include_router(build_router(broker))

Presence itself (who is where, typing, selection, soft edit locks, route) is
presence.py: HTTP heartbeats, published on the "presence" broker channel.
This module adds what heartbeats are too slow for:

    WS /api/multiplayer/ws

      client -> server
        {"t": "join", "location": "graph" | "task:<id>" | "conversation:<id>" | ...}
        {"t": "cursor", "location", "x", "y"} | {"t": "cursor", "location", "hidden": true}
        {"t": "viewport", "location", "x", "y", "zoom"}
        {"t": "subscribe", "channels": ["graph", "presence", ...]}
        {"t": "ping"}

      server -> client
        {"t": "welcome", "session", "user"}
        {"t": "cursor" | "viewport", "session", "email", "name", "location", ...}
        {"t": "leave", "session"}                        drop that session's cursor
        {"t": "event", "channel", "type", "id", "data"}  forwarded broker event

Identity is identity.current_user on the upgrade request (oauth2-proxy sets
X-Forwarded-Email). Cursor and viewport frames go only to the other
connections in the same location (a room). Each connection has an outbox:
reliable messages queue (bounded; a client that falls too far behind is
disconnected and reconnects), lossy frames keep only the newest per sender
and kind, so a slow client gets fewer, fresher frames instead of a backlog.
Frames arriving faster than MIN_FRAME_INTERVAL per sender and kind are
dropped. In memory, one uvicorn worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from fastapi import APIRouter, WebSocket

from events import Broker
from identity import User, current_user

MIN_FRAME_INTERVAL = 0.025  # 40 Hz cap per sender and kind; clients send ~20 Hz
OUTBOX_LIMIT = 500
MAX_TEXT = 200
LOSSY = ("cursor", "viewport")


class Outbox:
    """Per-connection send buffer: reliable messages in order, lossy frames
    newest-only per key. Never blocks the sender."""

    def __init__(self, limit: int = OUTBOX_LIMIT):
        self.limit = limit
        self.reliable: deque[dict] = deque()
        self.latest: dict[tuple, dict] = {}
        self.overflowed = False
        self.ready = asyncio.Event()

    def send(self, message: dict) -> None:
        if len(self.reliable) >= self.limit:
            self.overflowed = True
        else:
            self.reliable.append(message)
        self.ready.set()

    def send_latest(self, key: tuple, message: dict) -> None:
        self.latest.pop(key, None)  # re-insert so order follows recency
        self.latest[key] = message
        self.ready.set()

    def forget(self, predicate: Callable[[tuple], bool]) -> None:
        for key in [k for k in self.latest if predicate(k)]:
            del self.latest[key]

    def drain(self) -> list[dict]:
        out = list(self.reliable) + list(self.latest.values())
        self.reliable.clear()
        self.latest.clear()
        self.ready.clear()
        return out


@dataclass(eq=False)
class Connection:
    id: str
    user: User
    location: str = ""
    outbox: Outbox = dc_field(default_factory=Outbox)
    # Last forwarded lossy frame per kind, for throttling.
    last_frame: dict[str, float] = dc_field(default_factory=dict)


class Hub:
    """Live connections and room fan-out."""

    def __init__(
        self,
        min_interval: float = MIN_FRAME_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.min_interval = min_interval
        self.clock = clock
        self.connections: dict[str, Connection] = {}

    def add(self, conn: Connection) -> None:
        self.connections[conn.id] = conn

    def remove(self, conn: Connection) -> None:
        if self.connections.get(conn.id) is conn:
            del self.connections[conn.id]
            self._left(conn)

    def join(self, conn: Connection, location: str) -> None:
        location = str(location or "")[:MAX_TEXT]
        if location != conn.location:
            self._left(conn)
            conn.location = location

    def _left(self, conn: Connection) -> None:
        """Others in the old room drop this connection's cursor."""
        for other in self.room(conn.location):
            if other is not conn:
                other.outbox.forget(lambda key: key[1] == conn.id)
                other.outbox.send({"t": "leave", "session": conn.id})

    def room(self, location: str) -> list[Connection]:
        if not location:
            return []
        return [c for c in self.connections.values() if c.location == location]

    def frame(self, sender: Connection, message: dict) -> bool:
        """Fan a lossy frame out to the sender's room. False if dropped."""
        kind = str(message.get("t"))
        location = str(message.get("location") or sender.location)[:MAX_TEXT]
        if location != sender.location:
            return False  # only send into the room you joined
        now = self.clock()
        if now - sender.last_frame.get(kind, float("-inf")) < self.min_interval:
            return False
        sender.last_frame[kind] = now
        out = {
            k: v
            for k, v in message.items()
            if k not in ("t", "session", "email", "name", "location") and _small(v)
        }
        out.update(
            t=kind,
            session=sender.id,
            email=sender.user.email,
            name=sender.user.name,
            location=location,
        )
        for peer in self.room(location):
            if peer is not sender:
                peer.outbox.send_latest((kind, sender.id), out)
        return True


def _small(value: Any) -> bool:
    if isinstance(value, (int, float, bool)) or value is None:
        return True
    return isinstance(value, str) and len(value) <= MAX_TEXT


def build_router(broker: Broker, hub: Hub | None = None) -> APIRouter:
    hub = hub or Hub()
    r = APIRouter(prefix="/api", tags=["multiplayer"])

    @r.websocket("/multiplayer/ws")
    async def ws(websocket: WebSocket):
        user = current_user(websocket)  # type: ignore[arg-type] — only reads headers
        await websocket.accept()
        conn = Connection(uuid.uuid4().hex[:16], user)
        hub.add(conn)
        box = conn.outbox
        sub = broker.subscribe([])  # channels come from "subscribe" messages
        box.send({"t": "welcome", "session": conn.id, "user": user.model_dump()})

        async def pump_events():
            while True:
                event = await sub.queue.get()
                if event.type == "resync":
                    sub.overflowed = False
                box.send(
                    {
                        "t": "event",
                        "channel": event.channel,
                        "type": event.type,
                        "id": event.id,
                        "data": event.data,
                    }
                )

        async def send_loop():
            while True:
                await box.ready.wait()
                if box.overflowed:
                    await websocket.close(code=1013)  # try again later
                    return
                for message in box.drain():
                    await websocket.send_text(json.dumps(message, default=str))

        async def receive_loop():
            while True:
                try:
                    message = json.loads(await websocket.receive_text())
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                kind = message.get("t")
                if kind == "join":
                    hub.join(conn, message.get("location") or "")
                elif kind in LOSSY:
                    hub.frame(conn, message)
                elif kind == "subscribe":
                    sub.channels = frozenset(
                        str(c)[:50] for c in message.get("channels") or [] if isinstance(c, str)
                    )

        tasks = [
            asyncio.create_task(pump_events()),
            asyncio.create_task(send_loop()),
            asyncio.create_task(receive_loop()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            broker.unsubscribe(sub)
            hub.remove(conn)
            with contextlib.suppress(Exception):
                await websocket.close()

    r.hub = hub  # type: ignore[attr-defined] — for tests
    return r
