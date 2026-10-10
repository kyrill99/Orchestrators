import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory import ChatMessage, MarkdownMemoryStore, MemoryService, MemoryServiceError, ReflectOp  # noqa: E402
from taskgraph import Actor, TaskGraphService  # noqa: E402
from taskgraph.models import CreateTask, RequestDecision, ResolveDecision  # noqa: E402

H = Actor(type="human", id="lorenz")
A = Actor(type="agent", id="planner")
P, U = "chunking", "lorenz"


class FakeDistiller:
    def __init__(self, ops):
        self.ops = ops
        self.seen = None

    def distill(self, messages, existing):
        self.seen = (list(messages), list(existing))
        return self.ops


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.mem = MemoryService(MarkdownMemoryStore(self.dir.name))

    def remember(self, title, body="body", kind="learning", scope="project", **kw):
        return self.mem.remember(H, title=title, body=body, kind=kind, scope=scope,
                                 project_id=P, user_id=U, **kw)


class CoreTest(MemoryTestCase):
    def test_file_roundtrip(self):
        m = self.remember("Success criteria", "Coherence first: then latency.\n---\n- a", "strategy",
                          tags=["eval"])
        self.assertEqual(m.id, "projects.chunking.strategy.success-criteria")
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, "projects", "chunking",
                                                    "strategy", "success-criteria.md")))
        again = MemoryService(MarkdownMemoryStore(self.dir.name)).get(m.id)
        self.assertEqual(again, m)

    def test_same_title_updates(self):
        first = self.remember("No A-vs-B questions", "old", "preference", "user", tags=["a"])
        second = self.remember("No A-vs-B questions", "new", "preference", "user", tags=["b"])
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.mem.list_memories()), 1)
        self.assertEqual((second.body, second.tags), ("new", ["a", "b"]))

    def test_scopes_are_isolated(self):
        self.remember("Project goal", kind="strategy")
        self.remember("Owner pref", kind="preference", scope="user")
        self.remember("Lesson", scope="global")
        ids = self.mem.context(project_id="other", user_id=U)["memoryIds"]
        self.assertEqual(ids, ["users.lorenz.preference.owner-pref", "global.learning.lesson"])
        self.assertEqual(self.mem.context(project_id="other", user_id="eve")["memoryIds"],
                         ["global.learning.lesson"])

    def test_context_precedence_budget_and_uses(self):
        self.remember("Lesson", "g" * 50, "strategy", "global")
        self.remember("Owner pref", "u" * 50, "preference", "user")
        goal = self.remember("Project goal", "p" * 50, "strategy")
        ctx = self.mem.context(project_id=P, user_id=U)
        self.assertEqual([i.split(".")[0] for i in ctx["memoryIds"]], ["projects", "users", "global"])
        self.assertLess(ctx["text"].index("Project goal"), ctx["text"].index("Owner pref"))
        tight = self.mem.context(project_id=P, user_id=U, budget_chars=130)
        self.assertEqual(len(tight["memoryIds"]), 2)
        self.assertEqual(self.mem.get(goal.id).uses, 2)

    def test_search_ranks_by_match_and_confidence(self):
        a = self.remember("Chunk coherence metric", "how coherence is measured")
        b = self.remember("Latency budget", "p95 latency; coherence is secondary")
        self.remember("Unrelated", "nothing here")
        hits = self.mem.search("coherence metric", project_id=P, user_id=U)
        self.assertEqual([m.id for m in hits], [a.id, b.id])
        both = "Coherence"
        x = self.remember(both + " one", "x")
        y = self.remember(both + " two", "x")
        self.mem.feedback(H, y.id, True)
        hits = self.mem.search("one two", project_id=P, user_id=U)
        self.assertEqual([m.id for m in hits], [y.id, x.id])

    def test_feedback_archives_bad_memory(self):
        m = self.remember("Prefers latency", kind="preference", scope="user")
        self.assertEqual(self.mem.feedback(A, m.id, True).confidence, 0.6)
        self.mem.feedback(H, m.id, False)
        self.mem.feedback(H, m.id, False)
        self.assertEqual(self.mem.feedback(H, m.id, False).status, "archived")
        self.assertEqual(self.mem.context(project_id=P, user_id=U)["memoryIds"], [])
        self.assertEqual(self.mem.stats()["helpfulRate"], 0.25)
        self.assertEqual(self.mem.log()[-1]["event"], "feedback")

    def test_update_validates(self):
        m = self.remember("X")
        with self.assertRaises(Exception):
            self.mem.update(H, m.id, confidence=3)
        with self.assertRaises(MemoryServiceError) as cm:
            self.mem.update(H, "nope", body="b")
        self.assertEqual(cm.exception.code, "not_found")
        self.assertEqual(self.mem.archive(H, m.id).status, "archived")
        self.assertEqual(self.mem.tree(), [])

    def test_tree_matches_frontend_shape(self):
        self.remember("Success criteria", "- a", "strategy")
        (folder,) = self.mem.tree()
        self.assertEqual(folder["name"], "projects/chunking/strategy")
        f = folder["files"][0]
        self.assertEqual((f["name"], f["path"]), ("success-criteria.md",
                                                  "projects/chunking/strategy/success-criteria.md"))
        self.assertTrue(f["content"].startswith("# Success criteria"))

    def test_two_services_share_a_directory(self):
        other = MemoryService(MarkdownMemoryStore(self.dir.name))
        m = self.remember("Shared")
        other.feedback(A, m.id, True)
        self.assertEqual(self.mem.get(m.id).helpful, 1)


