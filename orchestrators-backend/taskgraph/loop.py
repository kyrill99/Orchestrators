"""Owner actions that close the loop: approve or send back a finished task,
answer a decision, and start whatever became ready.

    worker run succeeds -> task in-review -> owner approves (closed) or sends back
    (worker restarts with the feedback) -> newly unblocked tasks start
    automatically, unless the project's autonomy is 'ask-all'.

With an `engine` (taskgraph/engine.py, what the API server passes), starting
goes through Engine.dispatch: the same queue rules and ENGINE_MAX_RUNS limit
as the engine's own loop, so owner actions and the engine never disagree.
Without one, every ready task starts (the original behaviour).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Actor, AddComment, ResolveDecision, SetStatus
from .runs import RunManager
from .service import TaskGraphError, TaskGraphService

if TYPE_CHECKING:
    from .engine import Engine

AUTO_START_KINDS = ("task",)


def close_ready_milestones(service: TaskGraphService, actor: Actor) -> bool:
    """Close 'start' / 'end' milestones whose blockers are done. True if any closed."""
    closed = False
    for _ in range(3):  # closing a milestone can unblock more milestones
        ready = [t for t in service.ready() if t["kind"] in ("start", "end")]
        if not ready:
            break
        for task in ready:
            service.submit(actor, [SetStatus(task_id=task["id"], status="closed")],
                           reason="milestone reached")
            closed = True
    return closed


def auto_start(
    service: TaskGraphService, manager: RunManager, actor: Actor, engine: Engine | None = None
) -> list[str]:
    """Start workers on ready tasks; close ready 'start'/'end' milestones."""
    if service.state.settings.autonomy == "ask-all":
        return []
    if engine is not None:
        return [run.task_id for run in engine.dispatch(actor, "unblocked: starting automatically")]
    started: list[str] = []
    for _ in range(3):  # closing a milestone can unblock more tasks
        progressed = False
        for task in service.ready():
            if task["kind"] in AUTO_START_KINDS:
                try:
                    manager.start(actor, task["id"], "unblocked: starting automatically",
                                  agent_id="worker")
                    started.append(task["id"])
                except TaskGraphError:
                    continue
            elif task["kind"] in ("start", "end"):
                service.submit(actor, [SetStatus(task_id=task["id"], status="closed")],
                               reason="milestone reached")
                progressed = True
        if not progressed:
            break
    return started


def review_task(
    service: TaskGraphService,
    manager: RunManager,
    actor: Actor,
    task_id: str,
    approve: bool,
    note: str | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    task = service.task(task_id)
    if task["status"] != "in-review":
        raise TaskGraphError("conflict", f"'{task_id}' is {task['status']}, not in-review")
    note = (note or "").strip() or None
    ops = []
    if note:
        ops.append(AddComment(task_id=task_id, body=f"Review: {note}"))
    ops.append(SetStatus(task_id=task_id, status="closed" if approve else "open"))
    service.submit(actor, ops, reason=note or ("approved" if approve else "sent back"))
    if approve:
        return {"task": service.task(task_id), "started": auto_start(service, manager, actor, engine)}
    run = manager.start(
        actor, task_id, "sent back for another attempt", agent_id="worker",
        instructions=f"The owner sent the previous result back: {note}" if note else
        "The owner sent the previous result back; improve it.",
    )
    return {"task": service.task(task_id), "started": [task_id], "run": run.dump()}


def decide(
    service: TaskGraphService,
    manager: RunManager,
    actor: Actor,
    task_id: str,
    chosen: str,
    rationale: str,
    engine: Engine | None = None,
) -> dict[str, Any]:
    ops = [ResolveDecision(task_id=task_id, chosen=chosen, rationale=rationale)]
    if service.task(task_id)["kind"] == "task":
        # A worker asked this on its own task: resolving closes a task, but the
        # work is not done; reopen it so it runs again with the answer.
        ops.append(SetStatus(task_id=task_id, status="open"))
    service.submit(actor, ops, reason=rationale)
    return {"task": service.task(task_id), "started": auto_start(service, manager, actor, engine)}
