import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from taskgraph import (  # noqa: E402
    Actor,
    JsonlEventStore,
    RunManager,
    SubprocessRunner,
    TaskGraphError,
    TaskGraphService,
)
from taskgraph.models import CreateTask, DeleteTask, RequestDecision  # noqa: E402

H = Actor(type="human", id="lorenz")
A = Actor(type="agent", id="planner")


def wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class RunsTest(unittest.TestCase):
    def setUp(self):
        self.svc = TaskGraphService()
        self.svc.submit(H, [CreateTask(id="a", title="A"), CreateTask(id="b", title="B", blocked_by=["a"])])
        self.runs = RunManager(self.svc)

    def test_start_moves_task_and_records_run(self):
        run = self.runs.start(A, "a", "go", agent_id="coder")
        self.assertEqual((run.status, run.runner, run.attempt), ("running", "record", 1))
        task = self.svc.task("a")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["assignee"], {"type": "agent", "id": "coder"})
        self.assertEqual(task["activeRun"]["id"], run.id)
        self.assertEqual([r.id for r in self.runs.active()], [run.id])
        self.assertEqual(self.svc.graph()["activeRuns"][0]["agent"]["id"], "coder")

    def test_start_rules(self):
        cases = [("b", "blocked"), ("nope", "not_found")]
        for task_id, code in cases:
            with self.assertRaises(TaskGraphError) as cm:
                self.runs.start(A, task_id, "go")
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(TaskGraphError):
            self.runs.start(A, "a", "")  # agents need a reason
        self.runs.start(A, "a", "go")
        with self.assertRaises(TaskGraphError) as cm:
            self.runs.start(A, "a", "again")
        self.assertEqual(cm.exception.code, "conflict")
        with self.assertRaises(TaskGraphError) as cm:
            self.svc.submit(A, [DeleteTask(task_id="a")], "drop")
        self.assertEqual(cm.exception.code, "conflict")

    def test_pending_decision_and_ask_all(self):
        self.svc.submit(H, [RequestDecision(task_id="a", question="Recall or latency?")])
        with self.assertRaises(TaskGraphError):
            self.runs.start(A, "a", "go")
        self.svc.update_settings(H, autonomy="ask-all")
        self.svc.submit(H, [CreateTask(id="c", title="C")])
        with self.assertRaises(TaskGraphError) as cm:
            self.runs.start(A, "c", "go")
        self.assertEqual(cm.exception.code, "forbidden")
        self.assertEqual(self.runs.start(H, "c").started_by, H)

    def test_stop_restart_finish(self):
        first = self.runs.start(A, "a", "go", instructions="be brief")
        stopped = self.runs.stop(A, first.id, "wrong approach")
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(self.svc.task("a")["status"], "open")
        with self.assertRaises(TaskGraphError):
            self.runs.stop(A, first.id, "again")

        second = self.runs.restart(A, "a", "retry")
        self.assertEqual((second.attempt, second.restart_of, second.instructions), (2, first.id, "be brief"))
        third = self.runs.restart(A, "a", "retry while running")
        self.assertEqual(self.svc.run(second.id).status, "stopped")
        self.assertEqual(third.attempt, 3)

        done = self.runs.finish(A, third.id, "succeeded", "tests pass")
        self.assertEqual(done.status, "succeeded")
        self.assertEqual(self.svc.task("a")["status"], "in-review")
        self.assertEqual(self.runs.active(), [])
        self.assertEqual(len(self.svc.runs(task_id="a")), 3)
        texts = [a["text"] for a in self.svc.task("a")["activity"]]
        self.assertTrue(any("restarted agent worker" in t for t in texts), texts)

    def test_rest_lists_runs(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from taskgraph.api import build_router

        app = FastAPI()
        app.include_router(build_router(self.svc))
        run = self.runs.start(A, "a", "go")
        c = TestClient(app)
        self.assertEqual([r["id"] for r in c.get("/api/taskgraph/runs", params={"active": True}).json()], [run.id])
        self.assertEqual(c.get("/api/taskgraph/runs", params={"taskId": "b"}).json(), [])
        self.assertEqual(c.get("/api/taskgraph/graph").json()["activeRuns"][0]["taskId"], "a")

    def test_rest_start_stop_restart(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from taskgraph.api import build_router

        app = FastAPI()
        app.include_router(build_router(self.svc, self.runs))
        c = TestClient(app)
        me = {"type": "human", "id": "lorenz"}
        base = "/api/taskgraph/tasks"

        r = c.post(f"{base}/a/run/start", json={"actor": me, "agentId": "coder"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["agent"]["id"], r.json()["startedBy"]), ("coder", me))
        self.assertEqual(c.get(f"/api/taskgraph/tasks/a").json()["activeRun"]["id"], r.json()["id"])
        self.assertEqual(c.post(f"{base}/a/run/start", json={"actor": me}).status_code, 409)
        self.assertEqual(c.post(f"{base}/b/run/start", json={"actor": me}).json()["detail"]["code"], "blocked")
        self.assertEqual(c.post(f"{base}/nope/run/start", json={"actor": me}).status_code, 404)
        agent = {"type": "agent", "id": "planner"}
        self.assertEqual(c.post(f"{base}/a/run/stop", json={"actor": agent}).status_code, 422)  # no reason

        r = c.post(f"{base}/a/run/restart", json={"actor": me, "reason": "retry"})
        self.assertEqual((r.json()["attempt"], r.json()["agent"]["id"]), (2, "coder"))
        r = c.post(f"{base}/a/run/stop", json={"actor": me, "reason": "pause"})
        self.assertEqual(r.json()["status"], "stopped")
        self.assertEqual(self.svc.task("a")["status"], "open")
        self.assertEqual(c.post(f"{base}/a/run/stop", json={"actor": me}).status_code, 409)
        self.assertEqual(c.post(f"{base}/nope/run/stop", json={"actor": me}).status_code, 404)

    def test_runs_survive_replay(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.jsonl")
            svc = TaskGraphService(JsonlEventStore(path))
            svc.submit(H, [CreateTask(id="t", title="T")])
            run = RunManager(svc).start(A, "t", "go")
            other = TaskGraphService(JsonlEventStore(path))
            self.assertEqual(other.runs(active=True)[0].id, run.id)
            RunManager(other).stop(H, run.id)
            self.assertEqual(svc.run(run.id).status, "stopped")


@unittest.skipIf(os.name == "nt", "process groups are POSIX only")
class SubprocessRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "g.jsonl")
        self.svc = TaskGraphService(JsonlEventStore(self.path))
        self.svc.submit(H, [CreateTask(id="t", title="Write the report", description="Two pages.")])

    def manager(self, script):
        cmd = [sys.executable, "-c", script, "{task_id}"]
        return RunManager(self.svc, SubprocessRunner(cmd, log_dir=os.path.join(self.tmp.name, "runs"),
                                                      grace_seconds=2))

    def test_exit_code_is_recorded(self):
        runs = self.manager("import sys; print(sys.argv[1], sys.stdin.read()); sys.exit(0)")
        run = runs.start(A, "t", "go")
        self.assertIsNotNone(run.pid)
        self.assertTrue(wait_for(lambda: self.svc.run(run.id).status != "running"))
        ended = self.svc.run(run.id)
        self.assertEqual((ended.status, ended.exit_code), ("succeeded", 0))
        self.assertEqual(self.svc.task("t")["status"], "in-review")
        with open(ended.log_path) as fh:
            log = fh.read()
        self.assertIn("t Task t: Write the report", log)
        self.assertIn("Two pages.", log)

    def test_failure_and_stop(self):
        failing = self.manager("import sys; sys.exit(3)")
        run = failing.start(A, "t", "go")
        self.assertTrue(wait_for(lambda: self.svc.run(run.id).status != "running"))
        self.assertEqual((self.svc.run(run.id).status, self.svc.run(run.id).exit_code), ("failed", 3))
        self.assertEqual(self.svc.task("t")["status"], "open")

        sleeper = self.manager("import time; time.sleep(60)")
        run = sleeper.start(A, "t", "go")
        self.assertTrue(sleeper.runner.is_alive(run))
        stopped = sleeper.stop(A, run.id, "enough")
        self.assertEqual(stopped.status, "stopped")
        self.assertTrue(wait_for(lambda: not sleeper.runner.is_alive(stopped)))
        time.sleep(0.2)  # the watcher must not overwrite 'stopped'
        self.assertEqual(self.svc.run(run.id).status, "stopped")

    def test_lost_run_is_reconciled(self):
        sleeper = self.manager("import time; time.sleep(60)")
        run = sleeper.start(A, "t", "go")
        # A second server process sees the run but did not launch it.
        other_svc = TaskGraphService(JsonlEventStore(self.path))
        other = RunManager(other_svc, SubprocessRunner(["true"], log_dir=self.tmp.name))
        self.assertEqual([r.id for r in other.active()], [run.id])  # pid alive: not lost
        sleeper.runner._stopping.add(run.id)  # simulate the launcher dying silently
        os.killpg(run.pid, 9)
        self.assertTrue(wait_for(lambda: not sleeper.runner.owns(run)))
        self.assertEqual(other.active(), [])
        self.assertEqual(other_svc.run(run.id).status, "lost")


class McpRunToolsTest(unittest.TestCase):
    def test_tools_in_process(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from taskgraph.mcp_server import build_server

        svc = TaskGraphService()
        server = build_server(svc)

        async def call(name, args):
            res = await server.call_tool(name, args)
            self.assertFalse(res.is_error, res.content)
            if res.structured_content is not None:  # list results
                return res.structured_content["result"]
            return json.loads(res.content[0].text)

        async def go():
            names = {t.name for t in await server.list_tools()}
            self.assertLessEqual({"start_task", "stop_task", "restart_task", "start_ready_tasks",
                                  "stop_all_runs", "finish_run", "list_active_runs", "list_runs",
                                  "get_run", "list_changesets", "get_events"}, names)
            await call("apply_changes", {"reason": "plan", "ops": [
                {"op": "task.create", "id": "r", "title": "Research"},
                {"op": "task.create", "id": "s", "title": "Survey"},
                {"op": "task.create", "id": "p", "title": "Proto", "blockedBy": ["r"]}]})
            started = await call("start_ready_tasks", {"reason": "kick off"})
            self.assertEqual(sorted(r["taskId"] for r in started), ["r", "s"])
            self.assertEqual(started[0]["startedBy"], {"type": "agent", "id": "planner"})
            self.assertEqual(len(await call("list_active_runs", {})), 2)
            await call("stop_task", {"task_id": "s", "reason": "not needed yet"})
            run = await call("restart_task", {"task_id": "r", "reason": "retry", "instructions": "cite sources"})
            self.assertEqual(run["attempt"], 2)
            await call("finish_run", {"run_id": run["id"], "outcome": "succeeded", "note": "done"})
            self.assertEqual(svc.task("r")["status"], "in-review")
            self.assertEqual(await call("list_active_runs", {}), [])
            self.assertEqual(len(await call("list_runs", {"task_id": "r"})), 2)
            self.assertTrue(any(e["type"] == "run.ended" for e in await call("get_events", {"since": 0})))
            self.assertEqual(len(await call("list_changesets", {"status": "applied"})), 1)
            with self.assertRaisesRegex(ToolError, "no active run"):
                await server.call_tool("stop_task", {"task_id": "p", "reason": "x"})

        asyncio.run(go())

    def test_round_trip_through_client(self):
        from mcp import Client

        from taskgraph.mcp_server import build_server

        svc = TaskGraphService()
        server = build_server(svc, agent_id="planner-2")

        async def go():
            async with Client(server) as client:
                tools = {t.name for t in (await client.list_tools()).tools}
                self.assertIn("start_task", tools)
                await client.call_tool("create_task", {"title": "Index docs", "id": "idx", "reason": "plan"})
                res = await client.call_tool("start_task", {"task_id": "idx", "reason": "go", "agent_id": "coder"})
                self.assertFalse(res.is_error, res.content)
                res = await client.call_tool("list_active_runs", {})
                self.assertIn('"coder"', res.content[0].text)
                res = await client.call_tool("start_task", {"task_id": "idx", "reason": "again"})
                self.assertTrue(res.is_error)
                self.assertIn("conflict", res.content[0].text)
                await client.call_tool("stop_task", {"task_id": "idx", "reason": "pause"})

        asyncio.run(go())
        run = svc.runs()[0]
        self.assertEqual((run.status, run.ended_by.id), ("stopped", "planner-2"))

    @unittest.skipIf(os.name == "nt", "process groups are POSIX only")
    def test_stdio_entry_point_with_subprocess_runner(self):
        from mcp import Client, StdioServerParameters

        backend = os.path.join(os.path.dirname(__file__), "..")
        with tempfile.TemporaryDirectory() as d:
            env = {
                **os.environ,
                "TASKGRAPH_PATH": os.path.join(d, "g.jsonl"),
                "MEMORY_PATH": os.path.join(d, "memory"),
                "TASKGRAPH_RUNNER": "subprocess",
                "TASKGRAPH_RUNNER_CMD": f'"{sys.executable}" -c "import sys; print(sys.stdin.read())"',
            }
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "taskgraph.mcp_server"], env=env, cwd=backend
            )

            async def go():
                async with Client(params) as client:
                    await client.call_tool("create_task", {"title": "Hello", "id": "h", "reason": "plan"})
                    res = await client.call_tool("start_task", {"task_id": "h", "reason": "go"})
                    self.assertFalse(res.is_error, res.content)
                    self.assertEqual(json.loads(res.content[0].text)["runner"], "subprocess")
                    for _ in range(100):  # the server records the exit code
                        res = await client.call_tool("get_run", {"run_id": "run-1"})
                        if json.loads(res.content[0].text)["status"] != "running":
                            break
                        await asyncio.sleep(0.1)

            asyncio.run(go())
            # The run is in the shared log, so the FastAPI process sees it too.
            svc = TaskGraphService(JsonlEventStore(env["TASKGRAPH_PATH"]))
            self.assertEqual(svc.run("run-1").status, "succeeded")
            with open(svc.run("run-1").log_path) as fh:
                self.assertIn("Task h: Hello", fh.read())


if __name__ == "__main__":
    unittest.main()
