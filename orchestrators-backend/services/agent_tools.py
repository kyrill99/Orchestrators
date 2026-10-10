"""In-process memory + taskgraph tools for the interview agent.

Same vocabulary as the MCP servers (`memory.mcp_server`, `taskgraph.mcp_server`),
but called directly from FastAPI against the shared service instances — no stdio.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import TypeAdapter, ValidationError

from memory.models import MemorySource
from memory.service import DEFAULT_ID, MemoryService, MemoryServiceError
from taskgraph.models import (
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
    UpdateTask,
)
from taskgraph.service import TaskGraphError, TaskGraphService

OpsAdapter = TypeAdapter(list[Op])

# Gemini Interactions function declarations (mirror MCP tool names).
TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_context",
        "description": (
            "Everything memory knows that applies now: project strategy, owner "
            "preferences, orchestrator lessons, plus entries matching query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional focus query"},
            },
        },
    },
    {
        "type": "function",
        "name": "recall",
        "description": "Search memory. Use before asking the owner a question.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["strategy", "preference", "decision", "learning", "note"],
                },
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "remember",
        "description": (
            "Store something worth knowing next session. scope 'project' for this "
            "project, 'user' for an owner preference across projects, 'global' for "
            "a lesson about orchestrating. Same title updates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["strategy", "preference", "decision", "learning", "note"],
                },
                "scope": {"type": "string", "enum": ["project", "user", "global"]},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "body", "kind"],
        },
    },
    {
        "type": "function",
        "name": "memory_feedback",
        "description": (
            "Report whether a memory held up. Not helpful lowers confidence; "
            "repeated, it is archived."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "helpful": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["memory_id", "helpful"],
        },
    },
    {
        "type": "function",
        "name": "list_memories",
        "description": "List active memories, optionally by scope or kind.",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["project", "user", "global"]},
                "kind": {
                    "type": "string",
                    "enum": ["strategy", "preference", "decision", "learning", "note"],
                },
            },
        },
    },
    {
        "type": "function",
        "name": "get_graph",
        "description": "Whole project: settings, tasks, edges, pending decisions/changesets.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_task",
        "description": "One task with its activity feed, dependents and children.",
        "parameters": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "type": "function",
        "name": "ready_tasks",
        "description": "Open tasks whose blockers are all closed (can be started now).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "blocked_tasks",
        "description": "Unfinished tasks and what they are waiting on.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "list_decisions",
        "description": "Decision log: questions, choices and rationale.",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["pending", "resolved"]},
            },
        },
    },
    {
        "type": "function",
        "name": "list_activity",
        "description": "Activity feed, optionally for one task, after event seq since.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "since": {"type": "integer"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "type": "function",
        "name": "apply_changes",
        "description": (
            "Apply a batch of ops atomically (all or nothing). Later ops may "
            "reference tasks created earlier in the batch by their id. Every change "
            "needs a reason."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ops": {"type": "array", "items": {"type": "object"}},
                "reason": {"type": "string"},
            },
            "required": ["ops", "reason"],
        },
    },
    {
        "type": "function",
        "name": "create_task",
        "description": "Create a task. blocked_by lists tasks that must close first.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "reason": {"type": "string"},
                "id": {"type": "string"},
                "kind": {"type": "string", "enum": ["start", "task", "decision", "end"]},
                "description": {"type": "string"},
                "blocked_by": {"type": "array", "items": {"type": "string"}},
                "parent_id": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["title", "reason"],
        },
    },
    {
        "type": "function",
        "name": "update_task",
        "description": (
            "Update fields: title, kind, description, parentId, startDate, endDate, "
            "progress, artifacts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "fields": {"type": "object"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "fields", "reason"],
        },
    },
    {
        "type": "function",
        "name": "set_status",
        "description": "Move a task: open | running | in-review | closed.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["open", "running", "in-review", "closed"],
                },
                "reason": {"type": "string"},
            },
            "required": ["task_id", "status", "reason"],
        },
    },
    {
        "type": "function",
        "name": "assign_task",
        "description": "Assign to a human or agent; omit assignee to unassign.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason": {"type": "string"},
                "assignee_type": {"type": "string", "enum": ["human", "agent"]},
                "assignee_id": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "type": "function",
        "name": "delete_task",
        "description": "Delete a task and its relations.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
    },
    {
        "type": "function",
        "name": "add_relation",
        "description": "blocks: source must close before target can start. Cycles are rejected.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
                "reason": {"type": "string"},
                "type": {"type": "string", "enum": ["blocks", "related"]},
            },
            "required": ["source", "target", "reason"],
        },
    },
    {
        "type": "function",
        "name": "remove_relation",
        "description": "Remove a relation.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
                "reason": {"type": "string"},
                "type": {"type": "string", "enum": ["blocks", "related"]},
            },
            "required": ["source", "target", "reason"],
        },
    },
    {
        "type": "function",
        "name": "add_comment",
        "description": "Post to a task's activity feed (progress notes, findings).",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "body": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "body"],
        },
    },
    {
        "type": "function",
        "name": "request_decision",
        "description": "Ask the human a STRATEGY question on a (decision) task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "question": {"type": "string"},
                "reason": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "context": {"type": "string"},
            },
            "required": ["task_id", "question", "reason"],
        },
    },
    {
        "type": "function",
        "name": "resolve_decision",
        "description": "Record a decision you made yourself, with the rationale.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "chosen": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["task_id", "chosen", "rationale"],
        },
    },
]


def _dump(out: Any) -> Any:
    if isinstance(out, list):
        return [_dump(item) for item in out]
    if hasattr(out, "dump"):
        return out.dump()
    return out


class AgentTools:
    """Dispatch Gemini function calls onto MemoryService + TaskGraphService."""

    def __init__(
        self,
        memory: MemoryService,
        taskgraph: TaskGraphService,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.memory = memory
        self.taskgraph = taskgraph
        self.agent_id = agent_id or os.environ.get("INTERVIEW_AGENT_ID", "interview")
        self.project_id = project_id or os.environ.get("MEMORY_PROJECT_ID", DEFAULT_ID)
        self.user_id = user_id or os.environ.get("MEMORY_USER_ID", DEFAULT_ID)
        self.me = Actor(type="agent", id=self.agent_id)

    def declarations(self) -> list[dict[str, Any]]:
        return list(TOOL_DECLARATIONS)

    def dispatch(self, name: str, arguments: dict[str, Any] | None) -> Any:
        args = arguments or {}
        try:
            return self._dispatch(name, args)
        except (MemoryServiceError, TaskGraphError) as e:
            code = getattr(e, "code", "error")
            return {"error": f"{code}: {e}"}
        except (ValidationError, TypeError, ValueError, KeyError) as e:
            return {"error": f"invalid: {e}"}

    def _run_graph(self, fn, *a, **kw) -> Any:
        return _dump(fn(*a, **kw))

    def _submit(self, ops: list[Op], reason: str) -> Any:
        return self._run_graph(self.taskgraph.submit, self.me, ops, reason)

    def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        match name:
            case "get_context":
                return _dump(
                    self.memory.context(
                        project_id=self.project_id,
                        user_id=self.user_id,
                        query=args.get("query"),
                    )
                )
            case "recall":
                return _dump(
                    self.memory.search(
                        args["query"],
                        project_id=self.project_id,
                        user_id=self.user_id,
                        kind=args.get("kind"),
                        limit=int(args.get("limit", 5)),
                    )
                )
            case "remember":
                return _dump(
                    self.memory.remember(
                        self.me,
                        title=args["title"],
                        body=args["body"],
                        kind=args["kind"],
                        scope=args.get("scope", "project"),
                        project_id=self.project_id,
                        user_id=self.user_id,
                        tags=args.get("tags") or [],
                        source=MemorySource(type="conversation"),
                    )
                )
            case "memory_feedback":
                return _dump(
                    self.memory.feedback(
                        self.me,
                        args["memory_id"],
                        bool(args["helpful"]),
                        args.get("note"),
                    )
                )
            case "list_memories":
                return _dump(
                    self.memory.list_memories(
                        scope=args.get("scope"),
                        kind=args.get("kind"),
                        project_id=self.project_id,
                        user_id=self.user_id,
                    )
                )
            case "get_graph":
                return self._run_graph(self.taskgraph.graph)
            case "get_task":
                return self._run_graph(self.taskgraph.task, args["task_id"])
            case "ready_tasks":
                return self._run_graph(self.taskgraph.ready)
            case "blocked_tasks":
                return self._run_graph(self.taskgraph.blocked)
            case "list_decisions":
                return self._run_graph(self.taskgraph.decisions, args.get("state"))
            case "list_activity":
                return self._run_graph(
                    self.taskgraph.activity,
                    args.get("task_id"),
                    since=int(args.get("since", 0)),
                    limit=int(args.get("limit", 50)),
                )
            case "apply_changes":
                ops = OpsAdapter.validate_python(args["ops"])
                return self._submit(ops, args["reason"])
            case "create_task":
                op = CreateTask(
                    id=args.get("id"),
                    title=args["title"],
                    kind=args.get("kind", "task"),
                    description=args.get("description", ""),
                    blocked_by=args.get("blocked_by") or [],
                    parent_id=args.get("parent_id"),
                    start_date=args.get("start_date"),
                    end_date=args.get("end_date"),
                )
                return self._submit([op], args["reason"])
            case "update_task":
                op = UpdateTask(task_id=args["task_id"], **(args.get("fields") or {}))
                return self._submit([op], args["reason"])
            case "set_status":
                return self._submit(
                    [SetStatus(task_id=args["task_id"], status=args["status"])],
                    args["reason"],
                )
            case "assign_task":
                who = None
                if args.get("assignee_type") and args.get("assignee_id"):
                    who = Actor(type=args["assignee_type"], id=args["assignee_id"])
                return self._submit(
                    [AssignTask(task_id=args["task_id"], assignee=who)],
                    args["reason"],
                )
            case "delete_task":
                return self._submit([DeleteTask(task_id=args["task_id"])], args["reason"])
            case "add_relation":
                return self._submit(
                    [
                        AddRelation(
                            source=args["source"],
                            target=args["target"],
                            type=args.get("type", "blocks"),
                        )
                    ],
                    args["reason"],
                )
            case "remove_relation":
                return self._submit(
                    [
                        RemoveRelation(
                            source=args["source"],
                            target=args["target"],
                            type=args.get("type", "blocks"),
                        )
                    ],
                    args["reason"],
                )
            case "add_comment":
                return self._submit(
                    [AddComment(task_id=args["task_id"], body=args["body"])],
                    args.get("reason", "comment"),
                )
            case "request_decision":
                return self._submit(
                    [
                        RequestDecision(
                            task_id=args["task_id"],
                            question=args["question"],
                            options=args.get("options") or [],
                            context=args.get("context"),
                        )
                    ],
                    args["reason"],
                )
            case "resolve_decision":
                return self._submit(
                    [
                        ResolveDecision(
                            task_id=args["task_id"],
                            chosen=args["chosen"],
                            rationale=args["rationale"],
                        )
                    ],
                    args["rationale"],
                )
            case _:
                return {"error": f"unknown tool: {name}"}


def result_as_text(result: Any) -> str:
    """Serialize a tool result for Gemini function_result steps."""
    try:
        return json.dumps(result, default=str)
    except TypeError:
        return str(result)
