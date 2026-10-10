"""One tool table for every agent that works on memory and the task graph.

Each tool is a plain Python function with type hints and a docstring, built
over a `ToolContext` (the shared service instances plus the acting agent).
The same `ToolDef` is used by

- the interview chat agent (`AgentTools(...).declarations()` / `.dispatch()`),
- the in-process Gemini worker that executes task runs (`worker_tools`),
- the planner's MCP server (`register_mcp(server, planner_tools(ctx))`), where
  the MCP SDK derives the schema from the same signature.

Gemini function declarations come from the signature too (pydantic JSON
schema, refs inlined), so a tool is written once. Tool functions raise
`TaskGraphError` / `MemoryServiceError`; `dispatch` turns those into
`{"error": ...}` for the model and the MCP adapter into `ToolError`. `bind`
derives a narrower tool (fixed or defaulted arguments, fewer parameters) for
the worker instead of a copy-pasted variant.

Tool sets: `interview_tools` (memory + graph reads/writes, + issues), `planner_tools`
(graph + run control + scratchpad + engine + artifact reads) and `worker_tools`
(one task, + saving artifacts, + `code_tools` on the run's git worktree when the
project has a repository; see workspaces.py).
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import inspect
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError, create_model

from issues import IssueError, IssueService, agent_author
from memory.models import MemorySource
from memory.service import DEFAULT_ID, MemoryService, MemoryServiceError, user_key
from taskgraph.models import (
    AGENT_ASSIGNEE,
    Actor,
    AddComment,
    AddRelation,
    Artifact,
    AssignTask,
    ChangesetStatus,
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
from taskgraph.service import TaskGraphError, TaskGraphService

if TYPE_CHECKING:
    from artifacts import ProjectArtifacts
    from taskgraph.engine import Engine
    from taskgraph.runlog import RunLog, Scratchpads
    from taskgraph.runs import RunManager
    from workspaces import RunWorkspace

MemoryKind = Literal["strategy", "preference", "decision", "learning", "note"]
Scope = Literal["project", "user", "global"]


# --------------------------------------------------------------------------
# ToolDef
# --------------------------------------------------------------------------


@dataclass
class ToolDef:
    """A named function plus how to describe and call it."""

    name: str
    fn: Callable[..., Any]
    description: str
    # Set by `bind`: arguments filled in (hidden from the model), changed
    # defaults, and the parameters to expose (None: all).
    fixed: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    only: tuple[str, ...] | None = None
    # Hand-written parameter schema when the derived one is too big for Gemini.
    schema_override: dict[str, Any] | None = None

    def parameters(self) -> list[inspect.Parameter]:
        sig = inspect.signature(self.fn, eval_str=True)
        out = []
        for p in sig.parameters.values():
            if p.name in self.fixed or (self.only is not None and p.name not in self.only):
                continue
            if p.name in self.defaults:
                p = p.replace(default=self.defaults[p.name])
            out.append(p)
        return out

    @functools.cached_property
    def args_model(self) -> type[BaseModel]:
        fields: dict[str, Any] = {}
        for p in self.parameters():
            ann = p.annotation if p.annotation is not inspect.Parameter.empty else Any
            fields[p.name] = (ann, ... if p.default is inspect.Parameter.empty else p.default)
        return create_model(f"{self.name}_args", **fields)

    def schema(self) -> dict[str, Any]:
        """JSON schema of the arguments (refs inlined, `X | None` collapsed to X)."""
        if self.schema_override is not None:
            return self.schema_override
        return clean_schema(self.args_model.model_json_schema())

    def declaration(self) -> dict[str, Any]:
        """Function declaration for the Gemini Interactions API."""
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.schema()}

    def call(self, args: dict[str, Any] | None) -> Any:
        """Validate `args` against the signature and call the tool (raises on errors)."""
        try:
            parsed = self.args_model.model_validate(args or {})
        except ValidationError as e:
            raise TaskGraphError("invalid", _short_validation(e)) from None
        kwargs = {k: getattr(parsed, k) for k in type(parsed).model_fields}
        return dump(self.fn(**self.fixed, **kwargs))


def tool(fn: Callable[..., Any], **kw: Any) -> ToolDef:
    return ToolDef(name=fn.__name__, fn=fn, description=inspect.cleandoc(fn.__doc__ or ""), **kw)


def bind(
    t: ToolDef,
    *,
    name: str | None = None,
    description: str | None = None,
    fixed: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
    only: Iterable[str] | None = None,
) -> ToolDef:
    """A narrower variant of `t`: `fixed` args are filled in and hidden,
    `defaults` make args optional, `only` keeps just those parameters (the
    rest use their defaults)."""
    return ToolDef(
        name=name or t.name,
        fn=t.fn,
        description=description or t.description,
        fixed={**t.fixed, **(fixed or {})},
        defaults={**t.defaults, **(defaults or {})},
        only=tuple(only) if only is not None else t.only,
    )


def dump(out: Any) -> Any:
    if isinstance(out, list):
        return [dump(o) for o in out]
    return out.dump() if hasattr(out, "dump") else out


def _short_validation(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err["loc"]) or "arguments"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $refs, drop titles and null defaults, collapse `X | None` to X."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs[node["$ref"].split("/")[-1]]
            return walk({**target, **{k: v for k, v in node.items() if k != "$ref"}})
        out = {k: walk(v) for k, v in node.items() if k not in ("title", "properties")}
        if isinstance(node.get("properties"), dict):  # property names may be "title"
            out["properties"] = {k: walk(v) for k, v in node["properties"].items()}
        any_of = out.get("anyOf")
        if isinstance(any_of, list):
            non_null = [s for s in any_of if s.get("type") != "null"]
            if len(non_null) == 1 and len(non_null) < len(any_of):
                out.pop("anyOf")
                out = {**non_null[0], **out}
        if "default" in out and out["default"] is None:
            out.pop("default")
        return out

    return walk(schema)


def result_as_text(result: Any, limit: int | None = None) -> str:
    """Serialize a tool result for Gemini function_result steps."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str, ensure_ascii=False)
        except TypeError:
            text = str(result)
    if limit is not None and len(text) > limit:
        text = text[:limit] + f"… [{len(text) - limit} more characters]"
    return text


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------


