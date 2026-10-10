import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from db import Database  # noqa: E402
from identity import build_router  # noqa: E402


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "sub", "test.db")
        self.db = Database(self.path)
        with self.db.tx() as c:
            c.execute("CREATE TABLE t (v INTEGER)")

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    def test_rollback_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.db.tx() as c:
                c.execute("INSERT INTO t VALUES (1)")
                raise RuntimeError
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM t").fetchone()[0], 0)

    def test_second_process_connection_sees_writes(self):
        other = Database(self.path)
        with self.db.tx() as c:
            c.execute("INSERT INTO t VALUES (7)")
        with other.read() as c:
            self.assertEqual(c.execute("SELECT v FROM t").fetchone()["v"], 7)
        other.close()

    def test_threads(self):
        def work():
            for _ in range(50):
                with self.db.tx() as c:
                    c.execute("INSERT INTO t VALUES (1)")
        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM t").fetchone()[0], 200)


class IdentityTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(build_router())
        self.client = TestClient(app)

    def test_proxy_headers(self):
        me = self.client.get("/api/me", headers={"X-Forwarded-Email": "Lorenz@Example.com"}).json()
        self.assertEqual(me, {"email": "lorenz@example.com", "name": "lorenz"})

    def test_local_fallback(self):
        self.assertEqual(self.client.get("/api/me").json()["email"], os.environ.get("DEV_USER_EMAIL", "local@localhost"))


if __name__ == "__main__":
    unittest.main()
