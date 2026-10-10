"""Owner actions that close the loop: approve or send back a finished task,
answer a decision (or change it, or send input back to its preparation), and
start whatever became ready.

    worker run succeeds -> task in-review -> owner approves (closed) or sends back
    (worker restarts with the feedback) -> newly unblocked tasks start
    automatically, unless the project's autonomy is 'ask-all'.

With an `engine` (taskgraph/engine.py, what the API server passes), starting
goes through Engine.dispatch: the same queue rules and ENGINE_MAX_RUNS limit
as the engine's own loop, so owner actions and the engine never disagree.
Without one, every ready task starts (the original behaviour).

Decision tasks (engine.py describes the whole lifecycle): answering one closes
it once its blockers are closed or in review, so the tasks after it start.
Changing an answer later (a new decide on a resolved decision) keeps the old
one in the decision's history and, for each task directly after it that had
already started, restarts its active run or reopens it (in review / closed)
with a comment saying what changed, so it is redone with the new choice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Actor, AddComment, DecisionFeedback, ResolveDecision, SetStatus, blocker_done
from .runs import RunManager
from .service import TaskGraphError, TaskGraphService

if TYPE_CHECKING:
    from .engine import Engine

AUTO_START_KINDS = ("task",)


def _decided_but_open(service: TaskGraphService) -> list[dict[str, Any]]:
    """Decision tasks answered before their blockers were done, now actionable."""
    return [t for t in service.actionable_decisions()
            if t["status"] == "open" and (t.get("decision") or {}).get("state") == "resolved"]


def close_ready_milestones(service: TaskGraphService, actor: Actor) -> bool:
    """Close 'start' / 'end' milestones whose blockers are done, and decision
    tasks answered early whose blockers are done now. True if any closed."""
    closed = False
    for _ in range(3):  # closing a milestone can unblock more milestones
        ready = [t for t in service.ready() if t["kind"] in ("start", "end")]
        decided = _decided_but_open(service)
        if not ready and not decided:
            break
        for task in ready:
            service.submit(actor, [SetStatus(task_id=task["id"], status="closed")],
                           reason="milestone reached")
            closed = True
        for task in decided:
            service.submit(actor, [SetStatus(task_id=task["id"], status="closed")],
                           reason=f"decided: {task['decision']['chosen']}")
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
    rationale: str | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Answer a task's decision, or change an answer given before (decision
    tasks): tasks after it that already started are redone with the new one."""
    task = service.task(task_id)
    before = task.get("decision") or {}
    chosen = chosen.strip()
    rationale = (rationale or "").strip() or "(no rationale given)"
    if before.get("state") == "resolved" and before.get("chosen") == chosen:
        raise TaskGraphError("conflict", f"'{chosen}' is already the decision")
    ops: list[Any] = [ResolveDecision(task_id=task_id, chosen=chosen, rationale=rationale)]
    if task["kind"] == "task":
        # A worker asked this on its own task: resolving closes a task, but the
        # work is not done; reopen it so it runs again with the answer.
        ops.append(SetStatus(task_id=task_id, status="open"))
    active = service.state.active_run(task_id)
    if active is not None and task["kind"] == "decision":  # still preparing: the answer wins
        manager.stop(actor, active.id, "decided while the decision was being prepared")
    service.submit(actor, ops, reason=rationale)
    affected: list[str] = []
    if before.get("state") == "resolved":
        affected = propagate_decision_change(service, manager, actor, task_id, before.get("chosen"))
    out = {"task": service.task(task_id), "started": auto_start(service, manager, actor, engine)}
    if affected:
        out["affected"] = affected
    return out


def propagate_decision_change(
    service: TaskGraphService, manager: RunManager, actor: Actor, task_id: str,
    previous: str | None,
) -> list[str]:
    """A decision changed from `previous`: redo the tasks directly after it
    that already started. An active run starts over with a note; a task in
    review or closed goes back to open (the engine queues it again); each gets
    a comment. Tasks not started yet see the new choice in their brief."""
    task = service.task(task_id)
    d = task.get("decision") or {}
    note = (f"Decision changed: '{task['title']}' is now '{d.get('chosen')}' (was '{previous}'), "
            f"by {actor.label()}. Rationale: {d.get('rationale')}")
    affected = []
    for dep_id in task.get("blocks", []):
        dep = service.task(dep_id)
        if not service.runs(task_id=dep_id) and dep["status"] == "open":
            continue  # not started: its brief will carry the new choice
        active = service.state.active_run(dep_id)
        try:
            if active is not None:
                service.submit(actor, [AddComment(
                    task_id=dep_id, body=f"{note}\nStarting it over with the new choice.")],
                    reason="decision changed")
                manager.restart(actor, dep_id, "decision changed",
                                instructions=f"{note}\nRedo this task for '{d.get('chosen')}'.")
            elif dep["status"] in ("in-review", "closed"):
                service.submit(actor, [
                    AddComment(task_id=dep_id,
                               body=f"{note}\nReopened so it is redone with the new choice."),
                    SetStatus(task_id=dep_id, status="open"),
                ], reason="decision changed")
            else:
                service.submit(actor, [AddComment(task_id=dep_id, body=note)],
                               reason="decision changed")
        except TaskGraphError:
            continue
        affected.append(dep_id)
    return affected


def decision_feedback(
    service: TaskGraphService,
    manager: RunManager,
    actor: Actor,
    task_id: str,
    note: str,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """The owner sends input back on a pending decision without deciding: it
    is prepared again with the input (like sending back a task). From then on
    auto-decide leaves this decision to the owner."""
    note = note.strip()
    if not note:
        raise TaskGraphError("invalid", "say what to change")
    task = service.task(task_id)
    if task["kind"] != "decision":
        raise TaskGraphError("invalid", f"'{task_id}' is not a decision task")
    service.submit(actor, [DecisionFeedback(task_id=task_id, note=note)], reason=note)
    me = service.state.tasks[task_id]
    if any(not blocker_done(me, service.state.tasks[b]) for b in me.blocked_by):
        return {"task": service.task(task_id), "started": []}
    agent_id = engine.agent_id if engine is not None else "worker"
    run = manager.restart(actor, task_id, "owner input on the decision", agent_id=agent_id,
                          instructions=f"The owner sent input back on the prepared decision: {note}\n"
                                       "Prepare it again with this in mind.")
    return {"task": service.task(task_id), "started": [task_id], "run": run.dump()}
