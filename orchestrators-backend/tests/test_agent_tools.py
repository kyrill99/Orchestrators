"""Interview agent tools: in-process dispatch + Gemini tool loop (offline)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory.service import MemoryService  # noqa: E402
from memory.store import MarkdownMemoryStore  # noqa: E402
from services import agent as agent_mod  # noqa: E402
from services.agent_tools import TOOL_DECLARATIONS, AgentTools  # noqa: E402
from taskgraph import Actor, TaskGraphService  # noqa: E402
from taskgraph.models import CreateTask  # noqa: E402

H = Actor(type="human", id="lorenz")
P = "chunking"
U = "lorenz"


class _FakeInteractions:
    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("unexpected interactions.create call")
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted: list):
        self.interactions = _FakeInteractions(scripted)


class AgentToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mem = MemoryService(MarkdownMemoryStore(self.tmp.name))
        self.tg = TaskGraphService()
        self.tg.submit(H, [CreateTask(id="start", title="Start", kind="start")])
        self.tools = AgentTools(
            self.mem, self.tg, agent_id="interview", project_id=P, user_id=U
        )

    def test_declarations_cover_mcp_names(self):
        names = {t["name"] for t in TOOL_DECLARATIONS}
        self.assertLessEqual(
            {
                "get_context",
                "recall",
                "remember",
                "get_graph",
                "create_task",
                "apply_changes",
            },
            names,
        )

    def test_remember_and_recall(self):
        saved = self.tools.dispatch(
            "remember",
            {
                "title": "Coherence first",
                "body": "Coherence beats latency.",
                "kind": "strategy",
            },
        )
        self.assertIn("coherence", saved["id"])
        hits = self.tools.dispatch("recall", {"query": "coherence"})
        self.assertEqual(hits[0]["title"], "Coherence first")

    def test_create_task(self):
        result = self.tools.dispatch(
            "create_task",
            {"title": "Prototype", "reason": "capture plan", "id": "proto"},
        )
        self.assertEqual(result["status"], "applied")
        self.assertIn("proto", self.tg.state.tasks)

    def test_unknown_tool(self):
        self.assertIn("error", self.tools.dispatch("nope", {}))


class DefaultAssigneeTest(unittest.TestCase):
    """Tasks agents create go to "Agent" (the GUI picker's agent) unless told otherwise."""

    AGENT = {"type": "agent", "id": "agent"}

    def setUp(self):
        self.tg = TaskGraphService()
        self.tools = AgentTools(None, self.tg, agent_id="planner", project_id=P, user_id=U)

    def assignee(self, task_id):
        return self.tg.task(task_id)["assignee"]

    def test_create_task_defaults_to_agent(self):
        for kind in ("start", "task", "end"):
            self.tools.call("create_task", {"id": kind, "title": kind, "kind": kind, "reason": "plan"})
            self.assertEqual(self.assignee(kind), self.AGENT, kind)

    def test_explicit_assignee_wins(self):
        self.tools.call("create_task", {"id": "h", "title": "H", "reason": "plan",
                                        "assignee_type": "human", "assignee_id": "lorenz"})
        self.assertEqual(self.assignee("h"), {"type": "human", "id": "lorenz"})
        self.tools.call("apply_changes", {"reason": "plan", "ops": [
            {"op": "task.create", "id": "x", "title": "X", "assignee": {"type": "agent", "id": "codex"}},
            {"op": "task.create", "id": "y", "title": "Y", "assignee": None},
        ]})
        self.assertEqual(self.assignee("x"), {"type": "agent", "id": "codex"})
        self.assertIsNone(self.assignee("y"))  # explicitly unassigned

    def test_decision_tasks_stay_unassigned(self):
        self.tools.call("create_task", {"id": "d", "title": "Pick", "kind": "decision", "reason": "plan"})
        self.tools.call("apply_changes", {"reason": "plan", "ops": [
            {"op": "task.create", "id": "d2", "title": "Pick 2", "kind": "decision"},
            {"op": "task.create", "id": "w", "title": "Work", "blockedBy": ["d2"]},
        ]})
        self.assertIsNone(self.assignee("d"))
        self.assertIsNone(self.assignee("d2"))
        self.assertEqual(self.assignee("w"), self.AGENT)

    def test_humans_in_the_gui_are_not_forced(self):
        self.tg.submit(H, [CreateTask(id="g", title="Mine")], "gui")
        self.assertIsNone(self.assignee("g"))

    def test_worker_subtasks(self):
        from services.agent_tools import ToolContext, by_name, worker_tools

        self.tg.submit(H, [CreateTask(id="t", title="T")])
        ctx = ToolContext(taskgraph=self.tg, actor=Actor(type="agent", id="gemini"))
        by_name(worker_tools(ctx, run_id="r", task_id="t"))["create_subtask"].call(
            {"id": "sub", "title": "Sub", "reason": "follow-up"})
        self.assertEqual(self.assignee("sub"), self.AGENT)
        self.assertEqual(self.tg.task("sub")["parentId"], "t")

    def test_a_run_keeps_the_agent_assignee(self):
        from taskgraph import RunManager

        self.tools.call("create_task", {"id": "a", "title": "A", "reason": "plan"})
        self.tg.submit(H, [CreateTask(id="b", title="B"),
                           CreateTask(id="c", title="C", assignee=Actor(type="human", id="kyrill"))])
        runs = RunManager(self.tg)
        for task_id in ("a", "b", "c"):
            runs.start(H, task_id, "go", agent_id="gemini")
        self.assertEqual(self.assignee("a"), self.AGENT)  # stays "Agent"
        self.assertEqual(self.assignee("b"), {"type": "agent", "id": "gemini"})  # was unassigned
        self.assertEqual(self.assignee("c"), {"type": "agent", "id": "gemini"})  # a human handed it over
        self.assertEqual(self.tg.runs(task_id="a")[0].agent.id, "gemini")


class AgentChatToolLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mem = MemoryService(MarkdownMemoryStore(self.tmp.name))
        self.tg = TaskGraphService()
        self.tools = AgentTools(
            self.mem, self.tg, agent_id="interview", project_id=P, user_id=U
        )
        agent_mod.configure_tools(self.tools)
        self.addCleanup(agent_mod.configure_tools, None)
        os.environ["GEMINI_API_KEY"] = "test-key"
        self.addCleanup(os.environ.pop, "GEMINI_API_KEY", None)

    def test_tool_call_then_text(self):
        self.tools.dispatch(
            "remember",
            {
                "title": "Goal",
                "body": "Ship a reliable chunker.",
                "kind": "strategy",
            },
        )
        first = SimpleNamespace(
            id="i1",
            output_text="",
            outputs=[],
            steps=[
                SimpleNamespace(
                    type="function_call",
                    name="recall",
                    id="c1",
                    arguments={"query": "chunker"},
                )
            ],
        )
        second = SimpleNamespace(
            id="i2",
            output_text="You want a reliable chunker. What does success look like?",
            outputs=[],
            steps=[],
        )
        fake = _FakeClient([first, second])
        original = agent_mod._client
        agent_mod._client = lambda: fake  # type: ignore[assignment]
        self.addCleanup(setattr, agent_mod, "_client", original)

        out = agent_mod.chat("We need a better chunker.")
        self.assertEqual(out["interaction_id"], "i2")
        self.assertIn("reliable chunker", out["reply"])
        self.assertEqual(len(fake.interactions.calls), 2)
        follow = fake.interactions.calls[1]
        self.assertEqual(follow["previous_interaction_id"], "i1")
        self.assertEqual(follow["input"][0]["type"], "function_result")
        self.assertEqual(follow["input"][0]["name"], "recall")
        self.assertIn("tools", follow)

    def _reply(self, text="Noted."):
        return SimpleNamespace(id="i1", output_text=text, outputs=[], steps=[])

    def _chat(self, fake, *args, **kwargs):
        original = agent_mod._client
        agent_mod._client = lambda: fake  # type: ignore[assignment]
        self.addCleanup(setattr, agent_mod, "_client", original)
        return agent_mod.chat(*args, **kwargs)

    def test_new_conversation_starts_with_memory(self):
        self.tools.dispatch("remember", {"title": "Goal", "body": "Ship a reliable chunker.",
                                         "kind": "strategy"})
        fake = _FakeClient([self._reply(), self._reply()])
        self._chat(fake, "Hello again.")
        first = fake.interactions.calls[0]["input"]
        self.assertIn("Ship a reliable chunker.", first)
        self.assertTrue(first.endswith("Hello again."))
        self.assertEqual(self.mem.get("projects.chunking.strategy.goal").uses, 1)
        # Later turns carry only the owner's message; the chain already has memory.
        self._chat(fake, "And more.", previous_interaction_id="i1")
        self.assertEqual(fake.interactions.calls[1]["input"], "And more.")

    def test_memory_is_per_signed_in_user(self):
        os.environ.pop("MEMORY_USER_ID", None)
        anna = self.tools.for_user("Anna@Example.com")
        anna.dispatch("remember", {"title": "Short updates", "body": "Three lines at most.",
                                   "kind": "preference", "scope": "user"})
        self.assertIsNotNone(self.mem.get("users.anna-example-com.preference.short-updates"))
        self.assertIn("Three lines", anna.memory_context())
        self.assertNotIn("Three lines", self.tools.for_user("bob@example.com").memory_context())

        fake = _FakeClient([self._reply()])
        self._chat(fake, "Hi.", user="anna@example.com")
        self.assertIn("Three lines", fake.interactions.calls[0]["input"])


if __name__ == "__main__":
    unittest.main()
