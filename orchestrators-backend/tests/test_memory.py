import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import Database  # noqa: E402
from memory import ChatMessage, MarkdownMemoryStore, MemoryService, MemoryServiceError, ReflectOp  # noqa: E402
from memory.sqlite_store import SqliteMemoryStore  # noqa: E402
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
        self.mem = MemoryService(self.open_store())

    def open_store(self):
        """A store on this test's location; called again for a 'second process'."""
        return MarkdownMemoryStore(self.dir.name)

    def remember(self, title, body="body", kind="learning", scope="project", **kw):
        return self.mem.remember(H, title=title, body=body, kind=kind, scope=scope,
                                 project_id=P, user_id=U, **kw)


class CoreTest(MemoryTestCase):
    def test_file_roundtrip(self):
        m = self.remember("Success criteria", "Coherence first: then latency.\n---\n- a", "strategy",
                          tags=["eval"])
        self.assertEqual(m.id, "projects.chunking.strategy.success-criteria")
        if isinstance(self.mem.store, MarkdownMemoryStore):
            self.assertTrue(os.path.exists(os.path.join(self.dir.name, "projects", "chunking",
                                                        "strategy", "success-criteria.md")))
        again = MemoryService(self.open_store()).get(m.id)
        self.assertEqual(again, m)

    def test_same_title_updates(self):
        first = self.remember("No A-vs-B questions", "old", "preference", "user", tags=["a"])
        second = self.remember("No A-vs-B questions", "new", "preference", "user", tags=["b"])
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.mem.list_memories()), 1)
        self.assertEqual((second.body, second.tags), ("new", ["a", "b"]))

    def test_long_titles_do_not_overwrite_each_other(self):
        prefix = "Owner preference about how agents should report on "
        a = self.remember(prefix + "progress", "short updates", "preference", "user")
        b = self.remember(prefix + "blockers", "tell me at once", "preference", "user")
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(self.mem.get(a.id).body, "short updates")
        again = self.remember(prefix + "progress", "three lines", "preference", "user")
        self.assertEqual(again.id, a.id)
        self.assertEqual(len(self.mem.list_memories()), 2)

    def test_same_title_finds_memory_with_old_short_id(self):
        from memory.models import Memory

        title = "Success criteria and trade-offs: coherence first, then latency"
        old = Memory(id="projects.chunking.strategy.success-criteria-and-trade-offs-coherenc",
                     scope="project", project_id=P, kind="strategy", title=title, body="old")
        self.mem.store.write(old)
        m = self.remember(title, "new", "strategy")
        self.assertEqual(m.id, old.id)
        self.assertEqual(len(self.mem.list_memories()), 1)

    def test_overwritten_text_is_kept_in_log(self):
        self.remember("Priorities", "coherence first", "strategy")
        self.remember("Priorities", "latency first", "strategy")
        last = self.mem.log()[-1]
        self.assertEqual((last["event"], last["previousBody"]), ("updated", "coherence first"))

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
        (folder,) = self.mem.tree(P)
        self.assertEqual(folder["name"], "projects/chunking/strategy")
        self.assertEqual(folder["label"], "This project · strategy")
        f = folder["files"][0]
        self.assertEqual((f["name"], f["path"]), ("success-criteria.md",
                                                  "projects/chunking/strategy/success-criteria.md"))
        self.assertTrue(f["content"].startswith("# Success criteria"))

    def test_two_services_share_a_directory(self):
        other = MemoryService(self.open_store())
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


