import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from changelog import MAX_LIMIT, build_router  # noqa: E402
from selfupdate import STATUS  # noqa: E402

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Ada Lovelace",
    "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_COMMITTER_NAME": "Ada Lovelace",
    "GIT_COMMITTER_EMAIL": "ada@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


class ChangelogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.repo = root / "repo"
        self.data = root / "data"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        app = FastAPI()
        app.include_router(build_router(self.data, self.repo))
        self.client = TestClient(app)

    def tearDown(self):
        self.dir.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=ENV,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def get(self, **params) -> dict:
        res = self.client.get("/api/admin/changelog", params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_lists_commits_newest_first(self):
        self.commit("First")
        head = self.commit("Second\n\nWhy it matters.")
        body = self.get()
        self.assertEqual([c["subject"] for c in body["commits"]], ["Second", "First"])
        top = body["commits"][0]
        self.assertEqual(top["hash"], head)
        self.assertEqual(body["commit"], top["short"])
        self.assertEqual(top["author"], "Ada Lovelace")
        self.assertEqual(top["body"], "Why it matters.")
        self.assertIsNone(body["commits"][1]["body"])
        self.assertRegex(top["date"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual(body["pending"], [])

    def test_special_characters_survive(self):
        subject = 'Quote " pipe | tab\there %x00 {braces} ünïcode'
        self.commit(subject + "\n\nline one\n\nline two")
        top = self.get()["commits"][0]
        self.assertEqual(top["subject"], subject)
        self.assertEqual(top["body"], "line one\n\nline two")

    def test_limit_is_clamped(self):
        for i in range(5):
            self.commit(f"c{i}")
        self.assertEqual(len(self.get(limit=2)["commits"]), 2)
        self.assertEqual(len(self.get(limit=0)["commits"]), 1)
        self.assertEqual(len(self.get(limit=MAX_LIMIT + 50)["commits"]), 5)

    def test_marks_commits_from_last_update(self):
        old = self.commit("Old")
        self.commit("New A")
        self.commit("New B")
        self.data.mkdir()
        (self.data / STATUS).write_text(json.dumps({"state": "ok", "from": old[:7]}))
        body = self.get()
        self.assertEqual(
            {c["subject"]: c["new"] for c in body["commits"]},
            {"New B": True, "New A": True, "Old": False},
        )
        self.assertEqual(body["since"], old[:7])

    def test_failed_update_marks_nothing(self):
        old = self.commit("Old")
        self.commit("New")
        self.data.mkdir()
        (self.data / STATUS).write_text(json.dumps({"state": "failed", "from": old[:7]}))
        body = self.get()
        self.assertFalse(any(c["new"] for c in body["commits"]))
        self.assertIsNone(body["since"])

    def test_pending_from_fetched_upstream(self):
        self.commit("Deployed")
        self.commit("Upstream only")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("reset", "-q", "--hard", "HEAD~1")
        body = self.get()
        self.assertEqual(body["upstream"], "origin/main")
        self.assertEqual([c["subject"] for c in body["pending"]], ["Upstream only"])
        self.assertEqual([c["subject"] for c in body["commits"]], ["Deployed"])

    def test_not_a_repo_is_503(self):
        app = FastAPI()
        app.include_router(build_router(self.data, Path(self.dir.name) / "data"))
        (Path(self.dir.name) / "data").mkdir()
        res = TestClient(app).get("/api/admin/changelog")
        self.assertEqual(res.status_code, 503)
        self.assertIn("git", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
