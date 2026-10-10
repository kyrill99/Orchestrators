"""REST adapter for the GUI. Mount with:  app.include_router(build_router(service))

Not yet exercised against a running FastAPI in CI; the logic lives in
TaskGraphService, which is tested.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .models import Actor, Autonomy, Model, Op
from .loop import auto_start, decide, review_task
from .runs import RunManager, runner_from_env
from .service import TaskGraphError, TaskGraphService

STATUS_FOR_CODE = {"not_found": 404, "conflict": 409, "forbidden": 403, "runner": 500}


class SubmitBody(Model):
    actor: Actor
    ops: list[Op]
    reason: str | None = None
    base_seq: int | None = None


class ReviewBody(Model):
    actor: Actor
    approve: bool
    note: str | None = None


class RunBody(Model):
    actor: Actor
    reason: str | None = None
    agent_id: str | None = None
    instructions: str | None = None


class StopBody(Model):
    actor: Actor
    reason: str | None = None


class TaskReviewBody(Model):
    actor: Actor
    approve: bool
    note: str | None = None


class DecideBody(Model):
    actor: Actor
    chosen: str
    rationale: str


class SettingsBody(Model):
    actor: Actor
    name: str | None = None
    autonomy: Autonomy | None = None


def build_router(service: TaskGraphService, runs: RunManager | None = None) -> APIRouter:
    """`runs` defaults to the runner configured by TASKGRAPH_RUNNER (see runs.py)."""
    r = APIRouter(prefix="/api/taskgraph", tags=["taskgraph"])
    manager = runs if runs is not None else RunManager(service, runner_from_env())

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

    @r.post("/tasks/{task_id}/run/start")
    def start_run(task_id: str, body: RunBody):
        """Put an agent to work on the task (same rules as the MCP start_task)."""
        return call(manager.start, body.actor, task_id, body.reason,
                    agent_id=body.agent_id or "worker", instructions=body.instructions)

    @r.post("/tasks/{task_id}/run/stop")
    def stop_run(task_id: str, body: StopBody):
        """Stop the task's active run; the task goes back to open."""
        active = call(service.runs, task_id=task_id, active=True)
        if not active:
            call(service.task, task_id)  # 404 for an unknown task
            raise HTTPException(409, {"code": "conflict", "message": f"'{task_id}' has no active run", "opIndex": None})
        return call(manager.stop, body.actor, active[0].id, body.reason)

    @r.post("/tasks/{task_id}/run/restart")
    def restart_run(task_id: str, body: RunBody):
        """Stop the active run (if any) and start the next attempt."""
        return call(manager.restart, body.actor, task_id, body.reason,
                    agent_id=body.agent_id, instructions=body.instructions)

    @r.post("/tasks/{task_id}/review")
    def review_finished_task(task_id: str, body: TaskReviewBody):
        """Owner approves an in-review task (-> closed, next tasks start) or sends it
        back (-> worker restarts with the note as feedback)."""
        return call(review_task, service, manager, body.actor, task_id, body.approve, body.note)

    @r.post("/tasks/{task_id}/decide")
    def decide_task(task_id: str, body: DecideBody):
        """Owner answers the task's pending decision; tasks it unblocks start."""
        return call(decide, service, manager, body.actor, task_id, body.chosen, body.rationale)

    @r.post("/autostart")
    def autostart(body: StopBody):
        """Start workers on every ready task now."""
        return {"started": call(auto_start, service, manager, body.actor)}

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
