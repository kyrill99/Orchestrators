"""Gemini worker runs, the shared tool table, run steps, scratchpads and the engine.

Gemini is never called: a scripted fake client stands in for
`client.interactions.create`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from db import Database  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import MarkdownMemoryStore  # noqa: E402
from services.agent_tools import (  # noqa: E402
    TOOL_DECLARATIONS,
    AgentTools,
    ToolContext,
    planner_tools,
    worker_tools,
)
from services.gemini_worker import GeminiRunner, build_brief  # noqa: E402
from taskgraph import Actor, RecordRunner, RunManager, TaskGraphError, TaskGraphService  # noqa: E402
from taskgraph.api import build_engine_router, build_router  # noqa: E402
from taskgraph.engine import Engine  # noqa: E402
from taskgraph.models import CreateTask, SetStatus  # noqa: E402
from taskgraph.runlog import RunLog, Scratchpads  # noqa: E402

H = Actor(type="human", id="lorenz")


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def call(name, args, id=None):
    return NS(type="function_call", name=name, arguments=args, id=id or f"c-{name}")


def reply(*steps, id="i", status="requires_action"):
    return NS(id=id, status=status, steps=list(steps), output_text=None)


def text(t):
    return NS(type="model_output", content=[NS(type="text", text=t)], error=None)


class FakeInteractions:
    """Pops scripted responses; an entry may be a callable(kwargs) or an exception."""

    def __init__(self, script, default=None):
        self.script = list(script)
        self.default = default
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else self.default
        if item is None:
            raise AssertionError("unexpected interactions.create call")
        if isinstance(item, Exception):
            raise item
        out = item(kwargs) if callable(item) else item
        return NS(**{**vars(out), "id": f"i{len(self.calls)}"})


class FakeClient:
    def __init__(self, script, default=None):
        self.interactions = FakeInteractions(script, default)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "o.db"))
        self.addCleanup(self.db.close)
        self.svc = TaskGraphService()
        self.svc.submit(H, [
            CreateTask(id="t", title="Summarise orchestrators", description="Three bullets."),
            CreateTask(id="u", title="Second task"),
            CreateTask(id="v", title="Third task"),
            CreateTask(id="later", title="Blocked", blocked_by=["t"]),
            CreateTask(id="d", title="Pick", kind="decision"),
        ])
        self.runlog = RunLog(self.db)
        self.pads = Scratchpads(self.db)
        self.memory = MemoryService(MarkdownMemoryStore(os.path.join(self.tmp.name, "mem")))

    def runner(self, client, **kw):
        kw.setdefault("builtin_tools", ["google_search", "code_execution"])
        kw.setdefault("retry_delays", ())
        return GeminiRunner(self.svc, self.runlog, self.pads, memory=self.memory,
                            client_factory=lambda: client, **kw)

    def kinds(self, run_id):
        return [(s["kind"], s["payload"].get("name") or s["payload"].get("state"))
                for s in self.runlog.steps(run_id)]


class ToolTableTest(Base):
    def ctx(self, **kw):
        return ToolContext(taskgraph=self.svc, actor=Actor(type="agent", id="w"), memory=self.memory,
                           runs=RunManager(self.svc), scratchpads=self.pads, runlog=self.runlog, **kw)

    def test_worker_tools_are_bound_to_their_task(self):
        tools = {t.name: t for t in worker_tools(self.ctx(), run_id="run-1", task_id="t")}
        self.assertLessEqual({"get_task", "add_comment", "update_my_task", "create_subtask",
                              "request_decision", "read_scratchpad", "write_scratchpad",
                              "append_scratchpad", "recall", "remember", "finish_run"}, set(tools))
        self.assertNotIn("delete_task", tools)
        fin = tools["finish_run"].schema()
        self.assertEqual(sorted(fin["properties"]), ["outcome", "summary"])
        self.assertNotIn("task_id", tools["write_scratchpad"].schema()["properties"])
        self.assertEqual(sorted(tools["update_my_task"].schema()["properties"]),
                         ["add_artifacts", "description", "progress", "reason"])
        # Same function as the planner's tool, narrower.
        planner = {t.name: t for t in planner_tools(self.ctx())}
        self.assertIs(tools["create_subtask"].fn.__code__, planner["create_task"].fn.__code__)
        self.assertIn("title", TOOL_DECLARATIONS[[d["name"] for d in TOOL_DECLARATIONS].index(
            "create_task")]["parameters"]["properties"])

    def test_worker_tools_act_on_their_task(self):
        tools = AgentTools(self.memory, self.svc, agent_id="w",
                           toolset=lambda c: worker_tools(c, run_id="run-1", task_id="t"),
                           runs=RunManager(self.svc), scratchpads=self.pads)
        tools.dispatch("write_scratchpad", {"content": "# Plan"})
        tools.dispatch("append_scratchpad", {"text": "- step 1"})
        self.assertEqual(self.pads.get("t")["content"], "# Plan\n- step 1")
        self.assertEqual(self.pads.get("t")["updatedBy"], "w")
        tools.dispatch("update_my_task", {"reason": "progress", "progress": 0.5,
                                          "add_artifacts": [{"path": "https://x", "label": "X"}]})
        tools.dispatch("update_my_task", {"reason": "more", "add_artifacts": [{"path": "b"}]})
        task = self.svc.task("t")
        self.assertEqual(task["progress"], 0.5)
        self.assertEqual([a["path"] for a in task["artifacts"]], ["https://x", "b"])
        out = tools.dispatch("create_subtask", {"title": "Follow up", "reason": "split"})
        self.assertEqual(self.svc.task(out["createdIds"][0])["parentId"], "t")
        tools.dispatch("add_comment", {"body": "halfway"})
        self.assertIn("halfway", self.svc.task("t")["activity"][-1]["text"])
        self.assertIn("error", tools.dispatch("update_my_task", {"reason": "x", "progress": 3}))
        self.assertIn("error", tools.dispatch("add_comment", {}))
        self.assertIn("error", tools.dispatch("nope", {}))

    def test_planner_mcp_uses_the_table(self):
        from taskgraph.mcp_server import build_server

        engine = Engine(self.svc, RunManager(self.svc, FakeRunner()), self.db)
        server = build_server(self.svc, runs=engine.runs, runlog=self.runlog,
                              scratchpads=self.pads, engine=engine)

        async def go():
            names = {t.name for t in await server.list_tools()}
            self.assertLessEqual({"engine_status", "engine_start", "engine_stop", "read_scratchpad",
                                  "write_scratchpad", "get_run_steps", "start_task"}, names)
            await server.call_tool("write_scratchpad", {"task_id": "u", "content": "notes"})
            res = await server.call_tool("engine_start", {"reason": "go"})
            self.assertFalse(res.is_error, res.content)

        asyncio.run(go())
        self.assertEqual(self.pads.get("u")["content"], "notes")
        self.assertTrue(engine.running)


class FakeRunner(RecordRunner):
    name = "fake"


class GeminiRunnerTest(Base):
    def test_tool_calls_are_recorded_and_finish_ends_the_run(self):
        client = FakeClient([
            reply(NS(type="thought", summary=[NS(text="Plan the bullets")]),
                  NS(type="google_search_call", id="g1", arguments={"queries": ["orchestrator"]}),
                  NS(type="google_search_result", call_id="g1", is_error=False,
                     result=[{"search_suggestions": "<html>", "url": "https://e.x"}]),
                  call("write_scratchpad", {"content": "- a\n- b\n- c"})),
            reply(text("Done writing."), call("finish_run", {"outcome": "succeeded", "summary": "3 bullets"}),
                  call("add_comment", {"body": "never runs"})),
        ])
        runner = self.runner(client)
        runs = RunManager(self.svc, runner)
        run = runs.start(H, "t", "go", agent_id="gemini")
        self.assertEqual((run.runner, run.pid), ("gemini", os.getpid()))
        self.assertTrue(runner.join(run.id, 5))
        self.assertEqual(self.svc.run(run.id).status, "succeeded")
        self.assertEqual(self.svc.run(run.id).note, "3 bullets")
        self.assertEqual(self.svc.task("t")["status"], "in-review")
        self.assertEqual(self.pads.get("t")["content"], "- a\n- b\n- c")
        self.assertEqual(self.kinds(run.id), [
            ("status", "started"), ("thinking", None), ("tool_call", "google_search"),
            ("tool_result", "google_search"), ("tool_call", "write_scratchpad"),
            ("tool_result", "write_scratchpad"), ("message", None), ("tool_call", "finish_run"),
            ("tool_result", "finish_run"), ("status", "succeeded"),
        ])
        self.assertNotIn("<html>", str(self.runlog.steps(run.id)[3]["payload"]))
        first, second = client.interactions.calls
        self.assertIn("Summarise orchestrators", first["input"])
        self.assertIn({"type": "google_search"}, first["tools"])
        self.assertEqual(second["previous_interaction_id"], "i1")
        self.assertEqual(second["input"][0]["type"], "function_result")
        self.assertEqual(second["input"][0]["call_id"], "c-write_scratchpad")
        # The cursor returns only newer steps.
        self.assertEqual([s["seq"] for s in self.runlog.steps(run.id, after=8)], [9, 10])

    def test_step_budget_fails_the_run(self):
        client = FakeClient([], default=reply(call("add_comment", {"body": "still going"})))
        runner = self.runner(client, max_steps=3)
        run = RunManager(self.svc, runner).start(H, "t", "go")
        self.assertTrue(runner.join(run.id, 5))
        self.assertEqual(len(client.interactions.calls), 3)
        ended = self.svc.run(run.id)
        self.assertEqual(ended.status, "failed")
        self.assertIn("budget", ended.note)
        self.assertEqual(self.svc.task("t")["status"], "open")

    def test_text_without_tools_is_nudged_then_fails(self):
        client = FakeClient([], default=reply(text("I think I am done."), status="completed"))
        runner = self.runner(client)
        run = RunManager(self.svc, runner).start(H, "t", "go")
        self.assertTrue(runner.join(run.id, 5))
        self.assertEqual(len(client.interactions.calls), 3)
        self.assertIn("[engine]", client.interactions.calls[1]["input"])
        self.assertEqual(self.svc.run(run.id).status, "failed")

    def test_gemini_errors_are_retried_then_fail(self):
        client = FakeClient([RuntimeError("503 overloaded"), RuntimeError("503 again")])
        runner = self.runner(client, retry_delays=(0.01,))
        run = RunManager(self.svc, runner).start(H, "t", "go")
        self.assertTrue(runner.join(run.id, 5))
        self.assertEqual(self.svc.run(run.id).status, "failed")
        self.assertEqual([k for k, _ in self.kinds(run.id)].count("error"), 2)

    def test_stop_cancels_mid_run(self):
        entered, release = threading.Event(), threading.Event()

        def slow(kwargs):
            entered.set()
            release.wait(5)
            return reply(call("add_comment", {"body": "should not be posted"}))

        client = FakeClient([slow])
        runner = self.runner(client)
        runs = RunManager(self.svc, runner)
        run = runs.start(H, "t", "go")
        self.assertTrue(entered.wait(5))
        stopped = runs.stop(H, run.id, "enough")
        release.set()
        self.assertTrue(runner.join(run.id, 5))
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(self.svc.run(run.id).status, "stopped")
        self.assertEqual(len(client.interactions.calls), 1)
        self.assertFalse(any("should not" in a["text"] for a in self.svc.task("t")["activity"]))
        self.assertEqual(self.kinds(run.id)[-1], ("status", "stopped"))

    def test_restart_reconciles_and_the_brief_continues(self):
        entered, release = threading.Event(), threading.Event()

        def hang(kwargs):
            entered.set()
            release.wait(5)
            raise RuntimeError("process gone")

        old = self.runner(FakeClient([hang]))
        run = RunManager(self.svc, old).start(H, "t", "go")
        self.assertTrue(entered.wait(5))
        self.pads.write("t", "Found two of three bullets.", "gemini")
        self.svc.submit(H, [CreateTask(id="x", title="x")])  # unrelated change
        # A new process: same database, a runner that does not own the run.
        new = self.runner(FakeClient([]))
        lost = RunManager(self.svc, new).reconcile()
        self.assertEqual([r.id for r in lost], [run.id])
        self.assertIn("interrupted", self.svc.run(run.id).note)
        self.assertEqual(self.svc.task("t")["status"], "open")
        release.set()
        self.assertTrue(old.join(run.id, 5))
        self.assertEqual(self.svc.run(run.id).status, "lost")  # the old thread did not overwrite it
        # The next attempt's brief carries the scratchpad and the lost attempt.
        nxt = RunManager(self.svc, RecordRunner()).start(H, "t", "again", restart_of=run.id)
        task = self.svc.state.tasks["t"]
        brief = build_brief(self.svc, self.pads, self.runlog, None, nxt, task)
        self.assertIn("Found two of three bullets.", brief)
        self.assertIn("attempt 1", brief)
        self.assertIn("lost", brief)
        # A dependent task sees what this one produced.
        self.svc.end_run(H, nxt.id, "succeeded", note="Wrote the three bullets.")
        self.svc.submit(H, [SetStatus(task_id="t", status="closed")])
        dep = RunManager(self.svc, RecordRunner()).start(H, "later", "go")
        brief = build_brief(self.svc, self.pads, self.runlog, None, dep, self.svc.state.tasks["later"])
        self.assertIn("What the tasks before this one produced", brief)
        self.assertIn("Wrote the three bullets.", brief)


class EngineTest(Base):
    def setUp(self):
        super().setUp()
        self.runs = RunManager(self.svc, FakeRunner())
        self.engine = Engine(self.svc, self.runs, self.db, max_runs=2, max_attempts=2)

    def test_stopped_by_default_and_persisted(self):
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.tick(), [])
        self.engine.start(H, "go")
        again = Engine(self.svc, self.runs, self.db)  # e.g. after a restart
        self.assertTrue(again.running)
        self.assertEqual(again.status()["updatedBy"], "lorenz")

    def test_dispatch_respects_the_limit_and_queue_rules(self):
        self.engine.start(H, "go")
        started = self.engine.tick()
        self.assertEqual([r.task_id for r in started], ["t", "u"])  # decision and blocked skipped
        self.assertEqual(self.engine.tick(), [])  # both slots taken
        self.assertEqual([q["id"] for q in self.engine.status()["queue"]], ["v"])
        self.runs.finish(Actor(type="agent", id="gemini"), started[0].id, "failed", "nope")
        self.assertEqual([r.task_id for r in self.engine.tick()], ["v"])  # failed 't' is not retried
        status = self.engine.status()
        self.assertEqual((status["state"], len(status["activeRuns"]), status["maxRuns"]), ("running", 2, 2))

    def test_lost_runs_are_resumed_up_to_max_attempts(self):
        self.engine.start(H, "go")
        for attempt in (1, 2):
            started = {r.task_id: r for r in self.engine.tick()}
            self.assertIn("t", started)
            self.assertEqual(started["t"].attempt, attempt)
            for r in started.values():
                self.svc.end_run(H, r.id, "lost", note="interrupted")
        self.assertNotIn("t", [r.task_id for r in self.engine.tick()])

    def test_stop_with_runs(self):
        self.engine.start(H, "go")
        self.engine.tick()
        self.engine.stop(H, "pause")
        self.assertEqual(len(self.runs.active()), 2)
        self.engine.stop(H, "halt", stop_runs=True)
        self.assertEqual(self.runs.active(), [])
        self.assertFalse(self.engine.running)

    def test_record_runner_cannot_run_the_engine(self):
        engine = Engine(self.svc, RunManager(self.svc), self.db)
        with self.assertRaises(TaskGraphError):
            engine.start(H, "go")

    def test_background_loop_dispatches(self):
        self.engine.interval = 0.05
        self.engine.start_loop()
        self.addCleanup(self.engine.stop_loop)
        self.engine.start(H, "go")
        self.assertTrue(wait_for(lambda: len(self.runs.active()) == 2))


class RestTest(Base):
    def setUp(self):
        super().setUp()
        self.runs = RunManager(self.svc, FakeRunner())
        self.engine = Engine(self.svc, self.runs, self.db, max_runs=1)
        app = FastAPI()
        app.include_router(build_router(self.svc, self.runs, runlog=self.runlog, scratchpads=self.pads))
        app.include_router(build_engine_router(self.engine))
        self.http = TestClient(app)

    def test_steps_cursor(self):
        run = self.runs.start(H, "t", "go")
        for i in range(3):
            self.runlog.add(run.id, "t", "message", {"text": f"m{i}"})
        body = self.http.get(f"/api/taskgraph/runs/{run.id}/steps").json()
        self.assertEqual(([s["seq"] for s in body["steps"]], body["next"], body["active"]), ([1, 2, 3], 3, True))
        body = self.http.get(f"/api/taskgraph/runs/{run.id}/steps?after=2").json()
        self.assertEqual([s["payload"]["text"] for s in body["steps"]], ["m2"])
        body = self.http.get(f"/api/taskgraph/runs/{run.id}/steps?after=3").json()
        self.assertEqual((body["steps"], body["next"]), ([], 3))
        self.assertEqual(self.http.get("/api/taskgraph/runs/run-99/steps").status_code, 404)
        runs = self.http.get("/api/taskgraph/tasks/t/runs").json()
        self.assertEqual([r["id"] for r in runs], [run.id])
        self.assertEqual(self.http.get("/api/taskgraph/tasks/nope/runs").status_code, 404)

    def test_scratchpad(self):
        self.assertEqual(self.http.get("/api/taskgraph/tasks/t/scratchpad").json()["content"], "")
        run = self.runs.start(H, "t", "go")
        res = self.http.put("/api/taskgraph/tasks/t/scratchpad", json={"content": "# Notes"},
                            headers={"x-forwarded-email": "Ann@Example.com"})
        self.assertEqual(res.status_code, 200)
        got = self.http.get("/api/taskgraph/tasks/t/scratchpad").json()
        self.assertEqual((got["content"], got["updatedBy"]), ("# Notes", "ann@example.com"))
        self.assertEqual(self.runlog.steps(run.id)[-1]["payload"]["state"], "scratchpad_edited")
        self.assertEqual(self.http.put("/api/taskgraph/tasks/nope/scratchpad",
                                       json={"content": "x"}).status_code, 404)

    def test_engine_endpoints(self):
        self.assertEqual(self.http.get("/api/engine").json()["state"], "stopped")
        body = self.http.post("/api/engine/start", json={"reason": "go"}).json()
        self.assertEqual(body["state"], "running")
        self.engine.tick()
        body = self.http.get("/api/engine").json()
        self.assertEqual((len(body["activeRuns"]), body["maxRuns"]), (1, 1))
        body = self.http.post("/api/engine/stop", json={"stopRuns": True}).json()
        self.assertEqual((body["state"], body["activeRuns"]), ("stopped", []))
        self.assertEqual(self.http.post("/api/engine/start").status_code, 200)
        self.assertIn("activeRuns", self.http.get("/api/taskgraph/graph").json())


if __name__ == "__main__":
    unittest.main()
