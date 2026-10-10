import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from taskgraph import Actor, RunManager, TaskGraphService  # noqa: E402
from taskgraph.models import CreateTask  # noqa: E402

H = Actor(type="human", id="lorenz")
A = Actor(type="agent", id="planner")


class RunStatsTest(unittest.TestCase):
    def setUp(self):
        self.svc = TaskGraphService()
        self.svc.submit(H, [CreateTask(id="a", title="A"), CreateTask(id="b", title="B")])
        self.runs = RunManager(self.svc)

    def test_cycles_tokens_and_active_since(self):
        self.assertEqual(self.svc.graph()["runStats"], {})
        first = self.runs.start(A, "a", "go", agent_id="coder")
        self.svc.update_run(first.id, tokens=1200)
        self.runs.finish(A, first.id, "failed", note="boom")
        second = self.runs.restart(A, "a", "again")
        self.svc.update_run(second.id, tokens=300)
        stats = self.svc.graph()["runStats"]
        self.assertEqual(stats["a"]["cycles"], 2)
        self.assertEqual(stats["a"]["tokens"], 1500)
        self.assertEqual(stats["a"]["activeSince"], self.svc.run(second.id).started_at)
        self.assertNotIn("b", stats)
        self.runs.finish(A, second.id, "succeeded")
        self.assertIsNone(self.svc.graph()["runStats"]["a"]["activeSince"])
        self.assertEqual(self.svc.graph()["runStats"]["a"]["tokens"], 1500)

    def test_usage_tokens_reads_several_shapes(self):
        try:
            from services.gemini_worker import _usage_tokens
        except ImportError as e:  # the worker needs FastAPI & co.
            self.skipTest(f"gemini_worker not importable here: {e}")
        self.assertEqual(_usage_tokens(SimpleNamespace()), 0)
        self.assertEqual(_usage_tokens(SimpleNamespace(usage=SimpleNamespace(total_tokens=42))), 42)
        self.assertEqual(_usage_tokens(SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 5})), 15)
        self.assertEqual(_usage_tokens(SimpleNamespace(
            usage_metadata=SimpleNamespace(total_token_count=7))), 7)


if __name__ == "__main__":
    unittest.main()
