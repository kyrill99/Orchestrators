"""ArtifactStore.list_project: every artifact of a project, with versions."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import fastapi  # noqa: F401
except ImportError:  # the store needs no FastAPI; stub the router imports
    import types

    _stub = types.ModuleType("fastapi")
    _stub.APIRouter = _stub.HTTPException = _stub.Query = object
    _resp = types.ModuleType("fastapi.responses")
    _resp.FileResponse = object
    sys.modules["fastapi"] = _stub
    sys.modules["fastapi.responses"] = _resp

from artifacts import ArtifactStore  # noqa: E402
from db import Database  # noqa: E402

PAGE = "<!doctype html><html><body>x</body></html>"


class ListProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "o.db"))
        self.addCleanup(self.db.close)
        self.store = ArtifactStore(self.db, os.path.join(self.tmp.name, "files"))

    def save(self, project, task, name, run):
        meta, _ = self.store.save(project, task, name, PAGE, title=f"{name} {run}",
                                  run_id=run, created_by="worker")
        time.sleep(0.003)  # distinct created_at (millisecond resolution)
        return meta

    def test_versions(self):
        a1 = self.save("p", "t", "report.html", "r1")
        b1 = self.save("p", "t", "data.html", "r1")
        a2 = self.save("p", "t", "report.html", "r2")
        a3 = self.save("p", "t", "report.html", "r3")
        o1 = self.save("p", "u", "report.html", "r1")  # other task: separate artifact
        self.save("q", "t", "report.html", "r1")  # other project: not listed

        rows = {r["id"]: r for r in self.store.list_project("p")}
        self.assertEqual(set(rows), {a1["id"], b1["id"], a2["id"], a3["id"], o1["id"]})
        self.assertEqual([rows[x["id"]]["version"] for x in (a1, a2, a3)], [1, 2, 3])
        self.assertTrue(all(rows[x["id"]]["versions"] == 3 for x in (a1, a2, a3)))
        self.assertEqual([rows[x["id"]]["latest"] for x in (a1, a2, a3)], [False, False, True])
        for single in (b1, o1):
            r = rows[single["id"]]
            self.assertEqual((r["version"], r["versions"], r["latest"]), (1, 1, True))
        self.assertEqual(self.store.list_project("nope"), [])

    def test_same_run_replaces_not_versions(self):
        a = self.save("p", "t", "report.html", "r1")
        b = self.save("p", "t", "report.html", "r1")
        self.assertEqual(a["id"], b["id"])
        [row] = self.store.list_project("p")
        self.assertEqual((row["version"], row["versions"], row["latest"]), (1, 1, True))


if __name__ == "__main__":
    unittest.main()
