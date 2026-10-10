import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import storage  # noqa: E402
from db import Database  # noqa: E402
from memory import MarkdownMemoryStore, MemoryService  # noqa: E402
from memory.sqlite_store import SqliteMemoryStore  # noqa: E402
from taskgraph import Actor, JsonlEventStore, TaskGraphService  # noqa: E402
from taskgraph.models import CreateTask  # noqa: E402
from taskgraph.sqlite_store import SqliteEventStore  # noqa: E402
from userstate import MAX_VALUE_BYTES, UserStateStore, build_router  # noqa: E402

H = Actor(type="human", id="lorenz")


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.jsonl = os.path.join(self.dir, "taskgraph.jsonl")
        self.md = os.path.join(self.dir, "memory")
        env = mock.patch.dict(os.environ, {
            "DATABASE_PATH": os.path.join(self.dir, "o.db"),
            "TASKGRAPH_PATH": self.jsonl,
            "MEMORY_PATH": self.md,
        })
        env.start()
        self.addCleanup(env.stop)
        for var in ("TASKGRAPH_STORE", "MEMORY_STORE"):
            os.environ.pop(var, None)
        # Avoid leaking the process-wide default Database across tests.
        if storage._default_db is not None:
            storage._default_db.close()
            storage._default_db = None
        self.addCleanup(self._close_default_db)

    def _close_default_db(self):
        if storage._default_db is not None:
            storage._default_db.close()
            storage._default_db = None

    def open_db(self):
        db = Database()
        self.addCleanup(db.close)
        return db

    def legacy_data(self):
        tg = TaskGraphService(JsonlEventStore(self.jsonl))
        tg.submit(H, [CreateTask(id="a", title="A"), CreateTask(id="b", title="B", blocked_by=["a"])])
        tg.set_status(H, "a", "running")
        mem = MemoryService(MarkdownMemoryStore(self.md))
        mem.remember(H, title="Goal", body="coherence", kind="strategy", project_id="p")
        mem.remember(H, title="Pref", body="short", kind="preference", scope="user", user_id="u")
        mem.context(project_id="p", user_id="u")
        return tg, mem


class ImportTest(StorageTestCase):
    def test_import_once_and_leave_files(self):
        tg_old, mem_old = self.legacy_data()
        for _ in range(3):  # three "restarts"
            db = self.open_db()
            tg = TaskGraphService(storage.open_taskgraph_store(db))
            mem = MemoryService(storage.open_memory_store(db))
            self.assertIsInstance(tg.store, SqliteEventStore)
            self.assertIsInstance(mem.store, SqliteMemoryStore)
            self.assertEqual(tg.events_since(0), tg_old.events_since(0))
            self.assertEqual(tg.graph(), tg_old.graph())
            self.assertEqual(mem.list_memories(), mem_old.list_memories())
            self.assertEqual(mem.log(0), mem_old.log(0))
        self.assertTrue(os.path.exists(self.jsonl))
        self.assertTrue(os.path.exists(os.path.join(self.md, "log.jsonl")))
        info = storage.info(db)
        self.assertEqual((info["taskgraph_events"], info["memories"]), (5, 2))
        self.assertEqual(len(info["imports"]), 2)

    def test_new_writes_survive_restart_without_reimport(self):
        self.legacy_data()
        tg = TaskGraphService(storage.open_taskgraph_store(self.open_db()))
        tg.set_status(H, "a", "closed")
        again = TaskGraphService(storage.open_taskgraph_store(self.open_db()))
        self.assertEqual(again.state.seq, 7)
        self.assertEqual(again.task("a")["status"], "closed")
        with open(self.jsonl) as fh:
            self.assertEqual(len(fh.readlines()), 5)  # JSONL untouched

    def test_no_import_into_existing_data(self):
        db = self.open_db()
        TaskGraphService(storage.open_taskgraph_store(db)).submit(H, [CreateTask(id="x", title="X")])
        self.legacy_data()  # appears later; must not be merged into the existing log
        tg = TaskGraphService(storage.open_taskgraph_store(db))
        self.assertEqual(list(tg.state.tasks), ["x"])

    def test_missing_sources(self):
        db = self.open_db()
        self.assertEqual(TaskGraphService(storage.open_taskgraph_store(db)).state.seq, 0)
        self.assertEqual(MemoryService(storage.open_memory_store(db)).list_memories(), [])


