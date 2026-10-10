"""Graph state and the event reducer.

The reducer trusts its input: events are validated by `TaskGraphService` before
they are written, so replaying a log never fails half-way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import TypeAdapter

from .models import (
    ActivityItem,
    AgentRun,
    Changeset,
    DecisionInfo,
    DecisionRecord,
    Edge,
    Event,
    Op,
    ProjectSettings,
    Task,
    blocker_done,
)

OP_ADAPTER: TypeAdapter[Op] = TypeAdapter(Op)


def parse_op(payload: dict) -> Op:
    return OP_ADAPTER.validate_python(payload)


def dump_op(op: Op, *, only_set: bool = False) -> dict:
    data = op.model_dump(mode="json", by_alias=True, exclude_unset=only_set)
    data["op"] = op.op
    return data


@dataclass
class GraphState:
    seq: int = 0
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    tasks: dict[str, Task] = field(default_factory=dict)
    changesets: dict[str, Changeset] = field(default_factory=dict)
    runs: dict[str, AgentRun] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    def copy(self) -> "GraphState":
        return GraphState(
            seq=self.seq,
            settings=self.settings.model_copy(deep=True),
            tasks={k: v.model_copy(deep=True) for k, v in self.tasks.items()},
            changesets={k: v.model_copy(deep=True) for k, v in self.changesets.items()},
            runs={k: v.model_copy(deep=True) for k, v in self.runs.items()},
            events=list(self.events),
        )

    # ---- derived views -------------------------------------------------

    def active_run(self, task_id: str) -> AgentRun | None:
        """The run currently working on `task_id` (at most one)."""
        for run in self.runs.values():
            if run.task_id == task_id and run.active:
                return run
        return None

    def edges(self) -> list[Edge]:
        out: list[Edge] = []
        for task in self.tasks.values():
            for src in task.blocked_by:
                out.append(Edge(id=f"e-{src}-{task.id}", source=src, target=task.id))
        return out

    def activity(self, task_id: str | None = None) -> list[ActivityItem]:
        items: list[ActivityItem] = []
        for ev in self.events:
            if not ev.summary:
                continue
            if task_id is not None and task_id not in ev.task_ids:
                continue
            items.append(
                ActivityItem(
                    id=f"ev-{ev.seq}",
                    text=ev.summary,
                    at=ev.ts,
                    actor=ev.actor,
                    op=ev.type,
                    reason=ev.reason,
                )
            )
        return items

    def blocks_path(self, start: str, goal: str) -> bool:
        """True if `goal` is reachable from `start` following blocked_by links,
        i.e. `start` (transitively) waits on `goal`."""
        stack, seen = [start], set()
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            if node in seen or node not in self.tasks:
                continue
            seen.add(node)
            stack.extend(self.tasks[node].blocked_by)
        return False


def apply_event(state: GraphState, ev: Event) -> None:
    """Mutate `state` by one event."""
    t = ev.type
    p = ev.payload

    if t == "project.settings":
        state.settings = state.settings.model_copy(
            update=ProjectSettings.model_validate(p).model_dump(exclude_unset=True)
        )
    elif t == "changeset.proposed":
        cs = Changeset.model_validate(p)
        state.changesets[cs.id] = cs
    elif t in ("changeset.approved", "changeset.rejected", "changeset.applied"):
        cs_id = p["changesetId"]
        if t == "changeset.applied":
            cs = Changeset.model_validate(p["changeset"])
            state.changesets[cs.id] = cs
        else:
            cs = state.changesets[cs_id]
            cs.status = "applied" if t == "changeset.approved" else "rejected"
            cs.reviewed_by = ev.actor
            cs.reviewed_at = ev.ts
            cs.review_note = p.get("note")
    elif t == "run.started":
        run = AgentRun.model_validate(p)
        state.runs[run.id] = run
    elif t == "run.updated":
        run = state.runs[p["runId"]]
        for key, attr in (("pid", "pid"), ("host", "host"), ("logPath", "log_path"), ("tokens", "tokens")):
            if key in p:
                setattr(run, attr, p[key])
    elif t == "run.ended":
        run = state.runs[p["runId"]]
        run.status = p["status"]
        run.exit_code = p.get("exitCode")
        run.note = p.get("note")
        run.failure_kind = p.get("failureKind")
        run.ended_at = ev.ts
        run.ended_by = ev.actor
    else:
        _apply_op(state, parse_op(p), ev)

    state.seq = ev.seq
    state.events.append(ev)


def _apply_op(state: GraphState, op: Op, ev: Event) -> None:
    tasks = state.tasks
    if op.op == "task.create":
        data = op.model_dump(exclude={"op"}, exclude_none=True)
        tasks[op.id] = Task(**data, created_at=ev.ts, updated_at=ev.ts)  # type: ignore[arg-type]
        return

    if op.op in ("relation.add", "relation.remove"):
        if op.type == "blocks":
            holder, other, attr = tasks[op.target], op.source, "blocked_by"
        else:
            holder, other, attr = tasks[op.source], op.target, "related_to"
        values: list[str] = getattr(holder, attr)
        if op.op == "relation.add" and other not in values:
            values.append(other)
        elif op.op == "relation.remove" and other in values:
            values.remove(other)
        holder.updated_at = ev.ts
        return

    task = tasks[op.task_id]
    if op.op == "task.update":
        for name in op.model_fields_set - {"op", "task_id"}:
            setattr(task, name, getattr(op, name))
    elif op.op == "task.set_status":
        task.status = op.status
        if op.status == "closed":
            task.progress = 1.0
    elif op.op == "task.assign":
        task.assignee = op.assignee
    elif op.op == "task.delete":
        del tasks[op.task_id]
        for other in tasks.values():
            if op.task_id in other.blocked_by:
                other.blocked_by.remove(op.task_id)
            if op.task_id in other.related_to:
                other.related_to.remove(op.task_id)
            if other.parent_id == op.task_id:
                other.parent_id = None
        return
    elif op.op == "comment.add":
        pass  # lives only in the event log / activity feed
    elif op.op == "decision.request":
        old = task.decision
        task.decision = DecisionInfo(
            question=op.question, options=op.options, context=op.context,
            recommended=op.recommended, recommendation=op.recommendation,
            requested_by=ev.actor, requested_at=ev.ts,
        )
        if old is not None and old.state == "pending":
            # Prepared again: keep the owner's input (it pauses auto-decide).
            task.decision.feedback = old.feedback
            task.decision.feedback_by = old.feedback_by
            task.decision.feedback_at = old.feedback_at
            task.decision.history = old.history
    elif op.op == "decision.feedback":
        assert task.decision is not None
        task.decision.feedback = op.note
        task.decision.feedback_by = ev.actor
        task.decision.feedback_at = ev.ts
    elif op.op == "decision.resolve":
        if task.decision is None:  # a decision task answered before it was prepared
            task.decision = DecisionInfo(question=task.description.strip() or task.title)
        d = task.decision
        if d.state == "resolved" and d.chosen:  # changed later: keep the earlier answer
            d.history.append(DecisionRecord(chosen=d.chosen, rationale=d.rationale,
                                            resolved_by=d.resolved_by, resolved_at=d.resolved_at,
                                            auto=d.auto))
        d.state = "resolved"
        d.chosen = op.chosen
        d.rationale = op.rationale
        d.resolved_by = ev.actor
        d.resolved_at = ev.ts
        d.auto = op.auto
        d.custom = bool(d.options) and op.chosen not in d.options
        if all(blocker_done(task, tasks[b]) for b in task.blocked_by if b in tasks):
            task.status = "closed"
            task.progress = 1.0
    task.updated_at = ev.ts
