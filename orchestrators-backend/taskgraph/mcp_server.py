"""MCP server exposing the task graph to the planner agent.

Run:  python -m taskgraph.mcp_server            (stdio, for Claude Code etc.)
Env:  TASKGRAPH_PATH      event log (default: data/taskgraph.jsonl)
      TASKGRAPH_AGENT_ID  agent id recorded on changes (default: planner)
      TASKGRAPH_RUNNER, TASKGRAPH_RUNNER_CMD, TASKGRAPH_RUN_LOGS  see runs.py
      MEMORY_PATH, MEMORY_PROJECT_ID, MEMORY_USER_ID  see memory/mcp_server.py
"""

from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from memory import mcp_server as memory_mcp
from memory.service import MemoryService

from .models import (
    Actor,
    ChangesetStatus,
    AddComment,
    AddRelation,
    AssignTask,
    CreateTask,
    DeleteTask,
    Op,
    RemoveRelation,
    RequestDecision,
    ResolveDecision,
    RunStatus,
    SetStatus,
    TaskStatus,
    UpdateTask,
)
from .runs import RunManager, runner_from_env
from .service import TaskGraphError, TaskGraphService
from .store import JsonlEventStore

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
on what. A run ends with finish_run (succeeded -> task goes to 'in-review',
failed/stopped -> back to 'open'). Check each run's `runner`: 'record' means no
process was launched for it; someone has to do the work and call finish_run."""


def build_server(
    service: TaskGraphService,
    agent_id: str = "planner",
    memory: MemoryService | None = None,
    runs: RunManager | None = None,
) -> MCPServer:
    """With `memory`, the planner also gets the memory tools on this server.
    `runs` executes agent runs; by default runs are only recorded."""
    runs = runs if runs is not None else RunManager(service)
    instructions = INSTRUCTIONS
    if memory is not None:
        instructions += "\n\n" + memory_mcp.INSTRUCTIONS
    server = MCPServer("orchestrators-taskgraph", instructions=instructions)
    if memory is not None:
        memory_mcp.register_tools(server, memory, agent_id)
    me = Actor(type="agent", id=agent_id)

    def run(fn, *args, **kwargs) -> Any:
        try:
            out = fn(*args, **kwargs)
        except TaskGraphError as e:
            raise ToolError(f"{e.code}: {e}") from None
        if isinstance(out, list):
            return [o.dump() if hasattr(o, "dump") else o for o in out]
        return out.dump() if hasattr(out, "dump") else out

    def active_run_id(task_id: str) -> str:
        active = run(service.runs, task_id=task_id, active=True)
        if not active:
            raise ToolError(f"not_found: '{task_id}' has no active run")
        return active[0]["id"]

    def submit(ops: list[Op], reason: str) -> dict:
        return run(service.submit, me, ops, reason)

    # ---- reads ----
    @server.tool()
    def get_graph() -> dict:
        """Whole project: settings, tasks, edges, pending decisions/changesets."""
        return run(service.graph)

    @server.tool()
    def get_task(task_id: str) -> dict:
        """One task with its activity feed, dependents and children."""
        return run(service.task, task_id)

    @server.tool()
    def ready_tasks() -> list[dict]:
        """Open tasks whose blockers are all closed (can be started now)."""
        return run(service.ready)

    @server.tool()
    def blocked_tasks() -> list[dict]:
        """Unfinished tasks and what they are waiting on."""
        return run(service.blocked)

    @server.tool()
    def list_decisions(state: Literal["pending", "resolved"] | None = None) -> list[dict]:
        """Decision log: questions, choices and rationale."""
        return run(service.decisions, state)

    @server.tool()
    def list_activity(task_id: str | None = None, since: int = 0, limit: int = 50) -> list[dict]:
        """Activity feed, optionally for one task, after event seq `since`."""
        return run(service.activity, task_id, since=since, limit=limit)

    @server.tool()
    def list_changesets(status: ChangesetStatus | None = None) -> list[dict]:
        """Changesets (batches of changes): applied, proposed (awaiting human review) or rejected."""
        return run(service.changesets, status)

    @server.tool()
    def get_events(since: int = 0, limit: int = 100) -> list[dict]:
        """Raw event log after seq `since` (every change, run start/stop, review)."""
        return run(service.events_since, since, limit)

    # ---- writes ----
    @server.tool()
    def apply_changes(ops: list[Op], reason: str) -> dict:
        """Apply a batch of ops atomically (all or nothing). Later ops may
        reference tasks created earlier in the batch by their `id`."""
        return submit(ops, reason)

    @server.tool()
    def create_task(
        title: str,
        reason: str,
        id: str | None = None,
        kind: Literal["start", "task", "decision", "end"] = "task",
        description: str = "",
        blocked_by: list[str] | None = None,
        parent_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Create a task. blocked_by lists tasks that must close first."""
        op = CreateTask(
            id=id, title=title, kind=kind, description=description,
            blocked_by=blocked_by or [], parent_id=parent_id,
            start_date=start_date, end_date=end_date,
        )
        return submit([op], reason)

    @server.tool()
    def update_task(task_id: str, fields: dict, reason: str) -> dict:
        """Update fields: title, kind, description, parentId, startDate, endDate, progress, artifacts."""
        try:
            op = UpdateTask(task_id=task_id, **fields)
        except Exception as e:  # pydantic validation
            raise ToolError(f"invalid: {e}") from None
        return submit([op], reason)

    @server.tool()
    def set_status(task_id: str, status: TaskStatus, reason: str) -> dict:
        """Move a task: open | running | in-review | closed (blockers must be closed)."""
        return submit([SetStatus(task_id=task_id, status=status)], reason)

    @server.tool()
    def assign_task(task_id: str, reason: str, assignee_type: Literal["human", "agent"] | None = None, assignee_id: str | None = None) -> dict:
        """Assign to a human or agent; omit assignee to unassign."""
        who = Actor(type=assignee_type, id=assignee_id) if assignee_type and assignee_id else None
        return submit([AssignTask(task_id=task_id, assignee=who)], reason)

    @server.tool()
    def delete_task(task_id: str, reason: str) -> dict:
        """Delete a task and its relations."""
        return submit([DeleteTask(task_id=task_id)], reason)

    @server.tool()
    def add_relation(source: str, target: str, reason: str, type: Literal["blocks", "related"] = "blocks") -> dict:
        """'blocks': source must close before target can start. Cycles are rejected."""
        return submit([AddRelation(source=source, target=target, type=type)], reason)

    @server.tool()
    def remove_relation(source: str, target: str, reason: str, type: Literal["blocks", "related"] = "blocks") -> dict:
        """Remove a relation."""
        return submit([RemoveRelation(source=source, target=target, type=type)], reason)

    @server.tool()
    def add_comment(task_id: str, body: str, reason: str = "comment") -> dict:
        """Post to a task's activity feed (progress notes, findings)."""
        return submit([AddComment(task_id=task_id, body=body)], reason)

    @server.tool()
    def request_decision(task_id: str, question: str, reason: str, options: list[str] | None = None, context: str | None = None) -> dict:
        """Ask the human a STRATEGY question on a (decision) task."""
        return submit([RequestDecision(task_id=task_id, question=question, options=options or [], context=context)], reason)

    @server.tool()
    def resolve_decision(task_id: str, chosen: str, rationale: str) -> dict:
        """Record a decision you made yourself, with the rationale."""
        return submit([ResolveDecision(task_id=task_id, chosen=chosen, rationale=rationale)], rationale)

    # ---- run control ----
    @server.tool()
    def start_task(
        task_id: str, reason: str, agent_id: str = "worker", instructions: str | None = None
    ) -> dict:
        """Put agent `agent_id` to work on a task. The task must be ready (not
        closed, blockers closed, no pending decision, no active run); it moves
        to 'running' and is assigned to the agent. Returns the run."""
        return run(runs.start, me, task_id, reason, agent_id=agent_id, instructions=instructions)

    @server.tool()
    def stop_task(task_id: str, reason: str) -> dict:
        """Stop the agent working on a task. The task goes back to 'open'."""
        return run(runs.stop, me, active_run_id(task_id), reason)

    @server.tool()
    def restart_task(
        task_id: str, reason: str, agent_id: str | None = None, instructions: str | None = None
    ) -> dict:
        """Stop the task's active run (if any) and start a new attempt. Agent and
        instructions carry over from the last run unless you pass new ones."""
        return run(runs.restart, me, task_id, reason, agent_id=agent_id, instructions=instructions)

    @server.tool()
    def start_ready_tasks(reason: str, agent_id: str = "worker", limit: int | None = None) -> list[dict]:
        """Start runs on every ready work task (kind 'task'), up to `limit`."""
        ready = [t["id"] for t in run(service.ready) if t["kind"] == "task"]
        started = []
        for task_id in ready[:limit] if limit is not None else ready:
            if not service.runs(task_id=task_id, active=True):
                started.append(run(runs.start, me, task_id, reason, agent_id=agent_id))
        return started

    @server.tool()
    def stop_all_runs(reason: str) -> list[dict]:
        """Stop every active run (the whole graph run)."""
        return [run(runs.stop, me, r.id, reason) for r in runs.active()]

    @server.tool()
    def finish_run(run_id: str, outcome: Literal["succeeded", "failed"], note: str) -> dict:
        """Report how a run ended. succeeded -> task 'in-review'; failed -> 'open'."""
        return run(runs.finish, me, run_id, outcome, note)

    @server.tool()
    def list_active_runs() -> list[dict]:
        """Agents working right now: run id, task, agent, runner, since when."""
        return run(runs.active)

    @server.tool()
    def list_runs(task_id: str | None = None, status: RunStatus | None = None) -> list[dict]:
        """Run history, optionally for one task or one status."""
        return run(service.runs, task_id=task_id, status=status)

    @server.tool()
    def get_run(run_id: str) -> dict:
        """One run: agent, status, attempt, exit code, log path."""
        return run(service.run, run_id)

    return server


def main() -> None:
    path = os.environ.get("TASKGRAPH_PATH", "data/taskgraph.jsonl")
    service = TaskGraphService(JsonlEventStore(path))
    agent_id = os.environ.get("TASKGRAPH_AGENT_ID", "planner")
    runs = RunManager(service, runner_from_env())
    build_server(service, agent_id, memory_mcp.service_from_env(), runs).run("stdio")


if __name__ == "__main__":
    main()
