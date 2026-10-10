"""Issue list: service, REST (drafts, attachments), MCP and Gemini agent tools."""

import asyncio
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from db import Database  # noqa: E402
from issues import (  # noqa: E402
    MAX_ATTACHMENT_BYTES,
    Author,
    IssueError,
    IssueService,
    agent_author,
    build_router,
)

ANA = {"X-Forwarded-Email": "ana@example.com"}
BOB = {"X-Forwarded-Email": "bob@example.com"}


def tiny_png() -> bytes:
    """A valid 1x1 PNG."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = Database(os.path.join(self.dir.name, "t.db"))
        self.addCleanup(self.db.close)
        self.files = os.path.join(self.dir.name, "issues")
        self.svc = IssueService(self.db, self.files)


class IssueServiceTest(_Base):
    def test_numbers_are_assigned_on_publish(self):
        ana = Author("ana@example.com", "ana")
        first = self.svc.create(ana, "First", status="open")
        draft = self.svc.create(ana, status="draft")
        second = self.svc.create(ana, "Second")
        self.assertEqual((first["number"], draft["number"], second["number"]), (1, None, 2))
        with self.assertRaisesRegex(IssueError, "title"):
            self.svc.publish(ana, draft["id"])
        self.svc.update(ana, draft["id"], title="Third")
        self.assertEqual(self.svc.publish(ana, draft["id"])["number"], 3)
        self.assertEqual(self.svc.get("#3")["title"], "Third")
        self.assertEqual(self.svc.get(2)["title"], "Second")

    def test_update_status_rules(self):
        ana = Author("ana@example.com", "ana")
        issue = self.svc.create(ana, "Bug")
        self.assertEqual(self.svc.update(ana, issue["id"], status="in_progress")["status"], "in_progress")
        with self.assertRaisesRegex(IssueError, "use close"):
            self.svc.update(ana, issue["id"], status="closed")
        with self.assertRaises(IssueError):
            self.svc.update(ana, issue["id"], title="  ")

    def test_list_filters(self):
        ana, bob = Author("ana@example.com", "ana"), Author("bob@example.com", "bob")
        self.svc.create(ana, "Login button broken", labels=["ui", "ui", " "])
        self.svc.create(bob, "Slow graph", description="the planner view lags")
        self.svc.create(bob, status="draft")
        self.assertEqual(len(self.svc.list_issues()), 2)
        self.assertEqual(len(self.svc.list_issues("all")), 3)
        self.assertEqual([i["title"] for i in self.svc.list_issues(search="planner")], ["Slow graph"])
        self.assertEqual([i["number"] for i in self.svc.list_issues(search="#1")], [1])
        self.assertEqual(len(self.svc.list_issues(author="BOB@example.com")), 1)
        self.assertEqual([i["labels"] for i in self.svc.list_issues(label="ui")], [["ui"]])
        with self.assertRaises(IssueError):
            self.svc.list_issues("bogus")


class IssueApiTest(_Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_router(self.svc))
        self.client = TestClient(app)

    def upload(self, issue_id, data, name="shot.png", ctype="image/png"):
        return self.client.post(
            f"/api/issues/{issue_id}/attachments", headers=ANA, files={"file": (name, data, ctype)}
        )

    def test_crud_and_close_reopen(self):
        c = self.client
        r = c.post("/api/issues", json={"title": "Broken link", "description": "**bold**"}, headers=ANA)
        self.assertEqual(r.status_code, 201)
        issue = r.json()
        self.assertEqual((issue["status"], issue["number"]), ("open", 1))
        self.assertEqual(issue["author"], {"email": "ana@example.com", "name": "ana"})

        r = c.patch(f"/api/issues/{issue['id']}", json={"labels": ["bug"], "assignee": "agent:worker"}, headers=BOB)
        self.assertEqual((r.json()["labels"], r.json()["assignee"]), (["bug"], "agent:worker"))

        r = c.post(f"/api/issues/{issue['id']}/comments", json={"body": "On it"}, headers=BOB)
        self.assertEqual(r.json()["author"]["email"], "bob@example.com")
        self.assertEqual(len(c.get(f"/api/issues/{issue['id']}/comments").json()), 1)

        r = c.post(f"/api/issues/{issue['id']}/close", json={"reason": "fixed"}, headers=BOB)
        closed = r.json()
        self.assertEqual((closed["status"], closed["close_reason"]), ("closed", "fixed"))
        self.assertEqual(closed["closed_by"]["email"], "bob@example.com")
        self.assertEqual(c.post(f"/api/issues/{issue['id']}/close", headers=BOB).status_code, 409)
        self.assertEqual(c.delete(f"/api/issues/{issue['id']}", headers=ANA).status_code, 409)

        reopened = c.post("/api/issues/1/reopen", headers=ANA).json()
        self.assertEqual((reopened["status"], reopened["closed_at"], reopened["close_reason"]), ("open", None, None))

        self.assertEqual(c.get("/api/issues/nope").status_code, 404)
        self.assertEqual(c.post("/api/issues", json={"title": ""}).status_code, 422)

    def test_draft_publish_and_discard(self):
        c = self.client
        draft = c.post("/api/issues", json={"status": "draft", "context": {"url": "/project"}}, headers=ANA).json()
        self.assertEqual((draft["status"], draft["number"]), ("draft", None))
        att = self.upload(draft["id"], tiny_png()).json()
        path = os.path.join(self.files, draft["id"], f"{att['id']}.png")
        self.assertTrue(os.path.exists(path))

        mine = c.get("/api/issues?status=draft&author=me", headers=ANA).json()
        self.assertEqual([i["id"] for i in mine], [draft["id"]])
        self.assertEqual(c.get("/api/issues?status=draft&author=me", headers=BOB).json(), [])
        self.assertEqual(c.get("/api/issues").json(), [])  # drafts are not listed by default
        self.assertEqual(c.post(f"/api/issues/{draft['id']}/close").status_code, 409)

        self.assertEqual(c.post(f"/api/issues/{draft['id']}/publish").status_code, 422)  # no title
        c.patch(f"/api/issues/{draft['id']}", json={"title": "Chart overlaps"}, headers=ANA)
        published = c.post(f"/api/issues/{draft['id']}/publish", headers=ANA).json()
        self.assertEqual((published["status"], published["number"]), ("open", 1))
        self.assertEqual(published["context"], {"url": "/project"})
        self.assertEqual(c.post(f"/api/issues/{draft['id']}/publish").status_code, 409)

        other = c.post("/api/issues", json={"status": "draft"}, headers=ANA).json()
        self.upload(other["id"], tiny_png())
        self.assertEqual(c.delete(f"/api/issues/{other['id']}", headers=ANA).status_code, 204)
        self.assertEqual(c.get(f"/api/issues/{other['id']}").status_code, 404)
        self.assertFalse(os.path.exists(os.path.join(self.files, other["id"])))

    def test_attachment_validation_and_serving(self):
        issue = self.client.post("/api/issues", json={"title": "Shots"}, headers=ANA).json()
        png = tiny_png()
        r = self.upload(issue["id"], png)
        self.assertEqual(r.status_code, 201)
        att = r.json()
        self.assertEqual((att["content_type"], att["size"]), ("image/png", len(png)))

        got = self.client.get(att["url"])
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.content, png)
        self.assertEqual(got.headers["content-type"], "image/png")
        self.assertEqual(got.headers["x-content-type-options"], "nosniff")

        listed = self.client.get("/api/issues").json()[0]
        self.assertEqual(listed["attachments"][0]["url"], att["url"])

        # text pretending to be a png, an svg, an oversized file
        self.assertEqual(self.upload(issue["id"], b"hello world").status_code, 422)
        self.assertEqual(self.upload(issue["id"], b"<svg/>", "x.svg", "image/svg+xml").status_code, 422)
        big = png + b"\0" * MAX_ATTACHMENT_BYTES
        self.assertEqual(self.upload(issue["id"], big).status_code, 413)
        # jpeg and webp by magic bytes
        self.assertEqual(self.upload(issue["id"], b"\xff\xd8\xff\xe0" + b"\0" * 20, "a.jpg", "image/jpeg").json()["content_type"], "image/jpeg")
        webp = b"RIFF" + b"\0\0\0\0" + b"WEBPVP8 " + b"\0" * 20
        self.assertEqual(self.upload(issue["id"], webp, "a.webp", "image/webp").json()["content_type"], "image/webp")

        self.assertEqual(self.client.delete(att["url"], headers=ANA).status_code, 204)
        self.assertEqual(self.client.get(att["url"]).status_code, 404)
        self.assertEqual(self.client.get(f"/api/issues/{issue['id']}/attachments/att_x").status_code, 404)


class IssueAgentToolsTest(_Base):
    def test_gemini_tools(self):
        from memory.service import MemoryService
        from memory.store import MarkdownMemoryStore
        from services.agent_tools import TOOL_DECLARATIONS, AgentTools
        from taskgraph import TaskGraphService

        mem = MemoryService(MarkdownMemoryStore(os.path.join(self.dir.name, "mem")))
        plain = AgentTools(mem, TaskGraphService(), agent_id="interview")
        self.assertEqual(plain.declarations(), TOOL_DECLARATIONS)

        tools = AgentTools(mem, TaskGraphService(), agent_id="worker", issues=self.svc)
        names = [t["name"] for t in tools.declarations()]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual({"list_issues", "get_issue", "update_issue", "comment_on_issue", "close_issue"}, set(names))

        issue = self.svc.create(Author("ana@example.com", "ana"), "Crash on save")
        self.svc.add_attachment(Author("ana@example.com", "ana"), issue["id"], "s.png", tiny_png())
        self.assertEqual(tools.dispatch("list_issues", {})[0]["title"], "Crash on save")
        got = tools.dispatch("get_issue", {"issue": "#1"})
        self.assertTrue(os.path.exists(got["attachments"][0]["path"]))
        self.assertEqual(tools.dispatch("update_issue", {"issue": "1", "status": "in_progress"})["status"], "in_progress")
        comment = tools.dispatch("comment_on_issue", {"issue": "#1", "body": "Found it"})
        self.assertEqual(comment["author"], {"email": "agent:worker", "name": "worker"})
        closed = tools.dispatch("close_issue", {"issue": "#1", "reason": "fixed in abc123"})
        self.assertEqual((closed["status"], closed["closed_by"]["email"]), ("closed", "agent:worker"))
        self.assertIn("conflict", tools.dispatch("close_issue", {"issue": "#1", "reason": "again"})["error"])
        self.assertIn("not_found", tools.dispatch("get_issue", {"issue": "#9"})["error"])
        self.assertEqual(tools.dispatch("reopen_issue", {"issue": "#1"})["status"], "open")
        self.assertEqual(tools.dispatch("create_issue", {"title": "Agent bug"})["author"]["email"], "agent:worker")

    def test_mcp_tools(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from taskgraph import TaskGraphService
        from taskgraph.mcp_server import build_server

        server = build_server(TaskGraphService(), agent_id="planner", issues=self.svc)
        issue = self.svc.create(Author("ana@example.com", "ana"), "Graph is empty")
        att = self.svc.add_attachment(Author("ana@example.com", "ana"), issue["id"], "s.png", tiny_png())

        async def call(name, args):
            res = await server.call_tool(name, args)
            self.assertFalse(res.is_error, res.content)
            if res.structured_content is not None and "result" in res.structured_content:
                return res.structured_content["result"]
            return json.loads(res.content[0].text)

        async def go():
            names = {t.name for t in await server.list_tools()}
            self.assertLessEqual({"list_issues", "get_issue", "update_issue", "comment_on_issue",
                                  "close_issue", "reopen_issue", "get_issue_screenshot",
                                  "start_task", "add_comment"}, names)
            self.assertEqual(len(await call("list_issues", {})), 1)
            got = await call("get_issue", {"issue": "#1"})
            self.assertEqual(got["attachments"][0]["id"], att["id"])
            await call("comment_on_issue", {"issue": "#1", "body": "looking"})
            await call("update_issue", {"issue": "#1", "labels": ["graph"]})
            closed = await call("close_issue", {"issue": "#1", "reason": "fixed"})
            self.assertEqual(closed["closed_by"]["email"], "agent:planner")
            shot = await server.call_tool("get_issue_screenshot", {"issue": "#1", "attachment_id": att["id"]})
            self.assertEqual(shot.content[0].type, "image")
            self.assertEqual(shot.content[0].mime_type, "image/png")
            with self.assertRaisesRegex(ToolError, "not_found"):
                await server.call_tool("get_issue", {"issue": "#42"})

        asyncio.run(go())
        self.assertEqual(self.svc.get("#1")["comments"][0]["author"]["email"], "agent:planner")
        self.assertEqual(agent_author("x").email, "agent:x")


if __name__ == "__main__":
    unittest.main()
