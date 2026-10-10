"""MCP server exposing the task graph to the planner agent.

The tools come from the shared tool table in services/agent_tools.py
(`planner_tools`), the same functions the in-process Gemini agents use.

Run:  python -m taskgraph.mcp_server            (stdio, for Claude Code etc.)
Env:  TASKGRAPH_STORE, TASKGRAPH_PATH, DATABASE_PATH  storage, see storage.py
      TASKGRAPH_AGENT_ID  agent id recorded on changes (default: planner)
      TASKGRAPH_RUNNER, TASKGRAPH_RUNNER_CMD, TASKGRAPH_RUN_LOGS  see runs.py
      MEMORY_PATH, MEMORY_PROJECT_ID, MEMORY_USER_ID  see memory/mcp_server.py
      DATABASE_PATH, ISSUES_PATH  issue list (see issues.py)
      ARTIFACTS_PATH  task artifact files (see artifacts.py)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer

import issues as issues_mcp
from agent_identity import system_prompt
from issues import IssueService
from memory import mcp_server as memory_mcp
from memory.service import MemoryService

from .models import Actor
from .runs import RunManager, runner_from_env
from .service import TaskGraphService

if TYPE_CHECKING:
    from artifacts import ProjectArtifacts

    from .engine import Engine
    from .runlog import RunLog, Scratchpads

INSTRUCTIONS = """Task graph for an Orchestrators project. You are the planner.
Read with get_graph / get_task / ready_tasks. Change the graph with apply_changes
(a batch of ops, atomic) or the single-op tools. Every change needs a `reason`.
Depending on the project's autonomy setting your change applies immediately
(status 'applied') or waits for human review (status 'proposed').
Ask the human about strategy (goals, priorities, success criteria) with
request_decision, never about implementation A-vs-B choices you can decide.

Run control: start_task puts an agent to work on a ready task (it moves to
'running'), stop_task / restart_task stop or redo that work, start_ready_tasks
starts everything that is ready, list_active_runs shows which agents are working
on what, get_run_steps shows what an agent did. A run ends with finish_run
(succeeded -> task goes to 'in-review', failed/stopped -> back to 'open').
Check each run's `runner`: 'gemini' runs a Gemini agent in the server process;
'record' means nothing was launched and someone has to call finish_run.

Engine: engine_start keeps agents working on ready tasks (up to its concurrency
limit) in the API server; engine_stop stops dispatching (stop_runs also stops
active runs); engine_status shows state, active runs, the queue and `waiting`
(why nothing is starting: review, decisions, blocked tasks, autonomy).
Each task has a markdown scratchpad (read_scratchpad / write_scratchpad /
append_scratchpad) that its agents use as working memory across runs.
Workers leave their result as an HTML report on the task: list_artifacts shows a
task's files, read_artifact returns one's content (to review it or brief the
next task)."""


def build_server(
    service: TaskGraphService,
    agent_id: str = "planner",
    memory: MemoryService | None = None,
    runs: RunManager | None = None,
    issues: IssueService | None = None,
    *,
    runlog: RunLog | None = None,
    scratchpads: Scratchpads | None = None,
    engine: Engine | None = None,
    artifacts: ProjectArtifacts | None = None,
) -> MCPServer:
    """With `memory`, the planner also gets the memory tools on this server.
    `runs` executes agent runs; by default runs are only recorded. Without
    runlog / scratchpads / engine those tools answer 'not available'."""
    from services.agent_tools import ToolContext, planner_tools, register_mcp

    instructions = system_prompt("planner") + "\n\n## Tools\n\n" + INSTRUCTIONS
    if memory is not None:
        instructions += "\n\n" + memory_mcp.INSTRUCTIONS
    if issues is not None:
        instructions += "\n\n" + issues_mcp.MCP_INSTRUCTIONS
    server = MCPServer("orchestrators-taskgraph", instructions=instructions)
    if memory is not None:
        memory_mcp.register_tools(server, memory, agent_id)
    if issues is not None:
        issues_mcp.register_mcp_tools(server, issues, agent_id)
    ctx = ToolContext(
        taskgraph=service, actor=Actor(type="agent", id=agent_id), memory=memory,
        runs=runs if runs is not None else RunManager(service),
        runlog=runlog, scratchpads=scratchpads, engine=engine, artifacts=artifacts,
    )
    register_mcp(server, planner_tools(ctx))
    return server


def main() -> None:
    from storage import default_db, open_memory_store, open_taskgraph_store

    from .engine import Engine
    from .runlog import RunLog, Scratchpads

    db = default_db()
    service = TaskGraphService(open_taskgraph_store(db))
    memory = MemoryService(open_memory_store(db))
    runlog, scratchpads = RunLog(db), Scratchpads(db)
    issues = issues_mcp.service_from_env()
    from artifacts import ArtifactStore

    artifacts = ArtifactStore(db).scoped("default")

    def gemini():
        from services.gemini_worker import GeminiRunner

        return GeminiRunner(service, runlog, scratchpads, memory=memory, issues=issues,
                            artifacts=artifacts)

    runs = RunManager(service, runner_from_env(gemini))
    # Control only: the API server's engine loop does the dispatching.
    engine = Engine(service, runs, db)
    agent_id = os.environ.get("TASKGRAPH_AGENT_ID", "planner")
    build_server(service, agent_id, memory, runs, issues,
                 runlog=runlog, scratchpads=scratchpads, engine=engine,
                 artifacts=artifacts).run("stdio")


if __name__ == "__main__":
    main()
