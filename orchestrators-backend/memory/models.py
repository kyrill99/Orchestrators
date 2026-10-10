"""Data model for memory.

A memory is one markdown file: frontmatter (the fields below) plus a body.
JSON uses camelCase like the task graph, so the frontend can consume it as is.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from taskgraph.models import Actor, Model

Scope = Literal["project", "user", "global"]
MemoryKind = Literal["strategy", "preference", "decision", "learning", "note"]
MemoryStatus = Literal["active", "superseded", "archived"]
SourceType = Literal["conversation", "decision", "changeset", "manual"]

# Narrower scopes win when memories conflict.
SCOPE_ORDER: tuple[Scope, ...] = ("project", "user", "global")
# Always part of the agent's context; the other kinds are retrieved by query.
CORE_KINDS = frozenset({"strategy", "preference"})

DEFAULT_CONFIDENCE = 0.5
ARCHIVE_BELOW = 0.2
HELPFUL_STEP = 0.1
UNHELPFUL_STEP = 0.2

ID_PART = r"^[A-Za-z0-9_-]+$"


class MemorySource(Model):
    """Where a memory came from; `ref` is a task id, changeset id or chat id."""

    type: SourceType = "manual"
    ref: str | None = None


class Memory(Model):
    id: str
    scope: Scope
    project_id: str | None = None
    user_id: str | None = None
    kind: MemoryKind
    title: str
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = Field(default_factory=MemorySource)
    confidence: float = Field(default=DEFAULT_CONFIDENCE, ge=0.0, le=1.0)
    uses: int = 0
    helpful: int = 0
    unhelpful: int = 0
    status: MemoryStatus = "active"
    superseded_by: str | None = None
    created_by: Actor | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def path(self) -> str:
        """File path relative to the memory root; the id is the path with dots."""
        return self.id.replace(".", "/") + ".md"


class ReflectOp(Model):
    """One change proposed by the distiller after reading a conversation.

    create:    new memory from scope/kind/title/body.
    update:    replace title/body/tags of `target_id`.
    supersede: `target_id` is no longer true; store the new memory instead.
    reinforce: the conversation confirmed `target_id`.
    """

    op: Literal["create", "update", "supersede", "reinforce"]
    target_id: str | None = None
    scope: Scope | None = None
    kind: MemoryKind | None = None
    title: str | None = None
    body: str | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None


class ChatMessage(Model):
    role: str
    content: str
