"""Data model for the task graph.

JSON uses camelCase field names so the frontend (`lib/fixtures.ts`) can consume
the same shapes; Python code uses snake_case. Both spellings are accepted on input.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

TaskStatus = Literal["open", "running", "in-review", "closed"]
TaskKind = Literal["start", "task", "decision", "end"]
RelationType = Literal["blocks", "related"]
Autonomy = Literal["auto", "ask-structural", "ask-all"]
ChangesetStatus = Literal["applied", "proposed", "rejected"]
# running: work in progress. succeeded/failed: the run ended (exit code or a
# reported outcome). stopped: someone stopped it. lost: its process went away
# while nobody was watching, so the outcome is unknown.
RunStatus = Literal["running", "succeeded", "failed", "stopped", "lost"]
# Why a run failed, set by the runner where it ends the run: "api" (the model
# API kept failing), "infra" (the runner crashed, the checkout could not be
# prepared), "budget" (steps or time ran out), "agent" (the agent finished
# 'failed' itself or stopped without finishing), "checks" (its commits did not
# land: checks or rebase failed). The engine retries "api" and "infra" failures.
FailureKind = Literal["api", "infra", "budget", "agent", "checks"]
RETRYABLE_FAILURES = frozenset({"api", "infra"})
ACTIVE_RUN_STATUSES = frozenset({"running"})


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    def dump(self) -> dict[str, Any]:
        """JSON-ready dict with camelCase keys."""
        return self.model_dump(mode="json", by_alias=True)


class Actor(Model):
    type: Literal["human", "agent"]
    id: str = Field(min_length=1)

    def label(self) -> str:
        return self.id if self.type == "human" else f"agent {self.id}"


# The "Agent" option in the GUI's assignee picker (lib/assignees.ts): tasks
# agents create default to it. Whichever worker runs the task, it stays "Agent".
AGENT_ASSIGNEE = Actor(type="agent", id="agent")


class Artifact(Model):
    path: str
    label: str | None = None


class DecisionRecord(Model):
    """An earlier answer to a decision, kept when someone changes it."""

    chosen: str
    rationale: str | None = None
    resolved_by: Actor | None = None
    resolved_at: str | None = None
    auto: bool = False


class DecisionInfo(Model):
    """Attached to tasks of kind 'decision' (and to work tasks whose worker
    stopped on a strategy question).

    Agents should phrase `question` in terms of strategy (goals, priorities,
    success criteria), not as an implementation A-vs-B choice.

    A decision-prep run (engine.py) fills question, options, context and its
    recommendation (`recommended`, one of the options, with `recommendation`:
    the reasons). `auto` marks a decision the agent made by itself while the
    engine ran; `custom` an answer that is none of the options. `feedback` is
    the owner's latest input sent back to the preparation; `history` keeps
    earlier answers when a decision is changed.
    """

    question: str
    options: list[str] = Field(default_factory=list)
    context: str | None = None
    recommended: str | None = None
    recommendation: str | None = None
    requested_by: Actor | None = None
    requested_at: str | None = None
    feedback: str | None = None
    feedback_by: Actor | None = None
    feedback_at: str | None = None
    state: Literal["pending", "resolved"] = "pending"
    chosen: str | None = None
    rationale: str | None = None
    resolved_by: Actor | None = None
    resolved_at: str | None = None
    auto: bool = False
    custom: bool = False
    history: list[DecisionRecord] = Field(default_factory=list)


class Task(Model):
    id: str
    title: str
    kind: TaskKind = "task"
    status: TaskStatus = "open"
    description: str = ""
    parent_id: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    related_to: list[str] = Field(default_factory=list)
    assignee: Actor | None = None
    start_date: str | None = None
    end_date: str | None = None
    progress: float = 0.0
    artifacts: list[Artifact] = Field(default_factory=list)
    decision: DecisionInfo | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ActivityItem(Model):
    """One line of a task's activity feed (matches frontend TaskActivity)."""

    id: str
    text: str
    at: str
    actor: Actor
    op: str
    reason: str | None = None


class Edge(Model):
    """Derived view of `blocked_by`: source must finish before target."""

    id: str
    source: str
    target: str
    type: RelationType = "blocks"


# --------------------------------------------------------------------------
# Operations: the single write vocabulary shared by GUI and planner.
# --------------------------------------------------------------------------


def _check_iso_date(v: str | None) -> str | None:
    if v is not None:
        date.fromisoformat(v)
    return v


class TaskFields(Model):
    """Editable task fields (all optional; used by create and update)."""

    title: str | None = None
    kind: TaskKind | None = None
    description: str | None = None
    parent_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    artifacts: list[Artifact] | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _valid_dates(cls, v: str | None) -> str | None:
        return _check_iso_date(v)


class CreateTask(TaskFields):
    op: Literal["task.create"] = "task.create"
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=1)
    status: TaskStatus = "open"
    blocked_by: list[str] = Field(default_factory=list)
    related_to: list[str] = Field(default_factory=list)
    assignee: Actor | None = None


