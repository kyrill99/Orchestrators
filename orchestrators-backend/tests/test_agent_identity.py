"""Every agent starts from the shared identity and its own role card."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_identity import system_prompt  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import MarkdownMemoryStore  # noqa: E402
from services import agent as agent_mod  # noqa: E402
from services.agent_tools import AgentTools  # noqa: E402
from taskgraph import Actor, TaskGraphService  # noqa: E402
from taskgraph.mcp_server import INSTRUCTIONS, build_server  # noqa: E402
from taskgraph.models import AgentRun, CreateTask, Task  # noqa: E402
from taskgraph.runs import render_prompt  # noqa: E402

IDENTITY = "# Orchestrators"
OWNER = Actor(type="human", id="lorenz")


class SystemPromptTest(unittest.TestCase):
    def test_every_role_gets_the_identity_then_its_card(self):
        for role, card in [
            ("strategy_partner", "# Role: strategy partner"),
            ("planner", "# Role: planner"),
            ("worker", "# Role: worker"),
        ]:
            prompt = system_prompt(role)
            self.assertTrue(prompt.startswith(IDENTITY), role)
            self.assertIn(card, prompt)
            self.assertLess(prompt.index("## Honesty"), prompt.index(card))


class WiringTest(unittest.TestCase):
    def test_chat_agent(self):
        for mode in ("text", "voice"):
            text = agent_mod.instructions(mode)
            self.assertTrue(text.startswith(IDENTITY))
            self.assertIn("# Role: strategy partner", text)
            self.assertIn(agent_mod.STYLE[mode], text)

    def test_planner_mcp_server(self):
        instructions = build_server(TaskGraphService()).instructions
        self.assertTrue(instructions.startswith(IDENTITY))
        self.assertIn("# Role: planner", instructions)
        self.assertIn(INSTRUCTIONS, instructions)

    def test_worker_brief_keeps_the_task_on_top(self):
        task = Task(id="t", title="Write the report")
        run = AgentRun(
            id="r1", task_id="t", agent=Actor(type="agent", id="worker"),
            runner="record", started_by=OWNER, started_at="2026-10-10T15:00:00Z",
        )
        brief = render_prompt(run, task)
        self.assertTrue(brief.startswith("Task t: Write the report"))
        self.assertIn("# Role: worker", brief)
        self.assertIn(IDENTITY, brief)


class OpeningContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = MemoryService(MarkdownMemoryStore(self.tmp.name))
        self.graph = TaskGraphService()
        agent_mod.configure_tools(
            AgentTools(self.memory, self.graph, project_id="p", user_id="u")
        )
        self.addCleanup(agent_mod.configure_tools, None)

    def test_empty_project(self):
        self.assertIn("No tasks yet.", agent_mod._opening_context())

    def test_known_strategy_and_board(self):
        self.memory.remember(
            OWNER, title="Goal", body="Evaluate our chunking algorithm.",
            kind="strategy", scope="project", project_id="p", user_id="u",
        )
        self.graph.submit(OWNER, [CreateTask(id="start", title="Start", kind="start")])
        context = agent_mod._opening_context()
        self.assertIn("Evaluate our chunking algorithm.", context)
        self.assertIn("1 tasks: 1 open.", context)

    def test_without_tools(self):
        agent_mod.configure_tools(None)
        self.assertEqual(agent_mod._opening_context(), "")


if __name__ == "__main__":
    unittest.main()
