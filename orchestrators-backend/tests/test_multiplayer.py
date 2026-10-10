import asyncio
import json
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

from events import Broker  # noqa: E402
from events import build_router as build_events_router  # noqa: E402
from identity import User  # noqa: E402
from multiplayer import Connection, Hub, Outbox, build_router  # noqa: E402
from presence import CHANNEL as PRESENCE_CHANNEL  # noqa: E402
from presence import PresenceStore  # noqa: E402
from presence import build_router as build_presence_router  # noqa: E402

ALICE = User(email="alice@example.com", name="alice")
BOB = User(email="bob@example.com", name="bob")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class HubTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.hub = Hub(min_interval=0.025, clock=self.clock)
        self.a = Connection("a", ALICE)
        self.b = Connection("b", BOB)
        self.c = Connection("c", BOB)
        for conn, loc in ((self.a, "graph"), (self.b, "graph"), (self.c, "task:t1")):
            self.hub.add(conn)
            self.hub.join(conn, loc)

    async def test_room_fan_out(self):
        self.assertTrue(self.hub.frame(self.a, {"t": "cursor", "location": "graph", "x": 1, "y": 2}))
        got = self.b.outbox.drain()
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["email"], got[0]["x"], got[0]["y"]), (ALICE.email, 1, 2))
        self.assertEqual(self.a.outbox.drain(), [])  # not echoed
        self.assertEqual(self.c.outbox.drain(), [])  # other room
        self.clock.now += 1
        # Frames for a room you have not joined are refused.
        self.assertFalse(self.hub.frame(self.a, {"t": "cursor", "location": "task:t1", "x": 0, "y": 0}))

    async def test_throttle_and_stale_frames(self):
        sent = 0
        for i in range(10):  # 10 frames 10 ms apart: the 40 Hz cap lets every third through
            sent += self.hub.frame(self.a, {"t": "cursor", "location": "graph", "x": i, "y": 0})
            self.clock.now += 0.01
        self.assertEqual(sent, 4)
        # b never drained (a slow client): it only holds the newest frame.
        self.assertEqual([f["x"] for f in self.b.outbox.drain()], [9])

    async def test_kinds_are_throttled_separately(self):
        self.assertTrue(self.hub.frame(self.a, {"t": "cursor", "location": "graph", "x": 1, "y": 1}))
        self.assertTrue(self.hub.frame(self.a, {"t": "viewport", "location": "graph", "x": 0, "y": 0, "zoom": 1}))
        self.assertEqual(len(self.b.outbox.drain()), 2)

    async def test_leaving_drops_cursor_and_notifies_room(self):
        self.hub.frame(self.a, {"t": "cursor", "location": "graph", "x": 1, "y": 1})
        self.hub.join(self.a, "task:t1")  # moving rooms counts as leaving
        self.assertEqual(self.b.outbox.drain(), [{"t": "leave", "session": "a"}])
        self.hub.remove(self.a)
        self.assertEqual(self.c.outbox.drain(), [{"t": "leave", "session": "a"}])

    async def test_outbox_keeps_reliable_and_overflows(self):
        box = Outbox(limit=3)
        for i in range(3):
            box.send({"n": i})
        box.send_latest(("cursor", "x"), {"x": 1})
        box.send_latest(("cursor", "x"), {"x": 2})
        self.assertFalse(box.overflowed)
        self.assertEqual(box.drain(), [{"n": 0}, {"n": 1}, {"n": 2}, {"x": 2}])
        for i in range(4):
            box.send({"n": i})
        self.assertTrue(box.overflowed)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerTest(unittest.TestCase):
    """Real uvicorn: WebSocket round trip, and presence over the SSE fallback."""

    def setUp(self):
        self.broker = Broker()
        app = FastAPI()
        app.include_router(build_router(self.broker))
        app.include_router(build_events_router(self.broker, [PRESENCE_CHANNEL, "graph"]))
        app.include_router(
            build_presence_router(
                PresenceStore(on_change=lambda e: self.broker.publish(PRESENCE_CHANNEL, "presence", e))
            )
        )
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        self.base = f"127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(5)

    def test_websocket_round_trip_between_two_users(self):
        async def until(ws, predicate):
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if predicate(message):
                    return message

        async def go():
            url = f"ws://{self.base}/api/multiplayer/ws"
            async with connect(url, additional_headers={"X-Forwarded-Email": "alice@example.com"}) as a, \
                    connect(url, additional_headers={"X-Forwarded-Email": "bob@example.com"}) as b:
                welcome = await until(a, lambda m: m["t"] == "welcome")
                self.assertEqual(welcome["user"]["email"], "alice@example.com")
                await until(b, lambda m: m["t"] == "welcome")
                await a.send(json.dumps({"t": "join", "location": "graph"}))
                await b.send(json.dumps({"t": "join", "location": "graph"}))
                await b.send(json.dumps({"t": "subscribe", "channels": ["graph", PRESENCE_CHANNEL]}))
                await asyncio.sleep(0.1)  # let join and subscribe land

                await a.send(json.dumps({"t": "cursor", "location": "graph", "x": 12.5, "y": -3}))
                cursor = await until(b, lambda m: m["t"] == "cursor")
                self.assertEqual((cursor["email"], cursor["x"], cursor["y"]), ("alice@example.com", 12.5, -3))

                await a.send(json.dumps({"t": "viewport", "location": "graph", "x": 5, "y": 6, "zoom": 1.5}))
                viewport = await until(b, lambda m: m["t"] == "viewport")
                self.assertEqual(viewport["zoom"], 1.5)

                # Presence heartbeats and graph events reach the socket at once.
                async with httpx.AsyncClient(base_url=f"http://{self.base}") as http:
                    await http.post(
                        "/api/presence",
                        json={"where": "task:t1", "selection": "t1", "editing": "description"},
                        headers={"X-Forwarded-Email": "alice@example.com"},
                    )
                presence = await until(b, lambda m: m["t"] == "event" and m["channel"] == PRESENCE_CHANNEL)
                self.assertEqual(presence["data"][0]["editing"], "description")
                self.broker.publish("graph", "event", {"seq": 1})
                event = await until(b, lambda m: m["t"] == "event" and m["channel"] == "graph")
                self.assertEqual(event["data"], {"seq": 1})

                await a.close()
                leave = await until(b, lambda m: m["t"] == "leave")
                self.assertEqual(leave["session"], cursor["session"])

        asyncio.run(go())

    def test_sse_fallback_carries_presence(self):
        with httpx.Client(base_url=f"http://{self.base}", timeout=10) as client:
            h = {"X-Forwarded-Email": "carol@example.com"}
            with client.stream("GET", "/api/events?channel=presence", headers=h) as stream:
                client.post("/api/presence", json={"where": "graph", "selection": "t9"}, headers=h)
                # Keep one iterator: dropping it would close the stream.
                lines = stream.iter_lines()
                data = next(json.loads(line[6:]) for line in lines if line.startswith("data: "))
        self.assertEqual(data[0]["email"], "carol@example.com")
        self.assertEqual(data[0]["selection"], "t9")


if __name__ == "__main__":
    unittest.main()
