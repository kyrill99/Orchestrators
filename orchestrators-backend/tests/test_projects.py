"""Projects: registry, cookie -> contextvar middleware, per-project graphs via proxies."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import Database  # noqa: E402
from projects import (  # noqa: E402
    DEFAULT, ORCHESTRATORS, ProjectGraphs, ProjectMiddleware, ProjectRegistry, ScopedProxy,
    current_project, project_from_cookie,
)
from taskgraph import Actor, RecordRunner, RunManager, TaskGraphService  # noqa: E402
from taskgraph.loop import auto_start  # noqa: E402
from taskgraph.models import CreateTask  # noqa: E402
from taskgraph.sqlite_store import SqliteEventStore  # noqa: E402

H = Actor(type="human", id="lorenz")


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = Database(os.path.join(self.dir.name, "main.db"))
        self.addCleanup(self.db.close)
        self.registry = ProjectRegistry(self.db)

        def pair(pid):
            pdb = Database(os.path.join(self.dir.name, f"{pid}.db"))
            self.addCleanup(pdb.close)
            svc = TaskGraphService(SqliteEventStore(pdb))
            return svc, RunManager(svc, RecordRunner())

        default = TaskGraphService()
        self.graphs = ProjectGraphs(self.registry, pair, (default, RunManager(default, RecordRunner())))
        self.svc = ScopedProxy(self.graphs.service)
        self.runs = ScopedProxy(self.graphs.manager)

    def in_project(self, pid, fn):
        token = current_project.set(pid)
        try:
            return fn()
        finally:
            current_project.reset(token)

    def test_seeded_and_create(self):
        ids = [p["id"] for p in self.registry.list()]
        self.assertEqual(ids[:2], [DEFAULT, ORCHESTRATORS])
        p = self.registry.create("Snake Game", "Tiny browser game", "a@b.c")
        self.assertEqual(p["id"], "snake-game")
        self.assertEqual(self.registry.create("Snake Game")["id"], "snake-game-2")
        self.assertIn("Tiny browser game", self.registry.brief("snake-game"))
        self.registry.update("snake-game", description="New brief")
        self.assertIn("New brief", self.registry.brief("snake-game"))

    def test_graphs_are_isolated_per_project(self):
        self.registry.create("Game")
        self.in_project("game", lambda: self.svc.submit(H, [CreateTask(id="g1", title="Make game")]))
        self.in_project(DEFAULT, lambda: self.svc.submit(H, [CreateTask(id="d1", title="Default task")]))
        game = self.in_project("game", lambda: [t["id"] for t in self.svc.graph()["tasks"]])
        default = self.in_project(DEFAULT, lambda: [t["id"] for t in self.svc.graph()["tasks"]])
        self.assertEqual(game, ["g1"])
        self.assertEqual(default, ["d1"])
        # runs go to the same project, also through the proxies used by the REST loop
        started = self.in_project("game", lambda: auto_start(self.svc, self.runs, H))
        self.assertEqual(started, ["g1"])
        self.assertEqual(self.graphs.service("game").task("g1")["status"], "running")
        self.assertEqual(self.graphs.service(DEFAULT).task("d1")["status"], "open")

    def test_unknown_project_falls_back_to_default(self):
        self.assertIs(self.in_project("nope", lambda: self.graphs.service()), self.graphs.service(DEFAULT))

    def test_cookie_parsing(self):
        self.registry.create("Game")
        self.assertEqual(project_from_cookie("x=1; orch_project=game", self.registry), "game")
        self.assertEqual(project_from_cookie("orch_project=missing", self.registry), DEFAULT)
        self.assertEqual(project_from_cookie("garbage;;==", self.registry), DEFAULT)
        self.assertEqual(project_from_cookie(None, self.registry), DEFAULT)

    def test_middleware_sets_context_per_request(self):
        self.registry.create("Game")
        seen = []

        async def app(scope, receive, send):
            seen.append(current_project.get())

        mw = ProjectMiddleware(app, registry=self.registry)

        async def go():
            await mw({"type": "http", "headers": [(b"cookie", b"orch_project=game")]}, None, None)
            await mw({"type": "http", "headers": []}, None, None)

        asyncio.run(go())
        self.assertEqual(seen, ["game", DEFAULT])
        self.assertEqual(current_project.get(), DEFAULT)


if __name__ == "__main__":
    unittest.main()
