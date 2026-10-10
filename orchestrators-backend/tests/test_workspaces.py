"""Project repositories: per-run worktrees, the code tools and their guards,
checks, commits, landing on main, send-back carry-over, and a fake-Gemini run
that edits, commits and pushes. Everything runs on temp git repos: a bare
"origin" and a workspace clone."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from db import Database  # noqa: E402
from projects import ProjectRegistry, build_router  # noqa: E402
from services.agent_tools import AgentTools, worker_tools  # noqa: E402
from services.gemini_worker import GeminiRunner  # noqa: E402
from taskgraph import Actor, RunManager, TaskGraphService  # noqa: E402
from taskgraph.models import CreateTask  # noqa: E402
from taskgraph.runlog import RunLog, Scratchpads  # noqa: E402
from workspaces import (  # noqa: E402
    LandingError,
    Workspace,
    WorkspaceConfig,
    WorkspaceError,
    derive_web_url,
    git,
)

H = Actor(type="human", id="lorenz")

CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TEST = ("import unittest\n\nfrom calc import add, mul\n\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
        "    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n")
TESTS = f"{sys.executable} -m unittest -q test_calc"


class RepoCase(unittest.TestCase):
    """A bare origin with a tiny Python project, and a config pointing at it."""

    empty = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.origin = self.dir / "origin.git"
        git(self.dir, "init", "-q", "--bare", "-b", "main", str(self.origin))
        if not self.empty:
            self.push_upstream({"calc.py": CALC, "test_calc.py": TEST, "README.md": "# calc\n"}, "Initial")
        self.config = WorkspaceConfig(
            remote=str(self.origin), root=str(self.dir / "ws" / "calc"),
            checks=[{"name": "tests", "command": TESTS, "timeoutSeconds": 60}], keepRuns=2,
        )
        self.ws = Workspace(self.config, "proj")

    def push_upstream(self, files: dict[str, str], message: str) -> str:
        """Someone else pushes to origin/main."""
        other = self.dir / "other"
        if not other.exists():
            git(self.dir, "clone", "-q", str(self.origin), str(other))
        else:
            git(other, "pull", "-q", "origin", "main")
        for name, content in files.items():
            (other / name).write_text(content)
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", message)
        git(other, "push", "-q", "origin", "HEAD:main")
        return git(other, "rev-parse", "HEAD").stdout.strip()

    def origin_log(self) -> list[str]:
        return git(self.origin, "log", "--format=%s", "main").stdout.split("\n")[:-1]

    def origin_head(self) -> str:
        return git(self.origin, "rev-parse", "main").stdout.strip()


class WorktreeTest(RepoCase):
    def test_each_run_gets_a_fresh_worktree_from_origin_main(self):
        a = self.ws.start_run("run-1", "t")  # clones on first use
        self.assertTrue((self.dir / "ws" / "calc" / ".git").is_dir())
        self.assertEqual(a.path, self.dir / "ws" / "calc" / ".runs" / "run-1")
        self.assertEqual(a.base, self.origin_head())
        self.assertEqual((a.path / "calc.py").read_text(), CALC)
        new = self.push_upstream({"NEWS": "x\n"}, "Upstream change")
        b = self.ws.start_run("run-2", "t")
        self.assertEqual(b.base, new)  # fetched before branching
        self.assertTrue((b.path / "NEWS").exists())
        self.assertFalse((a.path / "NEWS").exists())
        self.assertEqual((a.branch, b.branch), ("agent/run-1", "agent/run-2"))
        # The runs directory does not show up in the clone.
        self.assertEqual(git(self.ws.root, "status", "--porcelain").stdout, "")

    def test_cleanup_keeps_the_newest_runs(self):
        for i in range(1, 5):
            rw = self.ws.start_run(f"run-{i}", "t")
            os.utime(rw.path, (i * 1000, i * 1000))
        removed = self.ws.cleanup(active={"run-1"})
        self.assertEqual(sorted(removed), ["run-2"])
        self.assertEqual(sorted(p.name for p in self.ws.runs_on_disk()), ["run-1", "run-3", "run-4"])
        branches = git(self.ws.root, "branch", "--format=%(refname:short)").stdout.split()
        self.assertNotIn("agent/run-2", branches)

    def test_web_urls(self):
        self.assertEqual(derive_web_url("git@github-orchestrators:kyrill99/Orchestrators.git"),
                         "https://github.com/kyrill99/Orchestrators")
        self.assertEqual(derive_web_url("git@github.com:fibr/jfloor-game.git"),
                         "https://github.com/fibr/jfloor-game")
        self.assertEqual(derive_web_url("https://github.com/a/b"), "https://github.com/a/b")
        self.assertIsNone(derive_web_url("/tmp/origin.git"))
        self.assertIsNone(derive_web_url("git@gitlab.com:a/b.git"))


class ToolsTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.rw = self.ws.start_run("run-1", "t")

    def test_read_list_search(self):
        out = self.rw.read_file("calc.py", 2, 2)
        self.assertEqual(out["content"], "2      return a - b")
        self.assertEqual(out["totalLines"], 6)
        self.assertEqual(self.rw.list_files("*.py")["files"], ["calc.py", "test_calc.py"])
        self.assertEqual(self.rw.list_files("**/*.md")["files"], ["README.md"])
        hits = self.rw.search_code(r"def (add|mul)", glob="calc.py")["matches"]
        self.assertEqual(hits, ["calc.py:1:def add(a, b):", "calc.py:5:def mul(a, b):"])

    def test_write_edit_delete(self):
        self.rw.write_file("pkg/new.py", "X = 1\n")
        self.assertEqual((self.rw.path / "pkg/new.py").read_text(), "X = 1\n")
        out = self.rw.edit_file("calc.py", "return a - b", "return a + b")
        self.assertEqual(out["line"], 2)
        self.assertIn("return a + b", (self.rw.path / "calc.py").read_text())
        with self.assertRaisesRegex(WorkspaceError, "not found"):
            self.rw.edit_file("calc.py", "return a - b", "x")
        with self.assertRaisesRegex(WorkspaceError, "2 times"):
            self.rw.edit_file("calc.py", "(a, b)", "(x, y)")
        self.rw.delete_file("README.md")
        self.assertFalse((self.rw.path / "README.md").exists())
        status = self.rw.status()
        self.assertEqual(sorted(s[3:] for s in status["uncommitted"]), ["README.md", "calc.py", "pkg/new.py"])
        diff = self.rw.diff()
        self.assertIn("pkg/new.py", diff["stat"])
        self.assertIn("+    return a + b", diff["diff"])

    def test_guards(self):
        outside = self.dir / "secret.txt"
        outside.write_text("no")
        for bad in ("../../../secret.txt", str(outside), "/etc/passwd", "a/../../x"):
            with self.assertRaisesRegex(WorkspaceError, "outside"):
                self.rw.read_file(bad)
        (self.rw.path / "link").symlink_to(outside)
        with self.assertRaisesRegex(WorkspaceError, "outside"):
            self.rw.read_file("link")
        with self.assertRaisesRegex(WorkspaceError, "outside"):
            self.rw.write_file("link", "overwrite")
        self.assertEqual(outside.read_text(), "no")
        for bad in (".git/config", ".git", "sub/.git/HEAD"):
            with self.assertRaisesRegex(WorkspaceError, ".git"):
                self.rw.read_file(bad)
        with self.assertRaisesRegex(WorkspaceError, ".git"):
            self.rw.write_file(".git/hooks/pre-commit", "#!/bin/sh")
        for bad in (".env", "backend/.env", ".env.production", "deploy/key.pem", "id_ed25519",
                    "certs/server.key", ".ssh/config"):
            with self.assertRaisesRegex(WorkspaceError, "secrets"):
                self.rw.write_file(bad, "SECRET=1")
            self.assertFalse((self.rw.path / bad).exists())
        self.rw.write_file(".env.example", "KEY=\n")  # templates are fine
        (self.rw.path / ".env").write_text("GEMINI_API_KEY=x\n")
        with self.assertRaisesRegex(WorkspaceError, "secrets"):
            self.rw.read_file(".env")
        self.assertNotIn(".env", self.rw.list_files("*")["files"])
        self.assertEqual(self.rw.search_code("GEMINI_API_KEY")["matches"], [])
        with self.assertRaisesRegex(WorkspaceError, "limit"):
            self.rw.write_file("big.txt", "x" * 500_000)
        (self.rw.path / "big.txt").write_text("x" * 500_000)
        with self.assertRaisesRegex(WorkspaceError, "too large"):
            self.rw.read_file("big.txt")

    def test_long_reads_are_cut(self):
        self.rw.write_file("long.txt", "".join(f"line {i} " + "y" * 100 + "\n" for i in range(1000)))
        out = self.rw.read_file("long.txt")
        self.assertIn("start_line=", out["note"])
        self.assertLess(len(out["content"]), 31_000)

    def test_checks_are_an_allowlist_with_a_timeout(self):
        with self.assertRaisesRegex(WorkspaceError, "unknown check 'rm'.*tests"):
            self.rw.run_check("rm")
        failed = self.rw.run_check("tests")
        self.assertFalse(failed["passed"])
        self.assertIn("AssertionError", failed["output"])
        self.rw.edit_file("calc.py", "return a - b", "return a + b")
        self.assertTrue(self.rw.run_check("tests")["passed"])
        self.ws.config = WorkspaceConfig(
            remote=str(self.origin), root=self.config.root,
            checks=[{"name": "slow", "command": "echo started; sleep 30", "timeoutSeconds": 1},
                    {"name": "env", "command": "env"}],
        )
        slow = self.rw.run_check("slow")
        self.assertEqual((slow["passed"], slow["timedOut"]), (False, True))
        self.assertLess(slow["seconds"], 10)
        self.assertIn("started", slow["output"])
        os.environ["GEMINI_API_KEY_TEST_LEAK"] = "sekrit"
        self.addCleanup(os.environ.pop, "GEMINI_API_KEY_TEST_LEAK", None)
        self.assertNotIn("sekrit", self.rw.run_check("env")["output"])

    def test_commit_identity_and_trailers(self):
        self.rw.edit_file("calc.py", "return a - b", "return a + b")
        out = self.rw.commit("Fix add\n\nIt subtracted.")
        self.assertEqual(out["files"], ["calc.py"])
        log = git(self.rw.path, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B").stdout
        self.assertTrue(log.startswith("fibr <vectra14@gmail.com>|fibr <vectra14@gmail.com>|Fix add"), log)
        self.assertIn("Agent-Run: run-1\nTask: proj/t", log)
        with self.assertRaisesRegex(WorkspaceError, "nothing to commit"):
            self.rw.commit("again")
        (self.rw.path / "deploy").mkdir()
        (self.rw.path / "deploy" / "prod.pem").write_text("KEY")
        (self.rw.path / "ok.txt").write_text("ok")
        with self.assertRaisesRegex(WorkspaceError, "secrets: deploy/prod.pem"):
            self.rw.commit("Add files")
        self.assertEqual(len(self.rw.ahead()), 1)


class LandingTest(RepoCase):
    def fix(self, rw, message="Fix add"):
        rw.edit_file("calc.py", "return a - b", "return a + b")
        rw.commit(message)

    def test_rebases_onto_new_upstream_commits_and_pushes(self):
        rw = self.ws.start_run("run-1", "t")
        self.fix(rw)
        self.push_upstream({"README.md": "# calc\n\nMeanwhile.\n"}, "Upstream docs")
        landed = rw.land()
        self.assertTrue(landed["pushed"])
        self.assertEqual(self.origin_log(), ["Fix add", "Upstream docs", "Initial"])
        self.assertEqual([c["subject"] for c in landed["commits"]], ["Fix add"])
        self.assertEqual(landed["after"], self.origin_head())
        self.assertEqual(landed["files"], [{"path": "calc.py", "added": 1, "removed": 1}])
        self.assertEqual(landed["checks"][0]["check"], "tests")
        body = git(self.origin, "log", "-1", "--format=%an|%B", "main").stdout
        self.assertTrue(body.startswith("fibr|Fix add"))
        self.assertIn("Agent-Run: run-1", body)

    def test_nothing_to_push(self):
        rw = self.ws.start_run("run-1", "t")
        self.assertEqual(rw.land(), {"pushed": False, "commits": []})

    def test_uncommitted_changes_block_landing(self):
        rw = self.ws.start_run("run-1", "t")
        rw.write_file("x.txt", "x")
        with self.assertRaises(LandingError) as cm:
            rw.land()
        self.assertEqual(cm.exception.kind, "dirty")

    def test_conflict_aborts_and_pushes_nothing(self):
        rw = self.ws.start_run("run-1", "t")
        self.fix(rw)
        theirs = self.push_upstream({"calc.py": CALC.replace("a - b", "b + a")}, "Their fix")
        with self.assertRaises(LandingError) as cm:
            rw.land()
        self.assertEqual(cm.exception.kind, "conflict")
        self.assertIn("calc.py", str(cm.exception))
        self.assertEqual(self.origin_head(), theirs)
        self.assertEqual(rw.dirty(), [])  # rebase aborted, worktree clean
        self.assertEqual(rw.ahead(rw.base)[0]["subject"], "Fix add")

    def test_failing_checks_block_the_push(self):
        rw = self.ws.start_run("run-1", "t")
        rw.edit_file("calc.py", "return a * b", "return a ** b")  # breaks mul
        rw.commit("Break mul")
        before = self.origin_head()
        with self.assertRaises(LandingError) as cm:
            rw.land()
        self.assertEqual(cm.exception.kind, "checks")
        self.assertIn("FAILED", cm.exception.output)
        self.assertEqual(self.origin_head(), before)

    def test_carry_over_unpushed_work_of_the_previous_run(self):
        first = self.ws.start_run("run-1", "t")
        self.fix(first)  # run-1 failed: its commit was never pushed
        self.push_upstream({"NEWS": "n\n"}, "Upstream news")
        second = self.ws.start_run("run-2", "t", ["run-1"])
        self.assertEqual(len(second.carried), 1)
        self.assertIn("return a + b", (second.path / "calc.py").read_text())
        self.assertTrue((second.path / "NEWS").exists())
        self.assertIn("Carried over 1", second.notes[0])
        second.land()
        # Once pushed, a later attempt starts from main with nothing to carry.
        third = self.ws.start_run("run-3", "t", ["run-2", "run-1"])
        self.assertEqual((third.carried, third.base), ([], self.origin_head()))


class EmptyRepoTest(RepoCase):
    empty = True

    def test_first_commit_to_an_empty_repository(self):
        self.ws.config = WorkspaceConfig(remote=str(self.origin), root=self.config.root)
        rw = self.ws.start_run("run-1", "t")
        self.assertIsNone(rw.base)
        self.assertEqual(rw.status()["base"], "(empty repository)")
        self.assertEqual(rw.list_files()["files"], [])
        rw.write_file("README.md", "# jfloor\n")
        self.assertIn("README.md", rw.diff()["stat"])
        rw.commit("Start the project")
        landed = rw.land()
        self.assertEqual((landed["pushed"], landed["before"]), (True, None))
        self.assertEqual(self.origin_log(), ["Start the project"])
        nxt = self.ws.start_run("run-2", "t")
        self.assertEqual(nxt.base, self.origin_head())


# ---- the worker: fake Gemini edits, checks, commits and pushes -----------------


def call(name, args):
    return NS(type="function_call", name=name, arguments=args, id=f"c-{name}")


def reply(*steps):
    return NS(id="i", status="requires_action", steps=list(steps), output_text=None)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.interactions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return NS(**{**vars(item), "id": f"i{len(self.calls)}"})


class WorkerTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.db = Database(str(self.dir / "o.db"))
        self.addCleanup(self.db.close)
        self.svc = TaskGraphService()
        self.svc.submit(H, [CreateTask(id="fix", title="Fix the failing test in calc.py")])
        self.runlog = RunLog(self.db)
        self.pads = Scratchpads(self.db)

    def runner(self, client):
        return GeminiRunner(self.svc, self.runlog, self.pads, client_factory=lambda: client,
                            builtin_tools=[], retry_delays=(), workspace=lambda: self.config,
                            project_id="proj")

    def test_fake_gemini_edits_commits_and_pushes(self):
        client = FakeClient([
            reply(call("search_code", {"pattern": "def add"})),
            reply(call("read_file", {"path": "calc.py"})),
            reply(call("edit_file", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})),
            reply(call("run_check", {"name": "tests"})),
            reply(call("commit", {"message": "Fix add: return the sum"})),
            reply(call("git_diff", {"stat_only": True})),
            reply(call("finish_run", {"outcome": "succeeded", "summary": "Fixed add."})),
        ])
        runner = self.runner(client)
        run = RunManager(self.svc, runner).start(H, "fix", "go", agent_id="gemini")
        self.assertTrue(runner.join(run.id, 30))
        ended = self.svc.run(run.id)
        self.assertEqual(ended.status, "succeeded", ended.note)
        self.assertIn("Pushed to main:", ended.note)
        self.assertEqual(self.origin_log(), ["Fix add: return the sum", "Initial"])
        first = client.calls[0]
        names = {t.get("name") for t in first["tools"]}
        self.assertLessEqual({"list_files", "read_file", "search_code", "write_file", "edit_file",
                              "delete_file", "git_status", "git_diff", "run_check", "commit",
                              "finish_run"}, names)
        self.assertIn("Working on the repository", first["system_instruction"])
        self.assertIn("## Repository", first["input"])
        self.assertIn("- tests:", first["input"])
        results = {c["input"][0]["name"]: c["input"][0]["result"] for c in client.calls[1:]}
        self.assertIn("calc.py:1:def add", results["search_code"])
        self.assertIn('"passed": true', results["run_check"])
        self.assertIn("calc.py", results["git_diff"])
        steps = self.runlog.steps(run.id)
        landing = [s for s in steps if s["kind"] == "landing"]
        self.assertEqual(landing[0]["payload"]["state"], "pushed")
        self.assertEqual(landing[0]["payload"]["commits"][0]["subject"], "Fix add: return the sum")
        self.assertEqual(steps[1]["payload"]["state"], "workspace")
        task = self.svc.task("fix")
        self.assertEqual(task["status"], "in-review")
        self.assertIn("Pushed 1 commit to main", [a for a in task["activity"] if a["op"] == "comment.add"][-1]["text"])

    def test_a_reseeded_conversation_sees_the_checkout(self):
        sys.path.insert(0, os.path.dirname(__file__))
        from test_gemini_engine import THOUGHT_400, FakeAPIError

        client = FakeClient([
            reply(call("edit_file", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})),
            reply(call("commit", {"message": "Fix add"})),
            reply(call("write_file", {"path": "NOTES.md", "content": "wip\n"})),
            FakeAPIError(400, THOUGHT_400),
            reply(call("finish_run", {"outcome": "failed", "summary": "stop here"})),
        ])
        runner = self.runner(client)
        runner.retry_delays = (0.01, 0.01)
        run = RunManager(self.svc, runner).start(H, "fix", "go")
        self.assertTrue(runner.join(run.id, 30))
        recap = client.calls[-1]["input"]
        self.assertIn("## Your repository checkout now", recap)
        self.assertIn("Fix add", recap)
        self.assertIn("NOTES.md", recap)
        self.assertIn("## Repository", recap)  # the original brief's section

    def test_no_repository_no_code_tools(self):
        client = FakeClient([reply(call("finish_run", {"outcome": "succeeded", "summary": "ok"}))])
        runner = self.runner(client)
        runner.workspace = lambda: None
        run = RunManager(self.svc, runner).start(H, "fix", "go")
        self.assertTrue(runner.join(run.id, 10))
        names = {t.get("name") for t in client.calls[0]["tools"]}
        self.assertNotIn("read_file", names)
        self.assertNotIn("## Working on the repository", client.calls[0]["system_instruction"])

    def tools(self, rw, run_id):
        return AgentTools(None, self.svc, agent_id="gemini",
                          toolset=lambda c: worker_tools(c, run_id=run_id, task_id="fix"),
                          runs=RunManager(self.svc), runlog=self.runlog, workspace=rw)

    def test_failing_checks_get_one_fix_attempt(self):
        run = RunManager(self.svc).start(H, "fix", "go")
        rw = self.ws.start_run(run.id, "fix")
        tools = self.tools(rw, run.id)
        tools.dispatch("edit_file", {"path": "calc.py", "old": "return a * b", "new": "return a ** b"})
        self.assertIn("Uncommitted", str(tools.dispatch("finish_run", {"outcome": "succeeded", "summary": "s"})).title())
        tools.dispatch("commit", {"message": "Break mul"})
        out = tools.dispatch("finish_run", {"outcome": "succeeded", "summary": "s"})
        self.assertTrue(out["error"].startswith("checks_failed: check 'tests' failed"), out)
        self.assertTrue(self.svc.run(run.id).active)  # the agent may fix it
        out = tools.dispatch("finish_run", {"outcome": "succeeded", "summary": "s"})
        self.assertFalse(out["pushed"])
        ended = self.svc.run(run.id)
        self.assertEqual(ended.status, "failed")
        self.assertIn("Not pushed: check 'tests' failed", ended.note)
        self.assertEqual(self.origin_log(), ["Initial"])

    def test_failed_outcome_pushes_nothing(self):
        run = RunManager(self.svc).start(H, "fix", "go")
        rw = self.ws.start_run(run.id, "fix")
        tools = self.tools(rw, run.id)
        tools.dispatch("edit_file", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})
        tools.dispatch("commit", {"message": "Fix add"})
        tools.dispatch("finish_run", {"outcome": "failed", "summary": "gave up"})
        self.assertEqual(self.svc.run(run.id).status, "failed")
        self.assertEqual(self.origin_log(), ["Initial"])
        self.assertEqual(len(rw.ahead()), 1)  # stays in the worktree until cleanup


class WorkspaceApiTest(unittest.TestCase):
    def test_get_put_clear(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Database(os.path.join(tmp.name, "o.db"))
        self.addCleanup(db.close)
        reg = ProjectRegistry(db)
        app = FastAPI()
        app.include_router(build_router(reg, lambda: []))
        http = TestClient(app)
        self.assertEqual(http.get("/api/projects/orchestrators/workspace").json()["workspace"], None)
        body = {"remote": "git@github-orchestrators:kyrill99/Orchestrators.git",
                "root": "/home/orchestrators/workspaces/orchestrators",
                "checks": [{"name": "backend-tests", "command": "true"}]}
        out = http.put("/api/projects/orchestrators/workspace", json=body).json()
        self.assertEqual(out["webUrl"], "https://github.com/kyrill99/Orchestrators")
        self.assertEqual(out["workspace"]["branch"], "main")
        self.assertEqual(out["workspace"]["checks"][0]["timeoutSeconds"], 600)
        self.assertEqual(ProjectRegistry(db).workspace("orchestrators").checks[0].name, "backend-tests")
        self.assertEqual(http.put("/api/projects/orchestrators/workspace",
                                  json={**body, "root": "relative/path"}).status_code, 422)
        self.assertEqual(http.put("/api/projects/orchestrators/workspace",
                                  json={**body, "checks": [{"name": "a b", "command": "x"}]}).status_code, 422)
        self.assertEqual(http.put("/api/projects/nope/workspace", json=body).status_code, 404)
        self.assertEqual(http.get("/api/projects/nope/workspace").status_code, 404)
        http.put("/api/projects/orchestrators/workspace", content="null",
                 headers={"content-type": "application/json"})
        self.assertIsNone(reg.workspace("orchestrators"))


if __name__ == "__main__":
    unittest.main()
