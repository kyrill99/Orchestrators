"""REST adapter for the GUI. Mount with:  app.include_router(build_router(service))

Not yet exercised against a running FastAPI in CI; the logic lives in
TaskGraphService, which is tested.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .models import Actor, Autonomy, Model, Op
from .service import TaskGraphError, TaskGraphService

STATUS_FOR_CODE = {"not_found": 404, "conflict": 409, "forbidden": 403}


class SubmitBody(Model):
    actor: Actor
    ops: list[Op]
    reason: str | None = None
    base_seq: int | None = None


class ReviewBody(Model):
    actor: Actor
    approve: bool
    note: str | None = None


class SettingsBody(Model):
    actor: Actor
    name: str | None = None
    autonomy: Autonomy | None = None


def build_router(service: TaskGraphService) -> APIRouter:
    r = APIRouter(prefix="/api/taskgraph", tags=["taskgraph"])

    def call(fn, *a, **kw):
        try:
            out = fn(*a, **kw)
        except TaskGraphError as e:
            raise HTTPException(STATUS_FOR_CODE.get(e.code, 422), e.to_dict()) from None
        return out.dump() if hasattr(out, "dump") else out

    @r.get("/graph")
    def graph():
        return call(service.graph)

    @r.get("/tasks/{task_id}")
    def task(task_id: str):
        return call(service.task, task_id)

    @r.get("/ready")
    def ready():
        return call(service.ready)

    @r.get("/blocked")
    def blocked():
        return call(service.blocked)

    @r.get("/decisions")
    def decisions(state: Literal["pending", "resolved"] | None = None):
        return call(service.decisions, state)

    @r.get("/activity")
    def activity(task_id: str | None = None, since: int = 0, limit: int = 100):
        return call(service.activity, task_id, since=since, limit=limit)

    @r.get("/events")
    def events(since: int = 0, limit: int = 500):
        """Poll for new events (GUI live updates)."""
        return call(service.events_since, since, limit)

    @r.get("/changesets")
    def changesets(status: Literal["applied", "proposed", "rejected"] | None = None):
        return call(service.changesets, status)

    @r.get("/runs")
    def runs(task_id: str | None = Query(None, alias="taskId"), active: bool | None = None):
        """Agent runs (who is working on what); started and stopped over MCP."""
        return [run.dump() for run in call(service.runs, task_id=task_id, active=active)]

    @r.post("/changes")
    def submit(body: SubmitBody):
        return call(service.submit, body.actor, body.ops, body.reason, base_seq=body.base_seq)

    @r.post("/changesets/{changeset_id}/review")
    def review(changeset_id: str, body: ReviewBody):
        return call(service.review, body.actor, changeset_id, body.approve, body.note)

    @r.patch("/settings")
    def settings(body: SettingsBody):
        return call(service.update_settings, body.actor, name=body.name, autonomy=body.autonomy)

    return r
