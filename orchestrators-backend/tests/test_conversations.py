import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import services.agent as agent_service  # noqa: E402
from conversations import (  # noqa: E402
    ConversationService,
    ConversationStore,
    PostBody,
    build_router,
)
from db import Database  # noqa: E402
from events import Broker  # noqa: E402
from identity import User  # noqa: E402

ALICE = User(email="alice@example.com", name="alice")
BOB = User(email="bob@example.com", name="bob")


class FakeAgent:
    """Stands in for services.agent.chat; never calls Gemini."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, message, *, previous_interaction_id=None, mode="text", on_tool=None):
        if on_tool and message.startswith("remember"):
            on_tool("remember", {"text": message}, {"ok": True})
            on_tool("recall", {"query": "x"}, {"error": "not_found: nothing"})
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            n = len(self.calls) + 1
            self.calls.append((message, previous_interaction_id, mode))
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return {"reply": f"reply to {message}", "interaction_id": f"i{n}"}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.dir.name, "t.db"))
        self.store = ConversationStore(self.db)
        self.agent = FakeAgent()
        self._orig = agent_service.chat
        agent_service.chat = self.agent

    def tearDown(self):
        agent_service.chat = self._orig
        self.db.close()
        self.dir.cleanup()


class StoreTest(Base):
    def test_storage_title_participants_preview(self):
        cid = self.store.create(ALICE)
        self.store.add_message(cid, "user", "[stage direction]", user=ALICE, hidden=True)
        self.assertEqual(self.store.summary(cid).title, "")
        self.store.add_message(cid, "user", "  Build a   better\nchunker ", user=BOB)
        self.store.add_message(cid, "agent", "x" * 500, interaction_id="i9")
        s = self.store.summary(cid)
        self.assertEqual(s.title, "Build a better chunker")
        self.assertEqual([p.email for p in s.participants], [ALICE.email, BOB.email])
        self.assertEqual(s.message_count, 2)
        self.assertEqual(s.last_message.role, "agent")
        self.assertLessEqual(len(s.last_message.content), 140)
        self.assertEqual(self.store.interaction_id(cid), "i9")
        msgs = self.store.messages(cid)
        self.assertEqual([m.role for m in msgs], ["user", "agent"])
        self.assertEqual(msgs[0].author_name, "bob")
        self.assertEqual(len(msgs[1].content), 500)

    def test_list_newest_activity_first(self):
        a = self.store.create(ALICE, "a")
        b = self.store.create(ALICE, "b")
        self.store.add_message(a, "user", "later", user=ALICE)
        self.assertEqual([s.id for s in self.store.summaries()], [a, b])

    def test_schema_is_idempotent(self):
        ConversationStore(self.db)


class ServiceTest(Base):
    def test_lock_serializes_agent_calls_into_one_chain(self):
        self.agent.delay = 0.05
        broker = Broker()
        service = ConversationService(self.store, broker)

        async def run():
            cid = (await service.create(ALICE, None, "text")).id
            sub = broker.subscribe(["conversations"])
            await asyncio.gather(
                service.post(cid, ALICE, PostBody(content="one")),
                service.post(cid, BOB, PostBody(content="two")),
                service.post(cid, ALICE, PostBody(content="three")),
            )
            events = []
            while not sub.queue.empty():
                events.append(sub.queue.get_nowait())
            return cid, events

        cid, events = asyncio.run(run())
        self.assertEqual(self.agent.max_active, 1)
        self.assertEqual([c[1] for c in self.agent.calls], [None, "i1", "i2"])
        self.assertEqual(self.store.interaction_id(cid), "i3")
        thinking = [e.data["thinking"] for e in events if e.type == "thinking"]
        self.assertEqual(thinking, [True, False] * 3)
        roles = [e.data["message"]["role"] for e in events if e.type == "message"]
        self.assertEqual(roles.count("user"), 3)
        self.assertEqual(roles.count("agent"), 3)

    def test_tool_calls_are_stored_and_broadcast(self):
        broker = Broker()
        service = ConversationService(self.store, broker)

        async def run():
            cid = (await service.create(ALICE, None, "text")).id
            sub = broker.subscribe(["conversations"])
            await service.post(cid, ALICE, PostBody(content="remember this"))
            await asyncio.sleep(0.05)  # tool events hop over from the worker thread
            events = []
            while not sub.queue.empty():
                events.append(sub.queue.get_nowait())
            return cid, events

        cid, events = asyncio.run(run())
        roles = [e.data["message"]["role"] for e in events if e.type == "message"]
        self.assertEqual(roles, ["user", "tool", "tool", "agent"])
        tools = [m for m in self.store.messages(cid) if m.role == "tool"]
        self.assertEqual(tools[0].content, 'remember({"text": "remember this"})')
        self.assertIn("not_found", tools[1].content)
        self.assertEqual(self.store.summary(cid).last_message.role, "agent")

    def test_agent_error_is_stored_and_raised(self):
        def boom(*a, **k):
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured on the server")

        agent_service.chat = boom
        client = TestClient(self._app())
        cid = client.post("/api/conversations", json={}).json()["id"]
        r = client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"})
        self.assertEqual(r.status_code, 503)
        detail = client.get(f"/api/conversations/{cid}").json()
        self.assertEqual([m["role"] for m in detail["messages"]], ["user", "error"])
        self.assertIn("GEMINI_API_KEY", detail["messages"][1]["content"])
        self.assertFalse(detail["thinking"])

    def test_rest_endpoints(self):
        client = TestClient(self._app())
        h = {"X-Forwarded-Email": "Bob@Example.com"}
        created = client.post("/api/conversations", json={"title": "Plan"}, headers=h)
        self.assertEqual(created.status_code, 201)
        cid = created.json()["id"]
        self.assertEqual(created.json()["created_by"], "bob@example.com")
        r = client.post(f"/api/conversations/{cid}/messages", json={"content": "hello"}, headers=h).json()
        self.assertEqual(r["message"]["author_email"], "bob@example.com")
        self.assertEqual(r["reply"]["content"], "reply to hello")
        hidden = client.post(
            f"/api/conversations/{cid}/messages",
            json={"content": "[opener]", "mode": "voice", "hidden": True},
        ).json()
        self.assertIsNone(hidden["message"])
        listed = client.get("/api/conversations").json()
        self.assertEqual(listed[0]["id"], cid)
        self.assertEqual(listed[0]["message_count"], 3)
        self.assertEqual(client.get("/api/conversations/nope").status_code, 404)
        self.assertEqual(client.post("/api/conversations/nope/messages", json={"content": "x"}).status_code, 404)

    def _app(self):
        app = FastAPI()
        app.include_router(build_router(ConversationService(self.store, Broker())))
        return app


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SSERoundTripTest(Base):
    """Real uvicorn server: one user posts, another user's stream sees it."""

    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_router(ConversationService(self.store, Broker())))
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(5)
        super().tearDown()

    def _read_events(self, response, until, out):
        event = {}
        for line in response.iter_lines():
            if line.startswith("id: "):
                event["id"] = line[4:]
            elif line.startswith("event: "):
                event["type"] = line[7:]
            elif line.startswith("data: "):
                event["data"] = json.loads(line[6:])
            elif line == "" and event:
                out.append(event)
                if until(event):
                    return
                event = {}

    def test_post_by_one_user_reaches_other_users_stream(self):
        with httpx.Client(base_url=self.base, timeout=10) as client:
            with client.stream(
                "GET", "/api/conversations/stream", headers={"X-Forwarded-Email": "bob@example.com"}
            ) as stream:
                self.assertEqual(stream.headers["content-type"].split(";")[0], "text/event-stream")
                self.assertEqual(stream.headers["x-accel-buffering"], "no")
                self.assertIn("no-cache", stream.headers["cache-control"])
                h = {"X-Forwarded-Email": "alice@example.com"}
                cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
                poster = threading.Thread(
                    target=lambda: client.post(
                        f"/api/conversations/{cid}/messages", json={"content": "hi all"}, headers=h
                    )
                )
                poster.start()
                events = []
                self._read_events(
                    stream,
                    lambda e: e.get("type") == "message" and e["data"]["message"]["role"] == "agent",
                    events,
                )
                poster.join()

        types = [e["type"] for e in events]
        self.assertEqual(types[0], "conversation")
        self.assertIn("thinking", types)
        users = [e["data"]["message"] for e in events if e["type"] == "message"]
        self.assertEqual(users[0]["author_email"], "alice@example.com")
        self.assertEqual(users[0]["content"], "hi all")
        self.assertEqual(users[1]["content"], "reply to hi all")

        # Reconnect with Last-Event-ID: everything after it is replayed.
        with httpx.Client(base_url=self.base, timeout=10) as client:
            with client.stream(
                "GET", "/api/conversations/stream", headers={"Last-Event-ID": events[0]["id"]}
            ) as stream:
                replay = []
                self._read_events(stream, lambda e: len(replay) == len(events) - 1, replay)
        self.assertEqual([e["id"] for e in replay], [e["id"] for e in events[1:]])


if __name__ == "__main__":
    unittest.main()
