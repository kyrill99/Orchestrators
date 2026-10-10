"""Closed loop: plan -> worker run (real subprocess) -> review -> next task starts."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import Database  # noqa: E402
from taskgraph import Actor, RecordRunner, RunManager, SubprocessRunner, TaskGraphService  # noqa: E402
from taskgraph.loop import auto_start, decide, review_task  # noqa: E402
from taskgraph.models import CreateTask, RequestDecision  # noqa: E402
from taskgraph.sqlite_store import SqliteEventStore  # noqa: E402

H = Actor(type="human", id="lorenz")


def plan(svc):
    svc.submit(Actor(type="agent", id="planner"), [
        CreateTask(id="start", title="START", kind="start"),
        CreateTask(id="research", title="Research", blocked_by=["start"]),
        CreateTask(id="choose", title="Decision: approach", kind="decision", blocked_by=["research"]),
        RequestDecision(task_id="choose", question="Optimise for recall or latency?", options=["recall", "latency"]),
        CreateTask(id="build", title="Build it", blocked_by=["choose"]),
    ], reason="initial plan")


class LoopWithRecordRunner(unittest.TestCase):
    def test_review_and_decide_advance_the_graph(self):
        svc = TaskGraphService()
        mgr = RunManager(svc, RecordRunner())
        plan(svc)
        self.assertEqual(auto_start(svc, mgr, H), ["research"])  # START milestone closed itself
        run = svc.runs(task_id="research", active=True)[0]
        mgr.finish(Actor(type="agent", id="worker"), run.id, "succeeded", "done")
        self.assertEqual(svc.task("research")["status"], "in-review")
        out = review_task(svc, mgr, H, "research", approve=False, note="add baselines")
        self.assertEqual(out["started"], ["research"])  # restarted with feedback
        run = svc.runs(task_id="research", active=True)[0]
        self.assertIn("add baselines", run.instructions)
        mgr.finish(Actor(type="agent", id="worker"), run.id, "succeeded", "done")
        out = review_task(svc, mgr, H, "research", approve=True)
        self.assertEqual(out["started"], [])  # decision is waiting on the owner
        out = decide(svc, mgr, H, "choose", "recall", "research project, quality first")
        self.assertEqual(out["started"], ["build"])


class LoopWithRealWorker(unittest.TestCase):
    def test_worker_process_posts_result_and_task_goes_to_review(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"DATABASE_PATH": os.path.join(d, "o.db"), "WORKER_ARTIFACTS": os.path.join(d, "art"),
                   "GEMINI_API_KEY": "", "TASKGRAPH_STORE": "sqlite", "TASKGRAPH_PATH": os.path.join(d, "g.jsonl")}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                     for k, v in old.items()])
            db = Database(env["DATABASE_PATH"])
            self.addCleanup(db.close)
            svc = TaskGraphService(SqliteEventStore(db))
            mgr = RunManager(svc, SubprocessRunner(f"{sys.executable} -m taskgraph.worker",
                                                   log_dir=os.path.join(d, "runs")))
            plan(svc)
            auto_start(svc, mgr, H)
            for _ in range(100):
                if svc.task("research")["status"] == "in-review":
                    break
                time.sleep(0.1)
            t = svc.task("research")
            log = open(os.path.join(d, "runs", os.listdir(os.path.join(d, "runs"))[0])).read()
            self.assertEqual(t["status"], "in-review", log)
            self.assertTrue(any(a["op"] == "comment.add" and "placeholder" in a["text"] for a in t["activity"]))
            self.assertTrue(t["artifacts"])


if __name__ == "__main__":
    unittest.main()
