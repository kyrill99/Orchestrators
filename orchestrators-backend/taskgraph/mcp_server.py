"""MCP server exposing the task graph to the planner agent.

Run:  python -m taskgraph.mcp_server            (stdio, for Claude Code etc.)
Env:  TASKGRAPH_PATH      event log (default: data/taskgraph.jsonl)
      TASKGRAPH_AGENT_ID  agent id recorded on changes (default: planner)
"""

from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .models import (
    Actor,
    AddComment,
    AddRelation,
    AssignTask,
    CreateTask,
    DeleteTask,
    Op,
    RemoveRelation,
    RequestDecision,
    ResolveDecision,
    SetStatus,
    TaskStatus,
    UpdateTask,
)
from .service import TaskGraphError, TaskGraphService
from .store import JsonlEventStore

INSTRUCTIONS = """Task graph for an Orchestrators project. You are the planner.
Read with get_graph / get_task / ready_tasks. Change the graph with apply_changes
(a batch of ops, atomic) or the single-op tools. Every change needs a `reason`.
Depending on the project's autonomy setting your change applies immediately
(status 'applied') or waits for human review (status 'proposed').
Ask the human about strategy (goals, priorities, success criteria) with
request_decision, never about implementation A-vs-B choices you can decide."""


def build_server(service: TaskGraphService, agent_id: str = "planner") -> MCPServer:
    server = MCPServer("orchestrators-taskgraph", instructions=INSTRUCTIONS)
    me = Actor(type="agent", id=agent_id)

    def run(fn, *args, **kwargs) -> Any:
        try:
            out = fn(*args, **kwargs)
        except TaskGraphError as e:
            raise ToolError(f"{e.code}: {e}") from None
        return out.dump() if hasattr(out, "dump") else out

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

    return server


def main() -> None:
    path = os.environ.get("TASKGRAPH_PATH", "data/taskgraph.jsonl")
    service = TaskGraphService(JsonlEventStore(path))
    build_server(service, os.environ.get("TASKGRAPH_AGENT_ID", "planner")).run("stdio")


if __name__ == "__main__":
    main()