@dataclass
class ToolContext:
    """The services the tools act on, and who is acting. Optional services
    (runs, scratchpads, ...) are only needed by the tools that use them."""

    taskgraph: TaskGraphService | None
    actor: Actor
    memory: MemoryService | None = None
    runs: RunManager | None = None
    scratchpads: Scratchpads | None = None
    runlog: RunLog | None = None
    engine: Engine | None = None
    issues: IssueService | None = None
    artifacts: ProjectArtifacts | None = None
    project_id: str | None = None  # None: the active project (MemoryService decides)
    user_id: str = DEFAULT_ID
    workspace: RunWorkspace | None = None  # the run's git worktree (worker runs on a repo)

    def need(self, what: str) -> Any:
        value = getattr(self, what)
        if value is None:
            raise TaskGraphError("invalid", f"{what} is not available here")
        return value

    @property
    def tg(self) -> TaskGraphService:
        return self.need("taskgraph")

    def submit(self, ops: list[Op], reason: str) -> Any:
        return self.tg.submit(self.actor, ops, reason)


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------


def memory_tools(ctx: ToolContext) -> list[ToolDef]:
    def mem() -> MemoryService:
        return ctx.need("memory")

    def get_context(query: str | None = None) -> dict:
        """Everything memory knows that applies now: project strategy, owner
        preferences, orchestrator lessons, plus entries matching query."""
        return mem().context(project_id=ctx.project_id, user_id=ctx.user_id, query=query)

    def recall(query: str, kind: MemoryKind | None = None, limit: int = 5) -> list[dict]:
        """Search memory. Use before asking the owner a question."""
        return mem().search(query, project_id=ctx.project_id, user_id=ctx.user_id,
                            kind=kind, limit=limit)

    def remember(
        title: str, body: str, kind: MemoryKind, scope: Scope = "project", tags: list[str] | None = None
    ) -> dict:
        """Store something worth knowing next session. scope 'project' for this
        project, 'user' for an owner preference across projects, 'global' for
        a lesson about orchestrating. Same title updates."""
        return mem().remember(
            ctx.actor, title=title, body=body, kind=kind, scope=scope,
            project_id=ctx.project_id, user_id=ctx.user_id, tags=tags or [],
            source=MemorySource(type="conversation"),
        )

    def memory_feedback(memory_id: str, helpful: bool, note: str | None = None) -> dict:
        """Report whether a memory held up. Not helpful lowers confidence;
        repeated, it is archived."""
        return mem().feedback(ctx.actor, memory_id, helpful, note)

    def list_memories(scope: Scope | None = None, kind: MemoryKind | None = None) -> list[dict]:
        """List active memories, optionally by scope or kind."""
        return mem().visible(scope=scope, kind=kind, project_id=ctx.project_id,
                             user_id=ctx.user_id)

    return [tool(f) for f in (get_context, recall, remember, memory_feedback, list_memories)]


# --------------------------------------------------------------------------
# Graph reads
# --------------------------------------------------------------------------


def graph_read_tools(ctx: ToolContext) -> list[ToolDef]:
    def get_graph() -> dict:
        """Whole project: settings, tasks, edges, pending decisions/changesets, active runs."""
        return ctx.tg.graph()

    def get_task(task_id: str) -> dict:
        """One task with its activity feed (comments, changes), dependents, children and active run."""
        return ctx.tg.task(task_id)

    def ready_tasks() -> list[dict]:
        """Open tasks whose blockers are all closed (can be started now)."""
        return ctx.tg.ready()

    def blocked_tasks() -> list[dict]:
        """Unfinished tasks and what they are waiting on."""
        return ctx.tg.blocked()

    def list_decisions(state: Literal["pending", "resolved"] | None = None) -> list[dict]:
        """Decision log: questions, choices and rationale."""
        return ctx.tg.decisions(state)

    def list_activity(task_id: str | None = None, since: int = 0, limit: int = 50) -> list[dict]:
        """Activity feed, optionally for one task, after event seq `since`."""
        return ctx.tg.activity(task_id, since=since, limit=limit)

    def list_changesets(status: ChangesetStatus | None = None) -> list[dict]:
        """Changesets (batches of changes): applied, proposed (awaiting human review) or rejected."""
        return ctx.tg.changesets(status)

    def get_events(since: int = 0, limit: int = 100) -> list[dict]:
        """Raw event log after seq `since` (every change, run start/stop, review)."""
        return ctx.tg.events_since(since, limit)

    return [tool(f) for f in (get_graph, get_task, ready_tasks, blocked_tasks, list_decisions,
                              list_activity, list_changesets, get_events)]


