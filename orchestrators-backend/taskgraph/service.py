"""TaskGraphService: the one API used by both the GUI (via REST) and agents (via MCP).

Every write goes through `submit(actor, ops, reason)`:

1. Ops are validated as a batch against a draft copy of the graph. If any op is
   invalid, nothing is written and a `TaskGraphError` explains which op and why.
2. Humans' changesets always apply. Agents' changesets apply or wait for review
   depending on the project's autonomy setting:
     - "auto" (default): apply immediately; humans can review afterwards.
     - "ask-structural": wait for approval if the batch creates/deletes tasks,
       changes relations or resolves a decision; status/comments apply directly.
     - "ask-all": every agent changeset waits for approval.
3. Agents must give a `reason`; it is stored on every event and shown in activity.

Agent runs (`start_run` / `end_run`) are runtime records, not changesets: they
say which agent is working on which task. They live in the same event log; what
actually executes a run is up to the runner in `runs.py`.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from .graph import GraphState, apply_event, dump_op
from .models import (
    STRUCTURAL_OPS,
    Actor,
    AgentRun,
    AssignTask,
    Autonomy,
    Changeset,
    ChangesetStatus,
    Event,
    Op,
    ProjectSettings,
    RunStatus,
    SetStatus,
    SubmitResult,
    Task,
)
from .store import EventStore, MemoryEventStore

WORK_STATUSES = {"running", "in-review", "closed"}


class TaskGraphError(Exception):
    """Validation or permission failure. `code` is machine-readable."""

    def __init__(self, code: str, message: str, *, op_index: int | None = None):
        prefix = f"op[{op_index}]: " if op_index is not None else ""
        super().__init__(prefix + message)
        self.code = code
        self.message = message
        self.op_index = op_index

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "opIndex": self.op_index}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40].strip("-") or "task"


class TaskGraphService:
    def __init__(
        self,
        store: EventStore | None = None,
        *,
        clock: Callable[[], str] = _utcnow,
    ) -> None:
        self.store: EventStore = store if store is not None else MemoryEventStore()
        self.clock = clock
        self.state = GraphState()
        # MCP tools, FastAPI handlers and run watchers use worker threads.
        # Readers hold this while catching up on the log; writers take it
        # before the store lock (same order everywhere, so no deadlock).
        self._sync_lock = threading.RLock()
        self._sync()

    # ------------------------------------------------------------------
    # sync / append
    # ------------------------------------------------------------------

    def _sync(self) -> None:
        with self._sync_lock:
            for ev in self.store.read_since(self.state.seq):
                if ev.seq > self.state.seq:
                    apply_event(self.state, ev)

    def _commit(self, draft: GraphState, events: list[Event]) -> None:
        self.store.append(events)
        self.state = draft

    def _event(
        self,
        draft: GraphState,
        actor: Actor,
        type_: str,
        payload: dict,
        *,
        task_ids: Sequence[str] = (),
        changeset_id: str | None = None,
        reason: str | None = None,
        summary: str = "",
    ) -> Event:
        ev = Event(
            seq=draft.seq + 1,
            ts=self.clock(),
            actor=actor,
            type=type_,
            payload=payload,
            task_ids=list(task_ids),
            changeset_id=changeset_id,
            reason=reason,
            summary=summary,
        )
        apply_event(draft, ev)
        return ev

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def graph(self) -> dict[str, Any]:
        """Everything the overview / timeline views need in one call."""
        self._sync()
        s = self.state
        return {
            "project": s.settings.dump(),
            "seq": s.seq,
            "tasks": [t.dump() for t in s.tasks.values()],
            "edges": [e.dump() for e in s.edges()],
            "pendingDecisions": [
                t.id for t in s.tasks.values() if t.decision and t.decision.state == "pending"
            ],
            "pendingChangesets": [
                c.id for c in s.changesets.values() if c.status == "proposed"
            ],
            "activeRuns": [r.dump() for r in s.runs.values() if r.active],
        }

    def task(self, task_id: str) -> dict[str, Any]:
        self._sync()
        task = self._get(self.state, task_id)
        data = task.dump()
        data["activity"] = [a.dump() for a in self.state.activity(task_id)]
        data["blocks"] = [t.id for t in self.state.tasks.values() if task_id in t.blocked_by]
        data["children"] = [t.id for t in self.state.tasks.values() if t.parent_id == task_id]
        run = self.state.active_run(task_id)
        data["activeRun"] = run.dump() if run else None
        return data

    def activity(
        self, task_id: str | None = None, *, since: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._sync()
        if task_id is not None:
            self._get(self.state, task_id)
        items = [a for a in self.state.activity(task_id) if int(a.id[3:]) > since]
        return [a.dump() for a in items[-limit:]]

    def ready(self) -> list[dict[str, Any]]:
        """Open tasks whose blockers are all closed, excluding decisions that
        are waiting on a pending question (those are waiting on a human)."""
        self._sync()
        out = []
        for t in self.state.tasks.values():
            if t.status != "open":
                continue
            if any(self.state.tasks[b].status != "closed" for b in t.blocked_by):
                continue
            if t.decision and t.decision.state == "pending":
                continue
            out.append(t.dump())
        return out

    def blocked(self) -> list[dict[str, Any]]:
        """Unfinished tasks with at least one blocker that is not closed."""
        self._sync()
        out = []
        for t in self.state.tasks.values():
            if t.status == "closed":
                continue
            waiting = [
                {"id": b, "status": self.state.tasks[b].status}
                for b in t.blocked_by
                if self.state.tasks[b].status != "closed"
            ]
            if waiting:
                out.append({"id": t.id, "title": t.title, "waitingOn": waiting})
        return out

    def decisions(self, state: str | None = None) -> list[dict[str, Any]]:
        """The decision log: questions, choices and rationale."""
        self._sync()
        out = []
        for t in self.state.tasks.values():
            if t.decision and (state is None or t.decision.state == state):
                out.append({"taskId": t.id, "title": t.title, **t.decision.dump()})
        return out

    def changesets(self, status: ChangesetStatus | None = None) -> list[dict[str, Any]]:
        self._sync()
        return [
            c.dump()
            for c in self.state.changesets.values()
            if status is None or c.status == status
        ]

    def events_since(self, seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        self._sync()
        return [e.dump() for e in self.state.events if e.seq > seq][:limit]

    def runs(
        self, *, task_id: str | None = None, status: RunStatus | None = None, active: bool | None = None
    ) -> list[AgentRun]:
        """Agent runs, oldest first. `active=True` lists the agents working now."""
        self._sync()
        return [
            r.model_copy(deep=True)
            for r in self.state.runs.values()
            if (task_id is None or r.task_id == task_id)
            and (status is None or r.status == status)
            and (active is None or r.active == active)
        ]

    def run(self, run_id: str) -> AgentRun:
        self._sync()
        return self._get_run(self.state, run_id).model_copy(deep=True)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def submit(
        self,
        actor: Actor,
        ops: Sequence[Op],
        reason: str | None = None,
        *,
        base_seq: int | None = None,
    ) -> SubmitResult:
        """Validate and apply (or propose) a batch of ops atomically."""
        if not ops:
            raise TaskGraphError("invalid", "no operations given")
        if actor.type == "agent" and not (reason and reason.strip()):
            raise TaskGraphError("invalid", "agents must give a reason for every change")

        with self._sync_lock, self.store.locked():
            self._sync()
            if base_seq is not None and base_seq != self.state.seq:
                raise TaskGraphError(
                    "conflict",
                    f"graph changed since seq {base_seq} (now {self.state.seq}); re-read and retry",
                )

            # Dry run on a draft: validates every op and fixes generated ids.
            check = self.state.copy()
            resolved, created = self._validate_batch(check, actor, ops)

            draft = self.state.copy()
            cs_id = f"cs-{len(draft.changesets) + 1}"
            needs_review = self._needs_review(actor, resolved, draft.settings.autonomy)
            cs = Changeset(
                id=cs_id,
                actor=actor,
                reason=reason,
                ops=resolved,
                status="proposed" if needs_review else "applied",
                base_seq=draft.seq,
                created_at=self.clock(),
            )
            touched = _touched(resolved)
            events: list[Event] = []
            if needs_review:
                events.append(
                    self._event(
                        draft, actor, "changeset.proposed", cs.dump(),
                        task_ids=[t for t in touched if t in draft.tasks],
                        changeset_id=cs_id, reason=reason,
                        summary=f"{actor.label()} proposed {len(resolved)} change(s) for review",
                    )
                )
            else:
                events.append(
                    self._event(
                        draft, actor, "changeset.applied",
                        {"changesetId": cs_id, "changeset": cs.dump()},
                        changeset_id=cs_id, reason=reason,
                    )
                )
                events.extend(self._apply_ops(draft, actor, resolved, cs_id, reason))
            self._commit(draft, events)
            return SubmitResult(
                changeset_id=cs_id,
                status=cs.status,
                seq=draft.seq,
                created_ids=created,
                events=events,
            )

    def review(
        self, actor: Actor, changeset_id: str, approve: bool, note: str | None = None
    ) -> SubmitResult:
        """Approve (apply) or reject a proposed changeset. Humans only."""
        if actor.type != "human":
            raise TaskGraphError("forbidden", "only humans can review changesets")
        with self._sync_lock, self.store.locked():
            self._sync()
            cs = self.state.changesets.get(changeset_id)
            if cs is None:
                raise TaskGraphError("not_found", f"changeset '{changeset_id}' not found")
            if cs.status != "proposed":
                raise TaskGraphError("conflict", f"changeset '{changeset_id}' is already {cs.status}")
            draft = self.state.copy()
            events: list[Event] = []
            if approve:
                # Re-validate: the graph may have moved on since the proposal.
                check = self.state.copy()
                self._validate_batch(check, cs.actor, cs.ops)
                events.append(
                    self._event(
                        draft, actor, "changeset.approved",
                        {"changesetId": cs.id, "note": note},
                        task_ids=[t for t in _touched(cs.ops) if t in draft.tasks],
                        changeset_id=cs.id, reason=note,
                        summary=f"{actor.label()} approved changes from {cs.actor.label()}",
                    )
                )
                events.extend(self._apply_ops(draft, cs.actor, cs.ops, cs.id, cs.reason))
            else:
                events.append(
                    self._event(
                        draft, actor, "changeset.rejected",
                        {"changesetId": cs.id, "note": note},
                        task_ids=[t for t in _touched(cs.ops) if t in draft.tasks],
                        changeset_id=cs.id, reason=note,
                        summary=f"{actor.label()} rejected changes from {cs.actor.label()}",
                    )
                )
            self._commit(draft, events)
            return SubmitResult(
                changeset_id=cs.id,
                status="applied" if approve else "rejected",
                seq=draft.seq,
                events=events,
            )

    def update_settings(
        self, actor: Actor, *, name: str | None = None, autonomy: Autonomy | None = None
    ) -> dict[str, Any]:
        """Rename the project or flip the autonomy switch. Humans only."""
        if actor.type != "human":
            raise TaskGraphError("forbidden", "only humans can change project settings")
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if autonomy is not None:
            changes["autonomy"] = autonomy
        if not changes:
            raise TaskGraphError("invalid", "nothing to change")
        ProjectSettings.model_validate(changes)
        with self._sync_lock, self.store.locked():
            self._sync()
            draft = self.state.copy()
            parts = [f"{k} → {v}" for k, v in changes.items()]
            ev = self._event(
                draft, actor, "project.settings", changes,
                summary=f"{actor.label()} set {', '.join(parts)}",
            )
            self._commit(draft, [ev])
            return draft.settings.dump()

    # agent runs ---------------------------------------------------------

    def start_run(
        self,
        actor: Actor,
        task_id: str,
        *,
        agent: Actor,
        reason: str | None = None,
        runner: str = "record",
        instructions: str | None = None,
        restart_of: str | None = None,
    ) -> AgentRun:
        """Record that `agent` starts working on `task_id`.

        The task must be startable (exists, not closed, blockers closed, no
        pending decision, no other active run). In the same write the task
        moves to 'running' and is assigned to `agent`. Under autonomy
        'ask-all' only humans may start runs.
        """
        if actor.type == "agent" and not (reason and reason.strip()):
            raise TaskGraphError("invalid", "agents must give a reason for every change")
        with self._sync_lock, self.store.locked():
            self._sync()
            s = self.state
            if actor.type == "agent" and s.settings.autonomy == "ask-all":
                raise TaskGraphError(
                    "forbidden", "autonomy is 'ask-all': a human has to start agent runs"
                )
            task = self._get(s, task_id)
            if task.status == "closed":
                raise TaskGraphError(
                    "invalid", f"'{task_id}' is closed; reopen it (set_status open) first"
                )
            if task.decision and task.decision.state == "pending":
                raise TaskGraphError("invalid", f"'{task_id}' is waiting on a pending decision")
            self._require_unblocked(s, task, "running")
            current = s.active_run(task_id)
            if current is not None:
                raise TaskGraphError(
                    "conflict", f"'{task_id}' already has an active run '{current.id}'"
                )

            draft = s.copy()
            attempt = 1 + sum(1 for r in draft.runs.values() if r.task_id == task_id)
            run = AgentRun(
                id=f"run-{len(draft.runs) + 1}",
                task_id=task_id,
                agent=agent,
                runner=runner,
                attempt=attempt,
                restart_of=restart_of,
                instructions=instructions,
                started_by=actor,
                started_at=self.clock(),
                reason=reason,
            )
            verb = "restarted" if restart_of else "started"
            events = [
                self._event(
                    draft, actor, "run.started", run.dump(),
                    task_ids=[task_id], reason=reason,
                    summary=f"{actor.label()} {verb} {agent.label()} ({run.id}, attempt {attempt})",
                )
            ]
            follow: list[Op] = []
            if task.status != "running":
                follow.append(SetStatus(task_id=task_id, status="running"))
            if task.assignee != agent:
                follow.append(AssignTask(task_id=task_id, assignee=agent))
            events.extend(self._apply_ops(draft, actor, follow, None, reason))
            self._commit(draft, events)
            return draft.runs[run.id].model_copy(deep=True)

    def update_run(self, run_id: str, **meta: Any) -> AgentRun:
        """Attach runner details (pid, host, log_path) to a run."""
        names = {"pid": "pid", "host": "host", "log_path": "logPath"}
        payload = {names[k]: v for k, v in meta.items() if k in names and v is not None}
        with self._sync_lock, self.store.locked():
            self._sync()
            run = self._get_run(self.state, run_id)
            if not payload:
                return run.model_copy(deep=True)
            draft = self.state.copy()
            ev = self._event(draft, run.agent, "run.updated", {"runId": run_id, **payload})
            self._commit(draft, [ev])
            return draft.runs[run_id].model_copy(deep=True)

    def end_run(
        self,
        actor: Actor,
        run_id: str,
        status: RunStatus,
        *,
        exit_code: int | None = None,
        note: str | None = None,
        reason: str | None = None,
    ) -> AgentRun:
        """Record that a run ended. Ending a run that already ended is a no-op.

        If the task is still 'running' it moves on: to 'in-review' when the run
        succeeded, back to 'open' otherwise (stopped / failed / lost).
        """
        if status == "running":
            raise TaskGraphError("invalid", "end_run needs a final status")
        with self._sync_lock, self.store.locked():
            self._sync()
            run = self._get_run(self.state, run_id)
            if not run.active:
                return run.model_copy(deep=True)
            draft = self.state.copy()
            detail = f" (exit {exit_code})" if exit_code is not None else ""
            events = [
                self._event(
                    draft, actor, "run.ended",
                    {"runId": run_id, "status": status, "exitCode": exit_code, "note": note},
                    task_ids=[run.task_id] if run.task_id in draft.tasks else [],
                    reason=reason or note,
                    summary=f"{run.agent.label()} {status}{detail} ({run_id})",
                )
            ]
            task = draft.tasks.get(run.task_id)
            if task is not None and task.status == "running":
                nxt = "in-review" if status == "succeeded" else "open"
                events.extend(
                    self._apply_ops(
                        draft, actor, [SetStatus(task_id=task.id, status=nxt)], None,
                        reason or note or f"run {run_id} {status}",
                    )
                )
            self._commit(draft, events)
            return draft.runs[run_id].model_copy(deep=True)

    # convenience wrappers (each is a one-op changeset) ------------------

    def create_task(self, actor: Actor, reason: str | None = None, **fields: Any) -> SubmitResult:
        from .models import CreateTask

        return self.submit(actor, [CreateTask(**fields)], reason)

    def set_status(self, actor: Actor, task_id: str, status: str, reason: str | None = None) -> SubmitResult:
        from .models import SetStatus

        return self.submit(actor, [SetStatus(task_id=task_id, status=status)], reason)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _needs_review(actor: Actor, ops: Sequence[Op], autonomy: Autonomy) -> bool:
        if actor.type == "human" or autonomy == "auto":
            return False
        if autonomy == "ask-all":
            return True
        return any(op.op in STRUCTURAL_OPS for op in ops)

    def _apply_ops(
        self, draft: GraphState, actor: Actor, ops: Sequence[Op], cs_id: str | None, reason: str | None
    ) -> list[Event]:
        events = []
        for op in ops:
            summary = _summarize(draft, actor, op)
            events.append(
                self._event(
                    draft, actor, op.op, dump_op(op, only_set=op.op == "task.update"),
                    task_ids=_touched([op]), changeset_id=cs_id, reason=reason,
                    summary=summary,
                )
            )
        return events

    def _validate_batch(
        self, draft: GraphState, actor: Actor, ops: Sequence[Op]
    ) -> tuple[list[Op], list[str]]:
        """Validate ops in order against `draft`, applying each so later ops
        can reference earlier ones. Returns ops with generated ids filled in."""
        resolved: list[Op] = []
        created: list[str] = []
        for i, op in enumerate(ops):
            try:
                op = self._validate_op(draft, op)
            except TaskGraphError as e:
                raise TaskGraphError(e.code, e.message, op_index=i) from None
            if op.op == "task.create":
                created.append(op.id)  # type: ignore[arg-type]
            resolved.append(op)
            self._event(draft, actor, op.op, dump_op(op, only_set=op.op == "task.update"))
        return resolved, created

    @staticmethod
    def _get(state: GraphState, task_id: str) -> Task:
        task = state.tasks.get(task_id)
        if task is None:
            raise TaskGraphError("not_found", f"task '{task_id}' not found")
        return task

    @staticmethod
    def _get_run(state: GraphState, run_id: str) -> AgentRun:
        run = state.runs.get(run_id)
        if run is None:
            raise TaskGraphError("not_found", f"run '{run_id}' not found")
        return run

    def _require_unblocked(self, state: GraphState, task: Task, status: str) -> None:
        if status not in WORK_STATUSES:
            return
        open_blockers = [b for b in task.blocked_by if state.tasks[b].status != "closed"]
        if open_blockers:
            raise TaskGraphError(
                "blocked",
                f"cannot move '{task.id}' to {status}: blocked by "
                + ", ".join(f"'{b}' ({state.tasks[b].status})" for b in open_blockers),
            )

    def _validate_op(self, s: GraphState, op: Op) -> Op:
        get = lambda tid: self._get(s, tid)  # noqa: E731

        if op.op == "task.create":
            if op.id is None:
                base, n = _slug(op.title), 2
                new_id = base
                while new_id in s.tasks:
                    new_id, n = f"{base}-{n}", n + 1
                op = op.model_copy(update={"id": new_id})
            elif op.id in s.tasks:
                raise TaskGraphError("conflict", f"task id '{op.id}' already exists")
            for ref in [*op.blocked_by, *op.related_to]:
                if ref == op.id:
                    raise TaskGraphError("invalid", "a task cannot relate to itself")
                get(ref)
            if op.parent_id is not None:
                get(op.parent_id)
            _check_date_order(op.start_date, op.end_date)
            probe = Task(id=op.id, title=op.title, blocked_by=op.blocked_by)  # type: ignore[arg-type]
            self._require_unblocked(s, probe, op.status)
            return op

        if op.op in ("relation.add", "relation.remove"):
            src, dst = get(op.source), get(op.target)
            if src.id == dst.id:
                raise TaskGraphError("invalid", "a task cannot relate to itself")
            exists = (
                src.id in dst.blocked_by if op.type == "blocks" else dst.id in src.related_to
            )
            if op.op == "relation.add":
                if exists:
                    raise TaskGraphError("conflict", f"relation {src.id} -{op.type}-> {dst.id} already exists")
                if op.type == "blocks":
                    if s.blocks_path(src.id, dst.id):
                        raise TaskGraphError(
                            "cycle", f"'{src.id}' already waits on '{dst.id}'; this would create a cycle"
                        )
                    if dst.status in WORK_STATUSES and src.status != "closed":
                        raise TaskGraphError(
                            "blocked",
                            f"'{dst.id}' is already {dst.status}; it can only gain a closed blocker",
                        )
            elif not exists:
                raise TaskGraphError("not_found", f"relation {src.id} -{op.type}-> {dst.id} does not exist")
            return op

        task = get(op.task_id)

        if op.op == "task.update":
            fields = op.model_fields_set - {"op", "task_id"}
            if not fields:
                raise TaskGraphError("invalid", "no fields to update")
            for name in fields & {"title", "kind"}:
                if getattr(op, name) is None:
                    raise TaskGraphError("invalid", f"'{name}' cannot be null")
            if "parent_id" in fields and op.parent_id is not None:
                node = get(op.parent_id)
                while node is not None:
                    if node.id == task.id:
                        raise TaskGraphError("cycle", "a task cannot be nested inside itself")
                    node = s.tasks.get(node.parent_id) if node.parent_id else None
            start = op.start_date if "start_date" in fields else task.start_date
            end = op.end_date if "end_date" in fields else task.end_date
            _check_date_order(start, end)
            return op

        if op.op == "task.set_status":
            self._require_unblocked(s, task, op.status)
            if op.status == "closed" and task.decision and task.decision.state == "pending":
                raise TaskGraphError(
                    "invalid", f"'{task.id}' has a pending decision; use decision.resolve instead"
                )
            return op

        if op.op == "decision.request":
            if task.decision and task.decision.state == "pending":
                raise TaskGraphError("conflict", f"'{task.id}' already has a pending decision")
            return op

        if op.op == "decision.resolve":
            if not task.decision or task.decision.state != "pending":
                raise TaskGraphError("invalid", f"'{task.id}' has no pending decision")
            return op

        if op.op == "task.delete":
            run = s.active_run(task.id)
            if run is not None:
                raise TaskGraphError(
                    "conflict", f"'{task.id}' has an active run '{run.id}'; stop it first"
                )
            return op

        # task.assign, comment.add: existence check is enough
        return op


def _check_date_order(start: str | None, end: str | None) -> None:
    if start and end and end < start:
        raise TaskGraphError("invalid", f"endDate {end} is before startDate {start}")


def _touched(ops: Sequence[Op]) -> list[str]:
    ids: list[str] = []
    for op in ops:
        if op.op == "task.create":
            cand = [op.id]
        elif op.op in ("relation.add", "relation.remove"):
            cand = [op.source, op.target]
        else:
            cand = [op.task_id]
        for c in cand:
            if c and c not in ids:
                ids.append(c)
    return ids


def _summarize(s: GraphState, actor: Actor, op: Op) -> str:
    who = actor.label()
    if op.op == "task.create":
        return f"{who} created '{op.title}'"
    if op.op == "task.update":
        fields = sorted(op.model_dump(by_alias=True, exclude_unset=True).keys() - {"op", "taskId"})
        return f"{who} updated {', '.join(fields)}"
    if op.op == "task.set_status":
        return f"{who} moved to {op.status}"
    if op.op == "task.assign":
        return f"assigned to {op.assignee.label()}" if op.assignee else f"{who} unassigned"
    if op.op == "task.delete":
        title = s.tasks[op.task_id].title if op.task_id in s.tasks else op.task_id
        return f"{who} deleted '{title}'"
    if op.op == "relation.add":
        verb = "blocks" if op.type == "blocks" else "is related to"
        return f"{who} linked: '{op.source}' {verb} '{op.target}'"
    if op.op == "relation.remove":
        return f"{who} removed link {op.source} → {op.target} ({op.type})"
    if op.op == "comment.add":
        return f"{who}: {op.body}"
    if op.op == "decision.request":
        return f"{who} asked: {op.question}"
    if op.op == "decision.resolve":
        return f"{who} decided '{op.chosen}' — {op.rationale}"
    return f"{who} {op.op}"
