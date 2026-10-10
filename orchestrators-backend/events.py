"""In-process pub/sub with a Server-Sent Events helper.

Any module can broadcast live updates to browsers:

    broker = Broker()
    broker.publish("conversations", "message", {"id": 1, ...})

    @router.get("/api/things/stream")
    async def stream(request: Request):
        return sse_response(broker, request, ["things"])

Events are keyed by channel, so one stream can carry several channels and
other features (task activity, graph changes) can share the same broker.

Guarantees:
- publish() never blocks: each subscriber has a bounded queue. A subscriber
  that falls behind has its queue replaced by one "resync" event, telling the
  client to re-fetch state instead of holding up everyone else.
- Every event has an id "<boot>.<seq>". Browsers send it back as
  Last-Event-ID when EventSource reconnects; recent events (a ring buffer)
  are replayed. If the id is too old or from an earlier process, the client
  gets "resync" instead.
- The stream sends a comment every HEARTBEAT seconds so proxies keep it open,
  and disables proxy buffering.

The broker is in-process: it assumes one uvicorn worker. publish() is safe to
call from worker threads (it hops onto the event loop).
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

HEARTBEAT = 15.0
QUEUE_SIZE = 256
HISTORY = 1000
RESYNC = "resync"


@dataclass(frozen=True)
class Event:
    seq: int
    id: str
    channel: str
    type: str
    data: Any

    def encode(self) -> str:
        payload = json.dumps(self.data, separators=(",", ":"), default=str)
        return f"id: {self.id}\nevent: {self.type}\ndata: {payload}\n\n"


@dataclass(eq=False)
class Subscriber:
    channels: frozenset[str]
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    overflowed: bool = False


class Broker:
    def __init__(self, history: int = HISTORY, queue_size: int = QUEUE_SIZE):
        self.boot = uuid.uuid4().hex[:8]
        self._seq = 0
        self._history: deque[Event] = deque(maxlen=history)
        self._subs: set[Subscriber] = set()
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- publishing ---------------------------------------------------------

    def publish(self, channel: str, type: str, data: Any) -> Event:
        """Broadcast one event. Never blocks; thread-safe."""
        with self._lock:
            self._seq += 1
            event = Event(self._seq, f"{self.boot}.{self._seq}", channel, type, data)
            self._history.append(event)
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._deliver, event)
        else:
            self._deliver(event)
        return event

    def _deliver(self, event: Event) -> None:
        for sub in list(self._subs):
            if event.channel in sub.channels:
                self._offer(sub, event)

    def _offer(self, sub: Subscriber, event: Event) -> None:
        if sub.overflowed:
            return
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Too slow: drop its backlog and ask it to re-fetch.
            sub.overflowed = True
            while not sub.queue.empty():
                sub.queue.get_nowait()
            sub.queue.put_nowait(self._resync_event())

    def _resync_event(self) -> Event:
        with self._lock:
            seq = self._seq
        return Event(seq, f"{self.boot}.{seq}", "", RESYNC, {})

    # -- subscribing --------------------------------------------------------

    def subscribe(
        self, channels: Iterable[str], last_event_id: str | None = None
    ) -> Subscriber:
        """Must be called on the event loop. Replays events after last_event_id."""
        self._loop = asyncio.get_running_loop()
        sub = Subscriber(frozenset(channels), asyncio.Queue(self._queue_size))
        if last_event_id:
            replay = self._since(last_event_id)
            if replay is None:
                sub.queue.put_nowait(self._resync_event())
            else:
                for event in replay:
                    if event.channel in sub.channels:
                        self._offer(sub, event)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def _since(self, last_event_id: str) -> list[Event] | None:
        boot, _, seq_text = last_event_id.partition(".")
        if boot != self.boot or not seq_text.isdigit():
            return None
        seq = int(seq_text)
        with self._lock:
            history = list(self._history)
            current = self._seq
        if seq > current:
            return None
        oldest = history[0].seq if history else current + 1
        if seq < oldest - 1:
            return None  # gap: some events were evicted
        return [e for e in history if e.seq > seq]

    # -- streaming ----------------------------------------------------------

    async def stream(
        self,
        request: Request,
        channels: Iterable[str],
        last_event_id: str | None = None,
        heartbeat: float = HEARTBEAT,
    ) -> AsyncIterator[str]:
        sub = self.subscribe(channels, last_event_id)
        try:
            # Tell EventSource to reconnect after 3 s, and flush headers now.
            yield "retry: 3000\n: connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), heartbeat)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                if event.type == RESYNC:
                    sub.overflowed = False
                yield event.encode()
        finally:
            self.unsubscribe(sub)


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def sse_response(
    broker: Broker,
    request: Request,
    channels: Iterable[str],
    heartbeat: float = HEARTBEAT,
) -> StreamingResponse:
    """A text/event-stream response for the given channels.

    Honors the Last-Event-ID header (sent by EventSource on reconnect) or a
    last_event_id query parameter (for clients that open a fresh EventSource).
    """
    last = request.headers.get("last-event-id") or request.query_params.get(
        "last_event_id"
    )
    return StreamingResponse(
        broker.stream(request, list(channels), last, heartbeat),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


class PublishingEventStore:
    """Wraps an append-only event store (e.g. the task graph's) and publishes
    every appended event on a channel. Other methods pass through.

    Only appends made in this process are seen; writes from another process
    (the MCP server) are not broadcast.
    """

    def __init__(self, inner: Any, broker: Broker, channel: str, type: str = "event"):
        self._inner = inner
        self._broker = broker
        self._channel = channel
        self._type = type

    def append(self, events: list) -> None:
        self._inner.append(events)
        for event in events:
            dump = getattr(event, "dump", None) or getattr(event, "model_dump", None)
            self._broker.publish(self._channel, self._type, dump() if dump else event)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def build_router(broker: Broker, channels: Iterable[str]) -> APIRouter:
    """GET /api/events?channel=a&channel=b: one SSE stream for any of `channels`
    (all of them when none is given)."""
    allowed = frozenset(channels)
    r = APIRouter(prefix="/api", tags=["events"])

    @r.get("/events")
    async def events(request: Request, channel: list[str] = Query(default=[])):
        wanted = [c for c in channel if c in allowed] or sorted(allowed)
        return sse_response(broker, request, wanted)

    return r