class ReflectTest(MemoryTestCase):
    def test_reflect_applies_ops(self):
        old = self.remember("Latency first", "optimize latency", "strategy")
        kept = self.remember("No A-vs-B questions", kind="preference", scope="user")
        fake = FakeDistiller([
            ReflectOp(op="create", scope="user", kind="preference", title="Short updates",
                      body="Keep progress updates to three lines."),
            ReflectOp(op="supersede", target_id=old.id, title="Coherence first",
                      body="Coherence beats latency.", reason="owner corrected"),
            ReflectOp(op="reinforce", target_id=kept.id),
            ReflectOp(op="update", target_id="missing", body="x"),
        ])
        self.mem.distiller = fake
        out = self.mem.reflect(A, [ChatMessage(role="user", content="coherence first")],
                               project_id=P, user_id=U, ref="chat-1")
        self.assertEqual([a["op"] for a in out["applied"]], ["create", "supersede", "reinforce"])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertEqual({m.id for m in fake.seen[1]}, {old.id, kept.id})
        new = self.mem.get("projects.chunking.strategy.coherence-first")
        self.assertEqual((new.source.type, new.source.ref), ("conversation", "chat-1"))
        self.assertEqual(self.mem.get(old.id).superseded_by, new.id)
        self.assertEqual(self.mem.get(kept.id).confidence, 0.6)
        ids = self.mem.context(project_id=P, user_id=U)["memoryIds"]
        self.assertIn("users.lorenz.preference.short-updates", ids)
        self.assertNotIn(old.id, ids)

    def test_reflect_needs_distiller(self):
        with self.assertRaises(MemoryServiceError) as cm:
            self.mem.reflect(A, [ChatMessage(role="user", content="hi")])
        self.assertEqual(cm.exception.code, "unavailable")

    def test_distiller_output_parses_camel_case(self):
        from memory.distill import OPS_ADAPTER, build_prompt

        ops = OPS_ADAPTER.validate_json('[{"op": "reinforce", "targetId": "a.b", "reason": "r"}]')
        self.assertEqual(ops[0].target_id, "a.b")
        m = self.remember("Goal", "evaluate chunking", "strategy")
        prompt = build_prompt([ChatMessage(role="user", content="hello")], [m])
        self.assertIn(m.id, prompt)
        self.assertIn("user: hello", prompt)


