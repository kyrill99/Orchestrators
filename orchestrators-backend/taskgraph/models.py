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


class Artifact(Model):
    path: str
    label: str | None = None


class DecisionInfo(Model):
    """Attached to tasks of kind 'decision'.

    Agents should phrase `question` in terms of strategy (goals, priorities,
    success criteria), not as an implementation A-vs-B choice.
    """

    question: str
    options: list[str] = Field(default_factory=list)
    context: str | None = None
    state: Literal["pending", "resolved"] = "pending"
    chosen: str | None = None
    rationale: str | None = None
    resolved_by: Actor | None = None
    resolved_at: str | None = None


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
    """Attach a strategy question to a task (usually kind='decision')."""

    op: Literal["decision.request"] = "decision.request"
    task_id: str
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    context: str | None = None


class ResolveDecision(Model):
    op: Literal["decision.resolve"] = "decision.resolve"
    task_id: str
    chosen: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


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


class ProjectSettings(Model):
    name: str = "Untitled project"
    autonomy: Autonomy = "auto"


class SubmitResult(Model):
    changeset_id: str
    status: ChangesetStatus
    seq: int
    created_ids: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
