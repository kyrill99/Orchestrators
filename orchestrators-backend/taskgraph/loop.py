"""Owner actions that close the loop: approve or send back a finished task,
answer a decision, and start whatever became ready.

    worker run succeeds -> task in-review -> owner approves (closed) or sends back
    (worker restarts with the feedback) -> newly unblocked tasks start
    automatically, unless the project's autonomy is 'ask-all'.
"""

from __future__ import annotations

from typing import Any

from .models import Actor, AddComment, ResolveDecision, SetStatus
from .runs import RunManager
from .service import TaskGraphError, TaskGraphService

AUTO_START_KINDS = ("task",)


def auto_start(service: TaskGraphService, manager: RunManager, actor: Actor) -> list[str]:
    """Start a worker on every ready task; close ready 'start'/'end' milestones."""
    if service.state.settings.autonomy == "ask-all":
        return []
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
        return {"task": service.task(task_id), "started": auto_start(service, manager, actor)}
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
) -> dict[str, Any]:
    service.submit(actor, [ResolveDecision(task_id=task_id, chosen=chosen, rationale=rationale)],
                   reason=rationale)
    return {"task": service.task(task_id), "started": auto_start(service, manager, actor)}
