import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import Database  # noqa: E402
from taskgraph import Actor, JsonlEventStore, MemoryEventStore, TaskGraphError, TaskGraphService  # noqa: E402
from taskgraph.sqlite_store import SqliteEventStore  # noqa: E402
from taskgraph.models import AddRelation, CreateTask, RequestDecision, ResolveDecision  # noqa: E402

H = Actor(type="human", id="lorenz")
A = Actor(type="agent", id="planner")


class CoreTest(unittest.TestCase):
    """Runs against the in-memory store; subclasses below rerun it on disk."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.svc = TaskGraphService(self.open_store())
        self.svc.submit(H, [CreateTask(id="a", title="A"), CreateTask(id="b", title="B", blocked_by=["a"])])

    def test_blocked_status_rejected(self):
        with self.assertRaises(TaskGraphError) as cm:
            self.svc.set_status(A, "b", "running", reason="go")
        self.assertEqual(cm.exception.code, "blocked")

    def test_cycle_rejected_and_batch_atomic(self):
        with self.assertRaises(TaskGraphError):
            self.svc.submit(H, [CreateTask(id="c", title="C"), AddRelation(source="b", target="a")])
        self.assertNotIn("c", self.svc.state.tasks)

    def test_agent_needs_reason(self):
        with self.assertRaises(TaskGraphError):
            self.svc.submit(A, [CreateTask(title="X")])

    def test_autonomy_default_auto(self):
        r = self.svc.submit(A, [CreateTask(title="X")], reason="r")
        self.assertEqual(r.status, "applied")

    def test_ask_structural_and_review(self):
        self.svc.update_settings(H, autonomy="ask-structural")
        r = self.svc.submit(A, [CreateTask(id="d", title="D", kind="decision", blocked_by=["b"]),
                                RequestDecision(task_id="d", question="Recall or latency?")], reason="r")
        self.assertEqual(r.status, "proposed")
        self.assertNotIn("d", self.svc.state.tasks)
        with self.assertRaises(TaskGraphError):
            self.svc.review(A, r.changeset_id, True)
        self.svc.review(H, r.changeset_id, True)
        self.assertIn("d", self.svc.state.tasks)
        self.assertEqual(self.svc.set_status(A, "a", "closed", reason="r").status, "applied")

    def test_decision_log(self):
        self.svc.submit(H, [RequestDecision(task_id="a", question="Q?"),
                            ResolveDecision(task_id="a", chosen="x", rationale="because")])
        log = self.svc.decisions("resolved")
        self.assertEqual(log[0]["rationale"], "because")
        self.assertEqual(self.svc.task("a")["status"], "closed")

    def open_store(self):
        """A store on this test's location; called again for a 'second process'."""
        return MemoryEventStore()

    def test_shared_between_processes(self):
        if type(self) is CoreTest:
            self.skipTest("in-memory store is per process")
        gui, agent = TaskGraphService(self.open_store()), TaskGraphService(self.open_store())
        gui.submit(H, [CreateTask(id="t", title="T")])
        agent.set_status(A, "t", "running", reason="start")
        self.assertEqual(gui.task("t")["status"], "running")
        fresh = TaskGraphService(self.open_store())
        self.assertEqual(fresh.task("t")["status"], "running")
        self.assertEqual(fresh.events_since(0), self.svc.events_since(0))
        self.assertEqual(fresh.state.seq, self.svc.state.seq)

    def test_failed_batch_writes_nothing(self):
        seq = self.svc.state.seq
        with self.assertRaises(TaskGraphError):
            self.svc.submit(H, [CreateTask(id="z", title="Z"), AddRelation(source="b", target="a")])
        self.assertEqual(self.svc.store.read_since(0)[-1].seq, seq)


class JsonlCoreTest(CoreTest):
    def open_store(self):
        return JsonlEventStore(os.path.join(self.dir.name, "g.jsonl"))


class SqliteCoreTest(CoreTest):
    def open_store(self):
        db = Database(os.path.join(self.dir.name, "g.db"))  # one per "process"
        self.addCleanup(db.close)
        return SqliteEventStore(db)


class McpTest(unittest.TestCase):
    def test_tools(self):
        from taskgraph.mcp_server import build_server

        svc = TaskGraphService()
        server = build_server(svc)

        async def go():
            names = {t.name for t in await server.list_tools()}
            self.assertIn("apply_changes", names)
            await server.call_tool("apply_changes", {"reason": "plan", "ops": [
                {"op": "task.create", "id": "r", "title": "Research"},
                {"op": "task.create", "id": "p", "title": "Proto", "blockedBy": ["r"]}]})
            await server.call_tool("set_status", {"task_id": "r", "status": "closed", "reason": "done"})
            res = await server.call_tool("ready_tasks", {})
            self.assertIn('"p"', res.content[0].text)

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
