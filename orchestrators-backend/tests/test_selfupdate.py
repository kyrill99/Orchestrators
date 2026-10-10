import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from selfupdate import REQUEST, STATUS, build_router  # noqa: E402


class SelfUpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.data = Path(self.dir.name) / "data"
        app = FastAPI()
        app.include_router(build_router(self.data))
        self.client = TestClient(app)

    def tearDown(self):
        self.dir.cleanup()

    def test_status_without_history(self):
        body = self.client.get("/api/admin/update").json()
        self.assertFalse(body["requested"])
        self.assertIsNone(body["last"])

    def test_post_drops_request_file(self):
        res = self.client.post("/api/admin/update")
        self.assertEqual(res.status_code, 202)
        self.assertTrue(res.json()["requested"])
        self.assertTrue((self.data / REQUEST).exists())

    def test_reports_last_run(self):
        self.data.mkdir()
        (self.data / STATUS).write_text(json.dumps({"state": "ok", "to": "abc1234"}))
        self.assertEqual(self.client.get("/api/admin/update").json()["last"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