# --------------------------------------------------------------------------
# Graph writes
# --------------------------------------------------------------------------

# list[Op] expands to a large discriminated union; Gemini gets a plain array.
_APPLY_CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {"type": "array", "items": {"type": "object"},
                "description": "Ops like {op: 'task.create', id, title, blockedBy: [...]}"},
        "reason": {"type": "string"},
    },
    "required": ["ops", "reason"],
}


def with_default_assignee(ctx: ToolContext, ops: list[Op]) -> list[Op]:
    """Tasks agents create go to "Agent" (AGENT_ASSIGNEE) unless the op names an
    assignee (an explicit `assignee: null` stays unassigned). Decision tasks
    stay unassigned: the owner answers them. Applied here, in the agent tool
    layer, so tasks humans create in the GUI keep whatever they set."""
    out: list[Op] = []
    for op in ops:
        if (isinstance(op, CreateTask) and op.kind != "decision"
                and "assignee" not in op.model_fields_set):
            op = op.model_copy(update={"assignee": AGENT_ASSIGNEE})
        out.append(op)
    return out


def graph_write_tools(ctx: ToolContext) -> list[ToolDef]:
    def apply_changes(ops: list[Op], reason: str) -> dict:
        """Apply a batch of ops atomically (all or nothing). Later ops may
        reference tasks created earlier in the batch by their id. Every change
        needs a reason. task.create ops without an assignee go to "Agent"
        (decision tasks stay unassigned)."""
        return ctx.submit(with_default_assignee(ctx, ops), reason)

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
        assignee_type: Literal["human", "agent"] | None = None,
        assignee_id: str | None = None,
    ) -> dict:
        """Create a task. blocked_by lists tasks that must close first. Without
        an assignee, start/task/end tasks go to "Agent" (agent id 'agent');
        decision tasks stay unassigned (the owner answers them)."""
        who = {"assignee": {"type": assignee_type, "id": assignee_id}} if assignee_type and assignee_id else {}
        try:
            op = CreateTask(
                id=id, title=title, kind=kind, description=description,
                blocked_by=blocked_by or [], parent_id=parent_id,
                start_date=start_date, end_date=end_date, **who,
            )
        except ValidationError as e:
            raise TaskGraphError("invalid", _short_validation(e)) from None
        return ctx.submit(with_default_assignee(ctx, [op]), reason)

    def update_task(task_id: str, fields: dict, reason: str) -> dict:
        """Update fields: title, kind, description, parentId, startDate, endDate, progress, artifacts."""
        try:
            op = UpdateTask(task_id=task_id, **fields)
        except (ValidationError, TypeError) as e:
            raise TaskGraphError("invalid", str(e)) from None
        return ctx.submit([op], reason)

    def update_task_fields(
        task_id: str,
        reason: str,
        title: str | None = None,
        description: str | None = None,
        progress: float | None = None,
        artifacts: list[Artifact] | None = None,
        add_artifacts: list[Artifact] | None = None,
    ) -> dict:
        """Update a task's title, description, progress (0 to 1) or artifacts.
        `artifacts` replaces the list, `add_artifacts` appends to it; omitted
        fields stay as they are."""
        fields: dict[str, Any] = {
            k: v for k, v in (("title", title), ("description", description), ("progress", progress))
            if v is not None
        }
        if artifacts is not None:
            fields["artifacts"] = artifacts
        if add_artifacts:
            current = fields.get("artifacts") or [
                Artifact.model_validate(a) for a in ctx.tg.task(task_id)["artifacts"]
            ]
            # One entry per path; a re-added path takes the new label.
            fields["artifacts"] = list({a.path: a for a in [*current, *add_artifacts]}.values())
        if not fields:
            raise TaskGraphError("invalid", "nothing to update")
        try:
            op = UpdateTask(task_id=task_id, **fields)
        except ValidationError as e:
            raise TaskGraphError("invalid", _short_validation(e)) from None
        return ctx.submit([op], reason)

    def set_status(task_id: str, status: TaskStatus, reason: str) -> dict:
        """Move a task: open | running | in-review | closed (blockers must be closed)."""
        return ctx.submit([SetStatus(task_id=task_id, status=status)], reason)

    def assign_task(
        task_id: str,
        reason: str,
        assignee_type: Literal["human", "agent"] | None = None,
        assignee_id: str | None = None,
    ) -> dict:
        """Assign to a human or agent; omit assignee to unassign."""
        who = Actor(type=assignee_type, id=assignee_id) if assignee_type and assignee_id else None
        return ctx.submit([AssignTask(task_id=task_id, assignee=who)], reason)

    def delete_task(task_id: str, reason: str) -> dict:
        """Delete a task and its relations."""
        return ctx.submit([DeleteTask(task_id=task_id)], reason)

    def add_relation(
        source: str, target: str, reason: str, type: Literal["blocks", "related"] = "blocks"
    ) -> dict:
        """'blocks': source must close before target can start. Cycles are rejected."""
        return ctx.submit([AddRelation(source=source, target=target, type=type)], reason)

    def remove_relation(
        source: str, target: str, reason: str, type: Literal["blocks", "related"] = "blocks"
    ) -> dict:
        """Remove a relation."""
        return ctx.submit([RemoveRelation(source=source, target=target, type=type)], reason)

    def add_comment(task_id: str, body: str, reason: str = "comment") -> dict:
        """Post to a task's activity feed (progress notes, findings)."""
        if not body.strip():
            raise TaskGraphError("invalid", "comment body is empty")
        return ctx.submit([AddComment(task_id=task_id, body=body)], reason)

    def request_decision(
        task_id: str,
        question: str,
        reason: str,
        options: list[str] | None = None,
        context: str | None = None,
    ) -> dict:
        """Ask the human a STRATEGY question (goals, priorities, success criteria) on a task."""
        return ctx.submit(
            [RequestDecision(task_id=task_id, question=question, options=options or [], context=context)],
            reason,
        )

    def resolve_decision(task_id: str, chosen: str, rationale: str) -> dict:
        """Record a decision you made yourself, with the rationale."""
        return ctx.submit([ResolveDecision(task_id=task_id, chosen=chosen, rationale=rationale)], rationale)

    return [
        tool(apply_changes, schema_override=_APPLY_CHANGES_SCHEMA),
        *(tool(f) for f in (create_task, update_task, update_task_fields, set_status, assign_task,
                            delete_task, add_relation, remove_relation, add_comment,
                            request_decision, resolve_decision)),
    ]