class TaskGraphSyncTest(MemoryTestCase):
    def test_sync_is_idempotent(self):
        tg = TaskGraphService()
        tg.submit(H, [CreateTask(id="d", title="Decision: which approach", kind="decision"),
                      RequestDecision(task_id="d", question="Recall or latency?"),
                      ResolveDecision(task_id="d", chosen="semantic", rationale="coherence first")])
        tg.update_settings(H, autonomy="ask-all")
        r = tg.submit(A, [CreateTask(title="Rewrite in Rust")], reason="faster chunking")
        tg.review(H, r.changeset_id, False, "out of scope for an evaluation project")

        created = self.mem.sync_from_taskgraph(tg, project_id=P)
        self.assertEqual([m.kind for m in created], ["decision", "learning"])
        self.assertIn("coherence first", created[0].body)
        self.assertEqual(created[0].created_by, H)
        self.assertIn("out of scope", created[1].body)
        self.assertEqual(self.mem.sync_from_taskgraph(tg, project_id=P), [])
        self.assertEqual(len(self.mem.list_memories()), 2)


class McpTest(MemoryTestCase):
    def test_tools_on_taskgraph_server(self):
        from taskgraph.mcp_server import build_server

        os.environ["MEMORY_PROJECT_ID"], os.environ["MEMORY_USER_ID"] = P, U
        self.addCleanup(os.environ.pop, "MEMORY_PROJECT_ID")
        self.addCleanup(os.environ.pop, "MEMORY_USER_ID")
        server = build_server(TaskGraphService(), memory=self.mem)

        async def go():
            names = {t.name for t in await server.list_tools()}
            self.assertLessEqual({"get_context", "recall", "remember", "memory_feedback",
                                  "list_memories", "apply_changes"}, names)
            await server.call_tool("remember", {"title": "Coherence first", "kind": "strategy",
                                                "body": "Coherence beats latency."})
            res = await server.call_tool("recall", {"query": "coherence"})
            self.assertIn("projects.chunking.strategy.coherence-first", res.content[0].text)
            res = await server.call_tool("get_context", {})
            self.assertIn("Coherence beats latency", res.content[0].text)

        asyncio.run(go())


class ApiTest(MemoryTestCase):
    def test_rest(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from memory.api import build_router

        app = FastAPI()
        app.include_router(build_router(self.mem, TaskGraphService()))
        c = TestClient(app)
        actor = {"type": "human", "id": "lorenz"}
        r = c.post("/api/memory/memories", json={
            "actor": actor, "title": "Coherence first", "body": "Coherence beats latency.",
            "kind": "strategy", "projectId": P})
        self.assertEqual(r.status_code, 200, r.text)
        mid = r.json()["id"]
        self.assertEqual(c.get(f"/api/memory/memories/{mid}").json()["projectId"], P)
        self.assertEqual(len(c.get("/api/memory/memories", params={"q": "coherence"}).json()), 1)
        self.assertEqual(c.get("/api/memory/context", params={"projectId": P}).json()["memoryIds"], [mid])
        self.assertEqual(c.get("/api/memory/tree").json()[0]["files"][0]["uses"], 1)
        r = c.post(f"/api/memory/memories/{mid}/feedback", json={"actor": actor, "helpful": False})
        self.assertEqual(r.json()["confidence"], 0.3)
        self.assertEqual(c.patch(f"/api/memory/memories/{mid}", json={"actor": actor, "body": "b"}).json()["body"], "b")
        self.assertEqual(c.get("/api/memory/memories/nope").status_code, 404)
        self.assertEqual(c.post("/api/memory/reflect", json={
            "actor": actor, "messages": [{"role": "user", "content": "hi"}]}).status_code, 503)
        self.assertEqual(c.post("/api/memory/sync").json(), [])
        self.assertEqual(c.delete(f"/api/memory/memories/{mid}").json()["status"], "archived")
        self.assertEqual(c.get("/api/memory/stats").json()["archived"], 1)


if __name__ == "__main__":
    unittest.main()