class FactoryTest(StorageTestCase):
    def test_legacy_backends_selectable(self):
        with mock.patch.dict(os.environ, {"TASKGRAPH_STORE": "jsonl", "MEMORY_STORE": "markdown"}):
            self.assertIsInstance(storage.open_taskgraph_store(), JsonlEventStore)
            self.assertIsInstance(storage.open_memory_store(), MarkdownMemoryStore)
        with mock.patch.dict(os.environ, {"TASKGRAPH_STORE": "postgres"}):
            with self.assertRaises(ValueError):
                storage.open_taskgraph_store()

    def test_memory_lock_can_wrap_taskgraph_write(self):
        db = self.open_db()
        tg = TaskGraphService(storage.open_taskgraph_store(db))
        mem = MemoryService(storage.open_memory_store(db))
        with mem.store.locked():
            tg.submit(H, [CreateTask(id="a", title="A")])
            mem.remember(H, title="t", body="b", kind="note", project_id="p")
        self.assertEqual(TaskGraphService(storage.open_taskgraph_store(self.open_db())).state.seq, 2)

    def test_backup(self):
        db = self.open_db()
        TaskGraphService(storage.open_taskgraph_store(db)).submit(H, [CreateTask(id="a", title="A")])
        os.makedirs(os.path.join(self.dir, "backups"))
        dest = storage.backup(os.path.join(self.dir, "backups"))
        conn = sqlite3.connect(dest)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT count(*) FROM taskgraph_events").fetchone()[0], 2)
        with self.assertRaises(FileExistsError):
            storage.backup(dest)
        self.assertEqual(storage.main(["nope"]), 2)


class UserStateTest(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.store = UserStateStore(self.open_db())
        app = FastAPI()
        app.include_router(build_router(self.store))
        self.c = TestClient(app)
        self.alice = {"X-Forwarded-Email": "Alice@Example.com"}
        self.bob = {"X-Forwarded-Email": "bob@example.com"}

    def test_me_records_user(self):
        r = self.c.get("/api/me", headers=self.alice)
        self.assertEqual(r.json(), {"email": "alice@example.com", "name": "alice"})
        first = self.store.user("alice@example.com")
        self.c.get("/api/me", headers=self.alice)
        again = self.store.user("alice@example.com")
        self.assertEqual(again["first_seen"], first["first_seen"])
        self.assertIsNone(self.store.user("bob@example.com"))

    def test_state_crud_per_user(self):
        c = self.c
        self.assertEqual(c.get("/api/me/state", headers=self.alice).json(), {})
        self.assertEqual(c.get("/api/me/state/theme", headers=self.alice).status_code, 404)
        r = c.put("/api/me/state/theme", headers=self.alice, json={"mode": "dark", "n": [1, 2]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"mode": "dark", "n": [1, 2]})
        c.put("/api/me/state/last-project", headers=self.alice, json="chunking")
        c.put("/api/me/state/nothing", headers=self.alice, content=b"null")
        self.assertEqual(c.get("/api/me/state/nothing", headers=self.alice).status_code, 200)
        self.assertEqual(c.get("/api/me/state/theme", headers=self.alice).json()["mode"], "dark")
        self.assertEqual(c.get("/api/me/state", headers=self.bob).json(), {})
        self.assertEqual(set(c.get("/api/me/state", headers=self.alice).json()),
                         {"theme", "last-project", "nothing"})
        c.put("/api/me/state/theme", headers=self.alice, json={"mode": "light"})
        self.assertEqual(c.get("/api/me/state/theme", headers=self.alice).json(), {"mode": "light"})
        self.assertEqual(c.delete("/api/me/state/theme", headers=self.alice).status_code, 204)
        self.assertEqual(c.delete("/api/me/state/theme", headers=self.alice).status_code, 404)
        self.assertIsNotNone(self.store.user("bob@example.com"))

    def test_validation(self):
        c = self.c
        big = "x" * MAX_VALUE_BYTES
        self.assertEqual(c.put("/api/me/state/k", headers=self.alice, json=big).status_code, 413)
        self.assertEqual(c.put("/api/me/state/k", headers=self.alice, content=b"{nope").status_code, 400)
        self.assertEqual(c.put("/api/me/state/bad key!", headers=self.alice, json=1).status_code, 400)
        self.assertEqual(c.get("/api/me/state", headers=self.alice).json(), {})


if __name__ == "__main__":
    unittest.main()
