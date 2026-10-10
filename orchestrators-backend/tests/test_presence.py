import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from presence import PresenceStore, build_router  # noqa: E402


class PresenceStoreTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.changes = []
        self.store = PresenceStore(
            ttl=15, clock=lambda: self.now, on_change=self.changes.append
        )

    def test_update_and_list(self):
        self.store.update("a@x.com", "a", "agent", typing=True, conversation_id="c1")
        self.store.update("b@x.com", "b", "task:t1")
        entries = self.store.active()
        self.assertEqual([e["email"] for e in entries], ["a@x.com", "b@x.com"])
        self.assertTrue(entries[0]["typing"])
        self.assertEqual(entries[0]["conversation_id"], "c1")
        self.assertEqual(entries[1]["where"], "task:t1")

    def test_expires(self):
        self.store.update("a@x.com", "a", "agent")
        self.now += 16
        self.assertEqual(self.store.active(), [])

    def test_change_only_published_on_change(self):
        self.store.update("a@x.com", "a", "agent")
        self.store.update("a@x.com", "a", "agent")
        self.assertEqual(len(self.changes), 1)
        self.store.update("a@x.com", "a", "agent", typing=True)
        self.assertEqual(len(self.changes), 2)

    def test_multiplayer_fields(self):
        self.store.update("a@x.com", "a", "task:t1", selection="t2", editing="description", route="/tasks/t1")
        entry = self.store.active()[0]
        self.assertEqual((entry["selection"], entry["editing"], entry["route"]), ("t2", "description", "/tasks/t1"))
        self.assertEqual(entry["project"], "default")
        self.store.update("a@x.com", "a", "task:t1", selection="t2", route="/tasks/t1")  # editing ends
        self.assertEqual(len(self.changes), 2)

    def test_heartbeat_endpoint(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from presence import build_router

        app = FastAPI()
        app.include_router(build_router(self.store))
        client = TestClient(app)
        r = client.post(
            "/api/presence",
            json={"where": "graph", "typing": True, "route": "/project"},
            headers={"X-Forwarded-Email": "a@x.com"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()[0]["route"], "/project")
        self.assertEqual(client.get("/api/presence").json()[0]["email"], "a@x.com")

    def test_leave(self):
        self.store.update("a@x.com", "a", "agent")
        self.store.leave("a@x.com")
        self.assertEqual(self.store.active(), [])
        self.assertEqual(len(self.changes), 2)



class PresenceApiTest(unittest.TestCase):
    """Through HTTP: catches request-model wiring the store tests can't."""

    def setUp(self):
        app = FastAPI()
        app.include_router(build_router(PresenceStore(ttl=15)))
        self.client = TestClient(app)

    def test_heartbeat_and_list(self):
        res = self.client.post(
            "/api/presence",
            json={"where": "task:t1", "typing": True, "conversation_id": None},
            headers={"X-Forwarded-Email": "a@x.com"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([e["email"] for e in res.json()], ["a@x.com"])
        self.assertEqual(self.client.get("/api/presence").json()[0]["where"], "task:t1")

    def test_empty_where_leaves(self):
        headers = {"X-Forwarded-Email": "a@x.com"}
        self.client.post("/api/presence", json={"where": "agent"}, headers=headers)
        res = self.client.post("/api/presence", json={"where": ""}, headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.client.get("/api/presence").json(), [])


if __name__ == "__main__":
    unittest.main()
