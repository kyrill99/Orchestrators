"""REST adapter for the GUI and the conversation code. Mount with:

    app.include_router(build_router(memory, taskgraph))
"""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from identity import current_user
from taskgraph.models import Actor, Model
from taskgraph.service import TaskGraphService

from .models import ChatMessage, MemoryKind, MemorySource, MemoryStatus, Scope
from .service import DEFAULT_ID, MemoryService, MemoryServiceError, user_key

STATUS_FOR_CODE = {"not_found": 404, "unavailable": 503}


class RememberBody(Model):
    actor: Actor
    title: str = Field(min_length=1)
    body: str
    kind: MemoryKind
    scope: Scope = "project"
    project_id: str | None = None
    user_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: MemorySource | None = None


class UpdateBody(Model):
    actor: Actor
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: MemoryStatus | None = None


class FeedbackBody(Model):
    actor: Actor
    helpful: bool
    note: str | None = None


class ReflectBody(Model):
    actor: Actor
    messages: list[ChatMessage]
    project_id: str | None = None  # default: the active project
    user_id: str = DEFAULT_ID
    ref: str | None = None


def _dump(out):
    if isinstance(out, list):
        return [_dump(item) for item in out]
    return out.dump() if hasattr(out, "dump") else out


def build_router(service: MemoryService, taskgraph: TaskGraphService | None = None) -> APIRouter:
    r = APIRouter(prefix="/api/memory", tags=["memory"])

    def call(fn, *a, **kw):
        try:
            out = fn(*a, **kw)
        except MemoryServiceError as e:
            raise HTTPException(STATUS_FOR_CODE.get(e.code, 422), e.to_dict()) from None
        return _dump(out)

    @r.get("/tree")
    def tree(request: Request, projectId: str | None = None):
        """Folders and files for the Memory page: the active project's memories
        (or `projectId`'s), the signed-in user's, and the ones shared by all projects."""
        user = os.environ.get("MEMORY_USER_ID") or user_key(current_user(request).email)
        return call(service.tree, projectId, user)

    @r.get("/stats")
    def stats():
        return call(service.stats)

    @r.get("/log")
    def log(limit: int = 100):
        return call(service.log, limit)

    @r.get("/context")
    def context(projectId: str | None = None, userId: str = DEFAULT_ID, q: str | None = None,
                limit: int = 5):
        """Prompt block for an agent session. Counts as a use of each memory in it."""
        return call(service.context, project_id=projectId, user_id=userId, query=q, limit=limit)

    @r.get("/memories")
    def memories(scope: Scope | None = None, projectId: str | None = None,
                 userId: str | None = None, kind: MemoryKind | None = None,
                 status: MemoryStatus | None = "active", q: str | None = None):
        return call(service.list_memories, scope=scope, project_id=projectId, user_id=userId,
                    kind=kind, status=status, q=q)

    @r.get("/memories/{memory_id}")
    def memory(memory_id: str):
        return call(service.get, memory_id)

    @r.post("/memories")
    def remember(body: RememberBody):
        return call(service.remember, body.actor, title=body.title, body=body.body,
                    kind=body.kind, scope=body.scope, project_id=body.project_id,
                    user_id=body.user_id, tags=body.tags, source=body.source)

    @r.patch("/memories/{memory_id}")
    def update(memory_id: str, body: UpdateBody):
        return call(service.update, body.actor, memory_id, title=body.title, body=body.body,
                    tags=body.tags, confidence=body.confidence, status=body.status)

    @r.delete("/memories/{memory_id}")
    def archive(memory_id: str, actorType: Literal["human", "agent"] = "human", actorId: str = DEFAULT_ID):
        """Archive (files are kept, the memory is no longer used)."""
        return call(service.archive, Actor(type=actorType, id=actorId), memory_id)

    @r.post("/memories/{memory_id}/feedback")
    def feedback(memory_id: str, body: FeedbackBody):
        return call(service.feedback, body.actor, memory_id, body.helpful, body.note)

    @r.post("/reflect")
    def reflect(body: ReflectBody):
        """Distill a finished conversation into memory. Call when a chat ends."""
        return call(service.reflect, body.actor, body.messages, project_id=body.project_id,
                    user_id=body.user_id, ref=body.ref)

    if taskgraph is not None:

        @r.post("/sync")
        def sync(projectId: str | None = None):
            """Pull resolved decisions and rejected changes from the active
            project's task graph into that project's memory."""
            return call(service.sync_from_taskgraph, taskgraph, project_id=projectId)

    return r
