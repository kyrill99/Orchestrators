import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sysstats import build_router  # noqa: E402

MEMINFO = """MemTotal:        2000000 kB
MemFree:          100000 kB
MemAvailable:     500000 kB
SwapTotal:       1000000 kB
SwapFree:         750000 kB
"""


class SysStatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.proc = Path(self.dir.name)
        (self.proc / "meminfo").write_text(MEMINFO)
        app = FastAPI()
        app.include_router(build_router(self.proc))
        self.client = TestClient(app)

    def tearDown(self):
        self.dir.cleanup()

    def stat(self, user, idle):
        # cpu user nice system idle iowait irq softirq steal
        (self.proc / "stat").write_text(f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n")

    def test_memory(self):
        self.stat(10, 90)
        mem = self.client.get("/api/admin/system").json()["memory"]
        self.assertEqual(mem["total"], 2000000 * 1024)
        self.assertEqual(mem["used"], 1500000 * 1024)
        self.assertEqual(mem["swap_used"], 250000 * 1024)

    def test_cpu_is_delta_between_requests(self):
        self.stat(10, 90)
        self.assertEqual(self.client.get("/api/admin/system").json()["cpu"]["percent"], 10.0)
        self.stat(10 + 75, 90 + 25)
        self.assertEqual(self.client.get("/api/admin/system").json()["cpu"]["percent"], 75.0)

    def test_unavailable(self):
        self.assertEqual(self.client.get("/api/admin/system").status_code, 503)


if __name__ == "__main__":
    unittest.main()
