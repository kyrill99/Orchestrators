"""Task artifacts: storage, REST (with the sandbox CSP), worker and planner tools."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from artifacts import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    ArtifactError,
    ArtifactStore,
    build_router,
    clean_filename,
)
from db import Database  # noqa: E402
from projects import current_project  # noqa: E402
from services.agent_tools import AgentTools, ToolContext, planner_tools, worker_tools  # noqa: E402
from services.gemini_worker import GeminiRunner, worker_instructions  # noqa: E402
from taskgraph import Actor, RunManager, TaskGraphService  # noqa: E402
from taskgraph.models import CreateTask  # noqa: E402
from taskgraph.runlog import RunLog, Scratchpads  # noqa: E402

H = Actor(type="human", id="lorenz")
PAGE = "<!doctype html><html><body><h1>Report</h1><script>1</script></body></html>"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "o.db"))
        self.addCleanup(self.db.close)
        self.store = ArtifactStore(self.db)
        self.mine = self.store.scoped("default")
        self.svc = TaskGraphService()
        self.svc.submit(H, [CreateTask(id="t", title="Compare engines"), CreateTask(id="u", title="Other")])


class StoreTest(Base):
    def test_save_list_read(self):
        meta, created = self.mine.save("t", "My Report!.html", PAGE, title="Engines", run_id="r1",
                                       created_by="w")
        self.assertTrue(created)
        self.assertEqual(meta["filename"], "My-Report.html")
        self.assertEqual((meta["kind"], meta["contentType"]), ("html", "text/html; charset=utf-8"))
        self.assertEqual(meta["size"], len(PAGE))
        self.assertEqual(meta["viewUrl"], f"/api/artifacts/{meta['id']}/view")
        path = self.store.path(meta)
        self.assertTrue(path.is_relative_to(os.path.join(self.tmp.name, "artifacts", "default", "t")))
        self.assertEqual(path.read_text(), PAGE)
        self.assertEqual(self.mine.read_text(meta["id"])[1], PAGE)
        self.assertEqual([a["id"] for a in self.mine.list("t")], [meta["id"]])
        self.assertEqual(self.mine.list("u"), [])
        # Other projects do not see it.
        self.assertEqual(self.store.scoped("other").list("t"), [])
        with self.assertRaises(ArtifactError):
            self.store.scoped("other").read_text(meta["id"])

    def test_same_filename_in_a_run_replaces(self):
        a, _ = self.mine.save("t", "report.html", PAGE, title="v1", run_id="r1", created_by="w")
        b, created = self.mine.save("t", "report", PAGE + "!", title="v2", run_id="r1", created_by="w")
        self.assertFalse(created)
        self.assertEqual((a["id"], b["title"], b["size"]), (b["id"], "v2", len(PAGE) + 1))
        c, created = self.mine.save("t", "report.html", PAGE, title="v3", run_id="r2", created_by="w")
        self.assertTrue(created)
        self.assertEqual(len(self.mine.list("t")), 2)

    def test_limits_and_validation(self):
        with self.assertRaises(ArtifactError) as e:
            self.mine.save("t", "big.html", "x" * (MAX_ARTIFACT_BYTES + 1), title="Big", created_by="w")
        self.assertEqual(e.exception.code, "too_large")
        self.mine.save("t", "ok.html", "x" * MAX_ARTIFACT_BYTES, title="Max", created_by="w")
        for kw in ({"title": ""}, {"title": "x", "kind": "exe"}):
            with self.assertRaises(ArtifactError):
                self.mine.save("t", "a.html", PAGE, created_by="w", **{"title": "x", **kw})
        with self.assertRaises(ArtifactError):
            self.mine.save("t", "a.html", "  ", title="Empty", created_by="w")
        self.assertEqual(self.mine.list("t")[0]["title"], "Max")

    def test_filenames_are_safe(self):
        self.assertEqual(clean_filename("../../etc/passwd", "text"), "passwd.txt")
        self.assertEqual(clean_filename("..", "html"), "report.html")
        self.assertEqual(clean_filename("notes.markdown", "markdown"), "notes.md")
        self.assertEqual(clean_filename("data.json", "html"), "data.json.html")
        meta, _ = self.store.save("p/../x", "../t", "a.txt", "hi", title="T", kind="text", created_by="w")
        self.assertTrue(self.store.path(meta).resolve().is_relative_to(self.store.files.resolve()))


class RestTest(Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_router(self.store))
        self.client = TestClient(app)
        self.html, _ = self.mine.save("t", "report.html", PAGE, title="Engines", run_id="r1", created_by="w")
        self.md, _ = self.mine.save("t", "notes.md", "# Notes", title="Notes", kind="markdown", created_by="w")

    def test_list_and_metadata(self):
        res = self.client.get("/api/artifacts", params={"task_id": "t"})
        self.assertEqual([a["title"] for a in res.json()], ["Engines", "Notes"])
        self.assertEqual(self.client.get("/api/artifacts", params={"task_id": "u"}).json(), [])
        self.assertEqual(self.client.get(f"/api/artifacts/{self.html['id']}").json()["runId"], "r1")
        self.assertEqual(self.client.get("/api/artifacts/nope").status_code, 404)
        token = current_project.set("other")
        try:
            self.assertEqual(self.client.get("/api/artifacts", params={"task_id": "t"}).json(), [])
        finally:
            current_project.reset(token)

    def test_view_is_sandboxed(self):
        res = self.client.get(f"/api/artifacts/{self.html['id']}/view")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, PAGE)
        self.assertTrue(res.headers["content-type"].startswith("text/html"))
        csp = res.headers["content-security-policy"]
        directives = {d.strip().split(" ")[0]: d.strip() for d in csp.split(";")}
        self.assertEqual(directives["sandbox"],
                         "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox")
        self.assertNotIn("allow-same-origin", csp)
        self.assertEqual(directives["connect-src"], "connect-src 'none'")
        self.assertEqual(directives["default-src"], "default-src 'none'")
        self.assertEqual(directives["form-action"], "form-action 'none'")
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        self.assertTrue(res.headers["content-disposition"].startswith("inline"))
        # Non-HTML shows as plain text, still sandboxed.
        res = self.client.get(f"/api/artifacts/{self.md['id']}/view")
        self.assertTrue(res.headers["content-type"].startswith("text/plain"))
        self.assertIn("sandbox", res.headers["content-security-policy"])

    def test_download(self):
        res = self.client.get(f"/api/artifacts/{self.html['id']}/download")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers["content-disposition"].startswith("attachment"))
        self.assertIn("report.html", res.headers["content-disposition"])
        self.assertIn("sandbox", res.headers["content-security-policy"])
        self.assertEqual(self.client.get("/api/artifacts/nope/download").status_code, 404)


class ToolTest(Base):
    def worker(self, **kw):
        return AgentTools(None, self.svc, agent_id="w",
                          toolset=lambda c: worker_tools(c, run_id="run-1", task_id="t"),
                          runs=RunManager(self.svc), artifacts=self.mine, **kw)

    def test_save_artifact_adds_a_task_artifact(self):
        tools = self.worker()
        schema = tools.tools["save_artifact"].schema()
        self.assertEqual(sorted(schema["properties"]), ["content", "filename", "kind", "title"])
        out = tools.dispatch("save_artifact", {"filename": "report.html", "content": PAGE,
                                               "title": "Engine comparison"})
        self.assertNotIn("error", out)
        meta = self.mine.list("t")[0]
        self.assertEqual((meta["runId"], meta["createdBy"]), ("run-1", "w"))
        self.assertEqual(self.svc.task("t")["artifacts"],
                         [{"path": meta["viewUrl"], "label": "Engine comparison"}])
        # Saving again replaces the file and relabels, without a second entry.
        tools.dispatch("save_artifact", {"filename": "report.html", "content": PAGE, "title": "Final"})
        self.assertEqual(self.svc.task("t")["artifacts"], [{"path": meta["viewUrl"], "label": "Final"}])
        # Adding the same path again by hand does not duplicate it either.
        tools.dispatch("update_my_task", {"reason": "x", "add_artifacts": [
            {"path": meta["viewUrl"], "label": "Report"}, {"path": "https://x", "label": "X"}]})
        self.assertEqual([a["label"] for a in self.svc.task("t")["artifacts"]], ["Report", "X"])
        listed = tools.dispatch("list_artifacts", {})
        self.assertEqual([a["title"] for a in listed], ["Final"])
        self.assertEqual(tools.dispatch("read_artifact", {"artifact_id": meta["id"]})["content"], PAGE)
        too_big = tools.dispatch("save_artifact", {"filename": "x.html", "title": "x",
                                                   "content": "x" * (MAX_ARTIFACT_BYTES + 1)})
        self.assertIn("too_large", too_big["error"])

    def test_without_a_store_there_are_no_artifact_tools(self):
        tools = AgentTools(None, self.svc, toolset=lambda c: worker_tools(c, run_id="r", task_id="t"))
        self.assertNotIn("save_artifact", tools.tools)

    def test_planner_reads_artifacts_over_mcp(self):
        from taskgraph.mcp_server import build_server

        meta, _ = self.mine.save("t", "report.html", PAGE, title="Engines", run_id="r1", created_by="w")
        ctx = ToolContext(taskgraph=self.svc, actor=Actor(type="agent", id="planner"), artifacts=self.mine)
        names = {t.name for t in planner_tools(ctx)}
        self.assertLessEqual({"list_artifacts", "read_artifact"}, names)
        self.assertNotIn("save_artifact", names)
        server = build_server(self.svc, artifacts=self.mine)

        async def go():
            res = await server.call_tool("list_artifacts", {"task_id": "t"})
            self.assertFalse(res.is_error, res.content)
            self.assertIn(meta["id"], str(res.content))
            res = await server.call_tool("read_artifact", {"artifact_id": meta["id"], "max_chars": 20})
            self.assertIn("<!doctype html>", str(res.content))
            self.assertIn("more characters", str(res.content))
            with self.assertRaisesRegex(Exception, "not_found"):
                await server.call_tool("read_artifact", {"artifact_id": "nope"})

        asyncio.run(go())


class RunnerTest(Base):
    def test_gemini_worker_saves_its_report(self):
        def call(name, args):
            return NS(type="function_call", name=name, arguments=args, id=f"c-{name}")

        script = [
            NS(id="i1", status="requires_action", output_text=None, steps=[
                call("save_artifact", {"filename": "report.html", "title": "Engines", "content": PAGE})]),
            NS(id="i2", status="requires_action", output_text=None, steps=[
                call("finish_run", {"outcome": "succeeded", "summary": "Report: Engines"})]),
        ]
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return script.pop(0)

        client = NS(interactions=NS(create=create))
        runner = GeminiRunner(self.svc, RunLog(self.db), Scratchpads(self.db), artifacts=self.mine,
                              client_factory=lambda: client, builtin_tools=[], retry_delays=())
        run = RunManager(self.svc, runner).start(H, "t", "go", agent_id="gemini")
        self.assertTrue(runner.join(run.id, 5))
        self.assertEqual(self.svc.run(run.id).status, "succeeded")
        meta = self.mine.list("t")[0]
        self.assertEqual(meta["runId"], run.id)
        self.assertEqual(self.svc.task("t")["artifacts"][0]["path"], meta["viewUrl"])
        self.assertIn("save_artifact", [t.get("name") for t in calls[0]["tools"]])
        self.assertIn("report.html", calls[0]["system_instruction"])

    def test_instructions_ask_for_a_report(self):
        text = worker_instructions()
        self.assertIn("## Your report", text)
        self.assertIn("save_artifact", text)
        self.assertIn("prefers-color-scheme", text)
        self.assertIn("polished report", text)  # the role card


if __name__ == "__main__":
    unittest.main()