# --------------------------------------------------------------------------
# Run control
# --------------------------------------------------------------------------


def run_tools(ctx: ToolContext) -> list[ToolDef]:
    def runs() -> RunManager:
        return ctx.need("runs")

    def active_run_id(task_id: str) -> str:
        active = ctx.tg.runs(task_id=task_id, active=True)
        if not active:
            ctx.tg.task(task_id)  # not_found for unknown tasks
            raise TaskGraphError("not_found", f"'{task_id}' has no active run")
        return active[0].id

    def start_task(
        task_id: str, reason: str, agent_id: str = "worker", instructions: str | None = None
    ) -> dict:
        """Put agent `agent_id` to work on a task. The task must be ready (not
        closed, blockers closed, no pending decision, no active run); it moves
        to 'running' and is assigned to the agent. Returns the run."""
        return runs().start(ctx.actor, task_id, reason, agent_id=agent_id, instructions=instructions)

    def stop_task(task_id: str, reason: str) -> dict:
        """Stop the agent working on a task. The task goes back to 'open'."""
        return runs().stop(ctx.actor, active_run_id(task_id), reason)

    def restart_task(
        task_id: str, reason: str, agent_id: str | None = None, instructions: str | None = None
    ) -> dict:
        """Stop the task's active run (if any) and start a new attempt. Agent and
        instructions carry over from the last run unless you pass new ones."""
        return runs().restart(ctx.actor, task_id, reason, agent_id=agent_id, instructions=instructions)

    def start_ready_tasks(reason: str, agent_id: str = "worker", limit: int | None = None) -> list[dict]:
        """Start runs on every ready work task (kind 'task'), up to `limit`."""
        ready = [t["id"] for t in ctx.tg.ready() if t["kind"] == "task"]
        started = []
        for task_id in ready[:limit] if limit is not None else ready:
            if not ctx.tg.runs(task_id=task_id, active=True):
                started.append(runs().start(ctx.actor, task_id, reason, agent_id=agent_id))
        return started

    def stop_all_runs(reason: str) -> list[dict]:
        """Stop every active run (the whole graph run)."""
        return [runs().stop(ctx.actor, r.id, reason) for r in runs().active()]

    def finish_run(run_id: str, outcome: Literal["succeeded", "failed"], summary: str) -> dict:
        """Report how a run ended, with a short summary of what was done (or why it
        failed). succeeded -> the task goes to 'in-review'; failed -> back to 'open'."""
        return runs().finish(ctx.actor, run_id, outcome, summary)

    def list_active_runs() -> list[dict]:
        """Agents working right now: run id, task, agent, runner, since when."""
        return runs().active()

    def list_runs(task_id: str | None = None, status: RunStatus | None = None) -> list[dict]:
        """Run history, optionally for one task or one status."""
        return ctx.tg.runs(task_id=task_id, status=status)

    def get_run(run_id: str) -> dict:
        """One run: agent, status, attempt, exit code, note."""
        return ctx.tg.run(run_id)

    def get_run_steps(run_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        """What an in-process agent did during a run (messages, tool calls,
        results, errors), after step `after`."""
        ctx.tg.run(run_id)
        return ctx.need("runlog").steps(run_id, after, limit)

    return [tool(f) for f in (start_task, stop_task, restart_task, start_ready_tasks, stop_all_runs,
                              finish_run, list_active_runs, list_runs, get_run, get_run_steps)]


# --------------------------------------------------------------------------
# Scratchpad
# --------------------------------------------------------------------------


def scratchpad_tools(ctx: ToolContext) -> list[ToolDef]:
    def pads() -> Scratchpads:
        return ctx.need("scratchpads")

    def written(result: dict) -> dict:
        return {"taskId": result["taskId"], "chars": len(result["content"]),
                "updatedAt": result["updatedAt"]}

    def read_scratchpad(task_id: str) -> dict:
        """A task's markdown scratchpad: working notes that persist across runs."""
        ctx.tg.task(task_id)
        return pads().get(task_id)

    def write_scratchpad(task_id: str, content: str) -> dict:
        """Replace a task's scratchpad with `content` (markdown)."""
        ctx.tg.task(task_id)
        try:
            return written(pads().write(task_id, content, ctx.actor.id))
        except ValueError as e:
            raise TaskGraphError("invalid", str(e)) from None

    def append_scratchpad(task_id: str, text: str) -> dict:
        """Append `text` (markdown) to the end of a task's scratchpad."""
        ctx.tg.task(task_id)
        try:
            return written(pads().append(task_id, text, ctx.actor.id))
        except ValueError as e:
            raise TaskGraphError("invalid", str(e)) from None

    return [tool(f) for f in (read_scratchpad, write_scratchpad, append_scratchpad)]


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


def engine_tools(ctx: ToolContext) -> list[ToolDef]:
    def engine() -> Engine:
        return ctx.need("engine")

    def engine_status() -> dict:
        """Engine state (running/stopped), active runs, concurrency limit, the
        queue of tasks it would start next, and `waiting`: why it is idle
        (tasks in review, pending decisions, blocked tasks, tasks it will not
        restart, autonomy or the run limit) with a one-line `summary`."""
        return engine().status()

    def engine_start(reason: str) -> dict:
        """Start the engine: it keeps starting agent runs on ready work tasks, up
        to its concurrency limit."""
        return engine().start(ctx.actor, reason)

    def engine_stop(reason: str, stop_runs: bool = False) -> dict:
        """Stop dispatching new runs. With stop_runs=true, also stop every active run."""
        return engine().stop(ctx.actor, reason, stop_runs=stop_runs)

    return [tool(f) for f in (engine_status, engine_start, engine_stop)]


# --------------------------------------------------------------------------
# Issues (bug reports, requests; issues.py). The MCP server registers
# issues.register_mcp_tools instead, which adds get_issue_screenshot.
# --------------------------------------------------------------------------


def issue_tools(ctx: ToolContext) -> list[ToolDef]:
    def svc() -> IssueService:
        return ctx.need("issues")

    def me():
        return agent_author(ctx.actor.id)

    def list_issues(
        status: str | None = None,
        search: str | None = None,
        author: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Issues (bug reports, requests), newest first. status: comma-separated
        draft/open/in_progress/closed or 'all' (default: everything published).
        search matches title, description or #number; author is an email."""
        return svc().list_issues(status=status, search=search, author=author,
                                 assignee=assignee, label=label, limit=limit)

    def get_issue(issue: str) -> dict:
        """One issue (id or number like '#12') with description, comments and
        screenshot metadata (url, path)."""
        out = svc().get(issue)
        for a in out["attachments"]:
            a["path"] = str(svc().attachment_path(a).resolve())
        return out

    def create_issue(
        title: str, description: str = "", labels: list[str] | None = None, assignee: str | None = None
    ) -> dict:
        """File a new (open) issue, e.g. a bug found while working."""
        return svc().create(me(), title=title, description=description, labels=labels, assignee=assignee)

    def update_issue(
        issue: str,
        title: str | None = None,
        description: str | None = None,
        status: Literal["open", "in_progress"] | None = None,
        labels: list[str] | None = None,
        assignee: str | None = None,
    ) -> dict:
        """Change an issue's title, description, labels or assignee ('' unassigns,
        'agent:<id>' or an email), or move it between open and in_progress."""
        return svc().update(me(), issue, title=title, description=description, status=status,
                            labels=labels, assignee=assignee)

    def comment_on_issue(issue: str, body: str) -> dict:
        """Add a comment (markdown) to an issue: progress, findings, questions."""
        return svc().add_comment(me(), issue, body)

    def close_issue(issue: str, reason: str) -> dict:
        """Close an issue, saying why (fixed, duplicate of #3, won't fix...)."""
        return svc().close(me(), issue, reason)

    def reopen_issue(issue: str) -> dict:
        """Reopen a closed issue."""
        return svc().reopen(me(), issue)

    return [tool(f) for f in (list_issues, get_issue, create_issue, update_issue,
                              comment_on_issue, close_issue, reopen_issue)]


# --------------------------------------------------------------------------
# Artifacts (files on a task, mostly HTML reports; artifacts.py)
# --------------------------------------------------------------------------

ArtifactKind = Literal["html", "markdown", "json", "csv", "text", "svg"]


def artifact_tools(ctx: ToolContext) -> list[ToolDef]:
    def store() -> ProjectArtifacts:
        return ctx.need("artifacts")

    def save_artifact(
        task_id: str,
        filename: str,
        content: str,
        title: str,
        kind: ArtifactKind = "html",
        run_id: str | None = None,
    ) -> dict:
        """Save a file (max 2 MB) on a task and list it in the task's artifacts,
        where the owner opens it. kind 'html' for reports. Saving the same
        filename again in the same run replaces it."""
        ctx.tg.task(task_id)
        meta, _ = store().save(task_id, filename, content, title=title, kind=kind,
                               run_id=run_id, created_by=ctx.actor.id)
        entry = Artifact(path=meta["viewUrl"], label=meta["title"])
        current = [Artifact.model_validate(a) for a in ctx.tg.task(task_id)["artifacts"]]
        if entry not in current:
            others = [a for a in current if a.path != entry.path]
            ctx.submit([UpdateTask(task_id=task_id, artifacts=[*others, entry])],
                       f"artifact: {meta['title']}")
        out = {k: meta[k] for k in ("id", "filename", "title", "kind", "size", "viewUrl")}
        return {**out, "note": "saved and listed in the task's artifacts"}

    def list_artifacts(task_id: str) -> list[dict]:
        """Files saved on a task (reports and other artifacts): id, title, kind,
        size, run, created by and when, view URL."""
        ctx.tg.task(task_id)
        return store().list(task_id)

    def read_artifact(artifact_id: str, max_chars: int = 50_000) -> dict:
        """One artifact's metadata and its content as text (truncated to max_chars)."""
        meta, text = store().read_text(artifact_id)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n… [{len(text) - max_chars} more characters]"
        return {**meta, "content": text}

    return [tool(f) for f in (save_artifact, list_artifacts, read_artifact)]


# --------------------------------------------------------------------------
# Code (the run's git worktree; workspaces.py). Worker set only.
# --------------------------------------------------------------------------


def _ws_call(fn: Callable[..., Any]) -> Callable[..., Any]:
    """WorkspaceError -> TaskGraphError, so dispatch reports it to the model."""
    from workspaces import WorkspaceError

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except WorkspaceError as e:
            raise TaskGraphError(e.code, str(e)) from None
    return wrapper


def code_tools(ctx: ToolContext) -> list[ToolDef]:
    def ws() -> RunWorkspace:
        return ctx.need("workspace")

    def list_files(glob: str = "**") -> dict:
        """Files in the repository (tracked and new, not ignored) matching a glob,
        e.g. '**/*.py', 'src/*', 'README*'."""
        return ws().list_files(glob)

    def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        """Read a text file (path relative to the repository root) with line
        numbers, optionally only lines start_line..end_line. Long files are cut;
        the result says where to continue."""
        return ws().read_file(path, start_line, end_line)

    def search_code(pattern: str, glob: str | None = None, ignore_case: bool = False) -> dict:
        """Search file contents with an extended regular expression (git grep -E).
        Returns 'path:line:text' matches; glob narrows the files (e.g. '*.py')."""
        return ws().search_code(pattern, glob, ignore_case)

    def write_file(path: str, content: str) -> dict:
        """Create or overwrite a file with `content`. Prefer edit_file for changes
        to existing files."""
        return ws().write_file(path, content)

    def edit_file(path: str, old: str, new: str) -> dict:
        """Replace the exact text `old` with `new` in a file. `old` must occur
        exactly once (whitespace included); add surrounding lines to make it unique."""
        return ws().edit_file(path, old, new)

    def delete_file(path: str) -> dict:
        """Delete a file."""
        return ws().delete_file(path)

    def git_status() -> dict:
        """Your branch, the main commit you started from, your commits so far and
        uncommitted changes."""
        return ws().status()

    def git_diff(stat_only: bool = False, path: str | None = None) -> dict:
        """What you changed since you started (commits and uncommitted edits):
        a diff stat (files, +/- lines) and the diff (truncated). stat_only=true
        for just the stat, e.g. for your report."""
        return ws().diff(stat_only, path)

    def run_check(name: str) -> dict:
        """Run one of the project's named checks (e.g. tests) in your worktree and
        get passed, exit code and the output (head and tail if long). Only the
        configured checks exist; there is no other shell."""
        return ws().run_check(name)

    def commit(message: str) -> dict:
        """Stage every change and commit it with `message` (first line: a short
        summary in the imperative; then a blank line and details if useful)."""
        return ws().commit(message)

    return [tool(_ws_call(f)) for f in (list_files, read_file, search_code, write_file, edit_file,
                                         delete_file, git_status, git_diff, run_check, commit)]


def record_landing(ctx: ToolContext, run_id: str, task_id: str, landed: dict[str, Any]) -> None:
    """Note pushed commits on the task (comment + compare link) and in the run's steps."""
    commits = landed["commits"]
    files = landed["files"]
    lines = [f"Pushed {len(commits)} commit{'s' * (len(commits) != 1)} to {landed['branch']} "
             f"({len(files)} file{'s' * (len(files) != 1)}, +{landed['added']} -{landed['removed']}):"]
    lines += [f"- {c['short']} {c['subject']}" for c in commits[:20]]
    if landed.get("compareUrl"):
        lines.append(f"Compare: {landed['compareUrl']}")
    ops: list[Op] = [AddComment(task_id=task_id, body="\n".join(lines))]
    if landed.get("compareUrl"):
        current = [Artifact.model_validate(a) for a in ctx.tg.task(task_id)["artifacts"]]
        label = f"Commits {commits[0]['short']}" + (f"..{commits[-1]['short']}" if len(commits) > 1 else "")
        ops.append(UpdateTask(task_id=task_id,
                              artifacts=[*current, Artifact(path=landed["compareUrl"], label=label)]))
    try:
        ctx.submit(ops, f"{run_id} pushed to {landed['branch']}")
    except TaskGraphError:
        pass  # the push happened; the run step below still records it
    if ctx.runlog is not None:
        ctx.runlog.add(run_id, task_id, "landing", {"state": "pushed", **landed})


def landing_finish_tool(ctx: ToolContext, run_id: str, task_id: str) -> ToolDef:
    """finish_run for a run on a repository: on success the commits land on main first."""
    from workspaces import LandingError

    def finish_run(outcome: Literal["succeeded", "failed"], summary: str) -> dict:
        """End your run. outcome 'succeeded' when the task is done: your commits
        are then rebased onto the latest main, the project's checks run, and
        they are pushed to main (if a check fails you get its output and one
        chance to fix it; a rebase conflict fails the run). 'failed' when you
        cannot finish it: nothing is pushed. summary: what you did, what is left,
        where the results are. Call it exactly once, as your last action."""
        rw = ctx.need("workspace")
        runs = ctx.need("runs")
        if outcome != "succeeded":
            return runs.finish(ctx.actor, run_id, outcome, summary)
        try:
            landed = rw.land()
        except LandingError as e:
            if e.kind == "dirty":
                raise TaskGraphError("invalid", str(e)) from None
            if ctx.runlog is not None:
                ctx.runlog.add(run_id, task_id, "landing",
                               {"state": "failed", "kind": e.kind, "error": str(e), "output": e.output})
            if e.kind == "checks" and rw.check_failures == 0:
                rw.check_failures += 1
                raise TaskGraphError(
                    "checks_failed",
                    f"{e}. Output:\n{e.output}\n\nYour commits are now on top of the latest main. "
                    "Fix the cause, commit, and call finish_run again; if the checks fail again "
                    "the run fails.") from None
            note = f"{summary}\n\nNot pushed: {e}"
            if e.output:
                note += f"\n{e.output[-1500:]}"
            run = runs.finish(ctx.actor, run_id, "failed", note)
            return {"run": run.dump(), "pushed": False, "error": str(e),
                    "note": "the run failed; nothing was pushed"}
        if landed["pushed"]:
            record_landing(ctx, run_id, task_id, landed)
            shas = ", ".join(c["short"] for c in landed["commits"][:10])
            summary = f"{summary}\n\nPushed to {landed['branch']}: {shas}"
        run = runs.finish(ctx.actor, run_id, outcome, summary)
        return {"run": run.dump(), "pushed": landed["pushed"],
                "commits": [f"{c['short']} {c['subject']}" for c in landed["commits"]],
                "compareUrl": landed.get("compareUrl")}

    return tool(finish_run)


# --------------------------------------------------------------------------
# Tool sets
# --------------------------------------------------------------------------


def by_name(tools: Iterable[ToolDef]) -> dict[str, ToolDef]:
    return {t.name: t for t in tools}


INTERVIEW_TOOLS = (
    "get_context", "recall", "remember", "memory_feedback", "list_memories",
    "get_graph", "get_task", "ready_tasks", "blocked_tasks", "list_decisions", "list_activity",
    "apply_changes", "create_task", "update_task", "set_status", "assign_task", "delete_task",
    "add_relation", "remove_relation", "add_comment", "request_decision", "resolve_decision",
)


def interview_tools(ctx: ToolContext) -> list[ToolDef]:
    """The strategy chat agent: memory plus graph reads and writes."""
    t = by_name([*memory_tools(ctx), *graph_read_tools(ctx), *graph_write_tools(ctx)])
    tools = [t[n] for n in INTERVIEW_TOOLS]
    if ctx.issues is not None:
        tools += issue_tools(ctx)
    return tools


def planner_tools(ctx: ToolContext) -> list[ToolDef]:
    """The planner over MCP: graph, run control, scratchpads and the engine.
    (Memory tools come from memory.mcp_server on the same server.)"""
    reads = [t for t in artifact_tools(ctx) if t.name != "save_artifact"]
    return [*graph_read_tools(ctx), *graph_write_tools(ctx), *run_tools(ctx),
            *scratchpad_tools(ctx), *engine_tools(ctx), *reads]


def worker_tools(ctx: ToolContext, *, run_id: str, task_id: str) -> list[ToolDef]:
    """An agent working on `task_id` in run `run_id`: read the graph, comment,
    update its own task, create subtasks and relations, ask a human, use
    memory and the scratchpad, and finish its run."""
    t = by_name([*memory_tools(ctx), *graph_read_tools(ctx), *graph_write_tools(ctx),
                 *run_tools(ctx), *scratchpad_tools(ctx), *artifact_tools(ctx)])
    mine = {"task_id": task_id}
    tools = [
        bind(t["get_task"], defaults=mine,
             description="One task (default: yours) with its activity feed (comments, changes), "
                         "dependents, children and active run."),
        t["get_graph"],
        bind(t["add_comment"], defaults=mine,
             description="Post a progress note or finding to a task's activity feed (default: your task)."),
        bind(t["update_task_fields"], name="update_my_task", fixed=mine,
             only=("reason", "description", "progress", "add_artifacts"),
             description="Update your own task: description, progress (0 to 1), or add artifacts "
                         "({path, label}; a path can be a URL)."),
        bind(t["create_task"], name="create_subtask", fixed={"parent_id": task_id, "kind": "task"},
             only=("title", "reason", "description", "blocked_by", "id"),
             description="Create a subtask of your task for follow-up work you will not do in this "
                         "run. blocked_by lists tasks that must close first."),
        t["add_relation"],
        bind(t["request_decision"], defaults=mine,
             description="Ask the human a STRATEGY question (goals, priorities, success criteria) on a "
                         "task (default: yours). Then finish your run with outcome 'failed' and a summary "
                         "saying you are waiting for the answer; the task can restart once it is answered."),
        bind(t["read_scratchpad"], defaults=mine,
             description="Read a task's markdown scratchpad (default: yours): working notes that persist "
                         "across runs."),
        bind(t["write_scratchpad"], fixed=mine,
             description="Replace your task's scratchpad with `content` (markdown). Keep it a current "
                         "summary: plan, findings, open questions, next steps."),
        bind(t["append_scratchpad"], fixed=mine,
             description="Append `text` (markdown) to your task's scratchpad."),
        bind(t["save_artifact"], fixed={**mine, "run_id": run_id},
             description="Save a file on your task, listed in its artifacts for the owner to open "
                         "(max 2 MB). kind 'html' (default) for your report: one self-contained page. "
                         "Saving the same filename again replaces it. It is added to the task's "
                         "artifacts for you; do not add it with update_my_task."),
        bind(t["list_artifacts"], defaults=mine,
             description="Files saved on a task (default: yours), e.g. reports of the tasks before it."),
        t["read_artifact"],
        t["get_context"],
        t["recall"],
        t["remember"],
        bind(t["finish_run"], fixed={"run_id": run_id},
             description="End your run. outcome 'succeeded' when the task is done (it goes to human "
                         "review), 'failed' when you cannot finish it. summary: what you did, what is "
                         "left, where the results are. Call it exactly once, as your last action."),
    ]
    if ctx.memory is None:
        tools = [x for x in tools if x.name not in ("get_context", "recall", "remember")]
    if ctx.artifacts is None:
        tools = [x for x in tools if x.name not in ("save_artifact", "list_artifacts", "read_artifact")]
    if ctx.issues is not None:
        tools += issue_tools(ctx)
    if ctx.workspace is not None:  # the project has a repository: code tools, landing on finish
        tools = [x for x in tools if x.name != "finish_run"]
        tools += [*code_tools(ctx), landing_finish_tool(ctx, run_id, task_id)]
    return tools


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------


class AgentTools:
    """Dispatch Gemini function calls onto the services. Defaults to the
    interview chat's tool set; pass `toolset` for another one."""

    def __init__(
        self,
        memory: MemoryService | None,
        taskgraph: TaskGraphService | None,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        toolset: Callable[[ToolContext], list[ToolDef]] = interview_tools,
        **services: Any,
    ) -> None:
        self.agent_id = agent_id or os.environ.get("INTERVIEW_AGENT_ID", "interview")
        self.me = Actor(type="agent", id=self.agent_id)
        self.ctx = ToolContext(
            taskgraph=taskgraph, actor=self.me, memory=memory,
            # Unset: memory follows the project of the request (or the run's).
            project_id=project_id or os.environ.get("MEMORY_PROJECT_ID"),
            user_id=user_id or os.environ.get("MEMORY_USER_ID", DEFAULT_ID),
            **services,
        )
        self.memory, self.taskgraph = memory, taskgraph
        self.project_id, self.user_id = self.ctx.project_id, self.ctx.user_id
        self.toolset = toolset
        self.tools = by_name(toolset(self.ctx))

    def for_user(self, identity: str) -> AgentTools:
        """The same tools with memory bound to a signed-in user, so one owner's
        preferences are not stored under, or read by, another. An explicit
        MEMORY_USER_ID keeps its binding."""
        if os.environ.get("MEMORY_USER_ID"):
            return self
        bound = copy.copy(self)
        bound.user_id = user_key(identity)
        bound.ctx = dataclasses.replace(self.ctx, user_id=bound.user_id)
        bound.tools = by_name(self.toolset(bound.ctx))
        return bound

    def memory_context(self) -> str:
        """What memory knows for this project and owner, as a prompt block."""
        return self.ctx.need("memory").context(project_id=self.project_id, user_id=self.user_id)["text"]

    def declarations(self) -> list[dict[str, Any]]:
        return [t.declaration() for t in self.tools.values()]

    def call(self, name: str, arguments: dict[str, Any] | None) -> Any:
        """Run a tool; raises TaskGraphError / MemoryServiceError."""
        t = self.tools.get(name)
        if t is None:
            raise TaskGraphError("not_found", f"unknown tool: {name}")
        return t.call(arguments)

    def dispatch(self, name: str, arguments: dict[str, Any] | None) -> Any:
        """Run a tool; errors come back as {"error": "..."} for the model."""
        if name not in self.tools:
            return {"error": f"unknown tool: {name}"}
        try:
            return self.call(name, arguments)
        except (MemoryServiceError, TaskGraphError, IssueError) as e:
            return {"error": f"{getattr(e, 'code', 'error')}: {e}"}
        except (ValidationError, TypeError, ValueError, KeyError) as e:
            return {"error": f"invalid: {e}"}


TOOL_DECLARATIONS: list[dict[str, Any]] = [
    t.declaration() for t in interview_tools(ToolContext(taskgraph=None, actor=Actor(type="agent", id="x")))
]


def register_mcp(server: Any, tools: Iterable[ToolDef]) -> None:
    """Register tools on an MCPServer; TaskGraphError becomes ToolError."""
    from mcp.server.mcpserver.exceptions import ToolError

    for t in tools:
        if t.fixed or t.defaults or t.only is not None:
            raise ValueError(f"bound tool {t.name} cannot be registered on MCP")

        def make(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            def handler(*a: Any, **kw: Any) -> Any:
                try:
                    return dump(fn(*a, **kw))
                except (TaskGraphError, MemoryServiceError, IssueError) as e:
                    raise ToolError(f"{e.code}: {e}") from None
            return handler

        server.add_tool(make(t.fn), name=t.name, description=t.description)
