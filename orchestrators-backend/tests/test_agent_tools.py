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