class ProjectScopeTest(MemoryTestCase):
    """Project memories follow the active project; user and global ones are shared."""

    def setUp(self):
        super().setUp()
        self.active = "alpha"
        self.mem.current_project = lambda: self.active

    def put(self, title, kind="strategy", scope="project", user_id=U):
        return self.mem.remember(H, title=title, body=f"{title} body", kind=kind, scope=scope,
                                 user_id=user_id)

    def test_project_memories_stay_in_their_project(self):
        goal = self.put("Goal")
        pref = self.put("Short updates", "preference", "user")
        lesson = self.put("Ask for the metric first", "learning", "global")
        self.assertEqual(goal.id, "projects.alpha.strategy.goal")

        self.active = "beta"
        self.assertEqual(self.mem.context(user_id=U)["memoryIds"], [pref.id, lesson.id])
        self.assertEqual(self.mem.search("goal", user_id=U), [])
        other = self.put("Goal")
        self.assertEqual(other.id, "projects.beta.strategy.goal")
        self.assertEqual({m.id for m in self.mem.visible(user_id=U)}, {other.id, pref.id, lesson.id})

        self.active = "alpha"
        self.assertEqual(self.mem.get(goal.id).body, "Goal body")
        self.assertEqual(self.mem.context(user_id=U)["memoryIds"], [goal.id, pref.id, lesson.id])
        # An explicit project still wins over the active one.
        self.assertIn(other.id, self.mem.context(project_id="beta", user_id=U)["memoryIds"])

    def test_tree_shows_this_project_personal_and_shared(self):
        self.put("Goal")
        self.put("Short updates", "preference", "user")
        self.put("Long updates", "preference", "user", user_id="someone-else")
        self.put("Ask for the metric first", "learning", "global")
        self.active = "beta"
        self.put("Other goal")
        tree = self.mem.tree(user_id=U)
        self.assertEqual([f["label"] for f in tree],
                         ["This project · strategy", "Personal · preference", "All projects · learning"])
        self.assertEqual([[x["title"] for x in f["files"]] for f in tree],
                         [["Other goal"], ["Short updates"], ["Ask for the metric first"]])

    def test_sync_keeps_same_task_id_apart(self):
        graphs = {}
        for pid, chosen in (("alpha", "semantic"), ("beta", "fixed windows")):
            tg = graphs[pid] = TaskGraphService()
            tg.submit(H, [CreateTask(id="d", title="Decision: which approach", kind="decision"),
                          RequestDecision(task_id="d", question="Which?"),
                          ResolveDecision(task_id="d", chosen=chosen, rationale="fits")])
        for pid, tg in graphs.items():
            self.active = pid
            self.assertEqual(len(self.mem.sync_from_taskgraph(tg)), 1)
            self.assertEqual(self.mem.sync_from_taskgraph(tg), [])
        self.assertIn("semantic", self.mem.get("projects.alpha.decision.d").body)
        self.assertIn("fixed windows", self.mem.get("projects.beta.decision.d").body)

    def test_agent_tools_follow_the_active_project(self):
        from services.agent_tools import AgentTools

        os.environ.pop("MEMORY_PROJECT_ID", None)
        tools = AgentTools(self.mem, TaskGraphService(), user_id=U)
        saved = tools.dispatch("remember", {"title": "Goal", "body": "alpha goal", "kind": "strategy"})
        self.assertEqual(saved["id"], "projects.alpha.strategy.goal")
        tools.dispatch("remember", {"title": "Lesson", "body": "shared", "kind": "learning",
                                    "scope": "global"})
        self.active = "beta"
        self.assertNotIn("alpha goal", tools.memory_context())
        self.assertIn("shared", tools.memory_context())
        self.assertEqual([m["id"] for m in tools.dispatch("list_memories", {})],
                         ["global.learning.lesson"])
        # A worker is bound to its project whatever request is active.
        worker = AgentTools(self.mem, TaskGraphService(), project_id="alpha", user_id=U)
        self.assertIn("alpha goal", worker.memory_context())


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
        self.assertEqual(c.get("/api/memory/tree").json(), [])  # another project is active
        tree = c.get("/api/memory/tree", params={"projectId": P}).json()
        self.assertEqual(tree[0]["files"][0]["uses"], 1)
        r = c.post(f"/api/memory/memories/{mid}/feedback", json={"actor": actor, "helpful": False})
        self.assertEqual(r.json()["confidence"], 0.3)
        self.assertEqual(c.patch(f"/api/memory/memories/{mid}", json={"actor": actor, "body": "b"}).json()["body"], "b")
        self.assertEqual(c.get("/api/memory/memories/nope").status_code, 404)
        self.assertEqual(c.post("/api/memory/reflect", json={
            "actor": actor, "messages": [{"role": "user", "content": "hi"}]}).status_code, 503)
        self.assertEqual(c.post("/api/memory/sync").json(), [])
        self.assertEqual(c.delete(f"/api/memory/memories/{mid}").json()["status"], "archived")
        self.assertEqual(c.get("/api/memory/stats").json()["archived"], 1)


class Sqlite:
    """Mixin: rerun a memory test case against SqliteMemoryStore."""

    def open_store(self):
        db = Database(os.path.join(self.dir.name, "memory.db"))  # one per "process"
        self.addCleanup(db.close)
        return SqliteMemoryStore(db)


class SqliteCoreTest(Sqlite, CoreTest):
    def test_order_and_log_match_markdown(self):
        md = MemoryService(MarkdownMemoryStore(os.path.join(self.dir.name, "md")))
        md.clock = self.mem.clock = lambda: "2026-01-01T00:00:00Z"
        for svc in (self.mem, md):
            for title in ["foo bar", "foo", "a b", "a", "Z", "x y z"]:
                svc.remember(H, title=title, body="  padded body \n", kind="note",
                             scope="project", project_id=P, user_id=U)
            svc.feedback(H, svc.list_memories()[0].id, True)
        self.assertEqual(list(self.mem.store.load()), list(md.store.load()))
        self.assertEqual([m.dump() for m in self.mem.list_memories()],
                         [m.dump() for m in md.list_memories()])
        for limit in (0, 1, 3, 100):
            self.assertEqual([e["event"] for e in self.mem.log(limit)],
                             [e["event"] for e in md.log(limit)])


class SqliteReflectTest(Sqlite, ReflectTest):
    pass


class SqliteTaskGraphSyncTest(Sqlite, TaskGraphSyncTest):
    pass


class SqliteProjectScopeTest(Sqlite, ProjectScopeTest):
    pass


class SqliteMcpTest(Sqlite, McpTest):
    pass


class SqliteApiTest(Sqlite, ApiTest):
    pass


if __name__ == "__main__":
    unittest.main()