class UpdateTask(TaskFields):
    op: Literal["task.update"] = "task.update"
    task_id: str


class SetStatus(Model):
    op: Literal["task.set_status"] = "task.set_status"
    task_id: str
    status: TaskStatus


class AssignTask(Model):
    op: Literal["task.assign"] = "task.assign"
    task_id: str
    assignee: Actor | None  # None unassigns


class DeleteTask(Model):
    op: Literal["task.delete"] = "task.delete"
    task_id: str


class AddRelation(Model):
    """`blocks`: source blocks target (target.blockedBy gets source).
    `related`: symmetric-ish link stored on source.relatedTo."""

    op: Literal["relation.add"] = "relation.add"
    source: str
    target: str
    type: RelationType = "blocks"


class RemoveRelation(Model):
    op: Literal["relation.remove"] = "relation.remove"
    source: str
    target: str
    type: RelationType = "blocks"


class AddComment(Model):
    op: Literal["comment.add"] = "comment.add"
    task_id: str
    body: str = Field(min_length=1)


class RequestDecision(Model):
    """Attach a strategy question to a task (usually kind='decision').

    On a decision task it may replace a pending question (the decision is
    prepared again); `recommended` must be one of `options`."""

    op: Literal["decision.request"] = "decision.request"
    task_id: str
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    context: str | None = None
    recommended: str | None = None
    recommendation: str | None = None


class ResolveDecision(Model):
    """Answer a decision. On a decision task that is already resolved, this
    changes the answer (the earlier one goes to `history`). `auto`: the agent
    decided by itself (engine auto-decide)."""

    op: Literal["decision.resolve"] = "decision.resolve"
    task_id: str
    chosen: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    auto: bool = False


class DecisionFeedback(Model):
    """The owner's input on a pending decision without deciding ("add an
    option that combines A and B"): the decision is prepared again with it."""

    op: Literal["decision.feedback"] = "decision.feedback"
    task_id: str
    note: str = Field(min_length=1)


Op = Annotated[
    Union[
        CreateTask,
        UpdateTask,
        SetStatus,
        AssignTask,
        DeleteTask,
        AddRelation,
        RemoveRelation,
        AddComment,
        RequestDecision,
        ResolveDecision,
        DecisionFeedback,
    ],
    Field(discriminator="op"),
]

# Ops that change the shape of the graph. Under autonomy 'ask-structural',
# an agent changeset containing any of these waits for human approval.
STRUCTURAL_OPS = frozenset(
    {"task.create", "task.delete", "relation.add", "relation.remove", "decision.resolve"}
)


# --------------------------------------------------------------------------
# Events and changesets
# --------------------------------------------------------------------------


class Event(Model):
    """One entry in the append-only log. Graph state is a fold over events."""

    seq: int
    ts: str
    actor: Actor
    type: str  # an op name, or changeset.* / project.*
    payload: dict[str, Any] = Field(default_factory=dict)
    task_ids: list[str] = Field(default_factory=list)
    changeset_id: str | None = None
    reason: str | None = None
    summary: str = ""


class Changeset(Model):
    id: str
    actor: Actor
    reason: str | None = None
    ops: list[Op]
    status: ChangesetStatus
    base_seq: int
    created_at: str
    reviewed_by: Actor | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


class AgentRun(Model):
    """One attempt by an agent to work on a task (see runs.py).

    Recorded in the event log like everything else, so every process sharing
    the log sees the same runs. `runner` says what executes it: "record" means
    nothing is launched (an external worker reports back via finish_run),
    "subprocess" means a local process with `pid` on `host`.
    """

    id: str
    task_id: str
    agent: Actor
    status: RunStatus = "running"
    runner: str = "record"
    attempt: int = 1
    restart_of: str | None = None
    instructions: str | None = None
    started_by: Actor
    started_at: str
    reason: str | None = None
    ended_at: str | None = None
    ended_by: Actor | None = None
    exit_code: int | None = None
    note: str | None = None
    pid: int | None = None
    host: str | None = None
    log_path: str | None = None
    failure_kind: FailureKind | None = None
    tokens: int = 0  # model tokens used so far (reported by the runner, if known)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_RUN_STATUSES


DECISION_KIND = "decision"


def blocker_done(task: Task, blocker: Task) -> bool:
    """Has `blocker` finished as far as `task` is concerned?

    Closed always counts. For a decision task, in-review counts too: the work
    is finished from the agent's point of view, and the decision is in effect
    the owner's review of it (the blockers stay in review; nothing is approved
    automatically)."""
    if blocker.status == "closed":
        return True
    return task.kind == DECISION_KIND and blocker.status == "in-review"


class ProjectSettings(Model):
    name: str = "Untitled project"
    autonomy: Autonomy = "auto"


class SubmitResult(Model):
    changeset_id: str
    status: ChangesetStatus
    seq: int
    created_ids: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
