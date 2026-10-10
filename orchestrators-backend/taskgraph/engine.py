"""The engine: keeps agents working on ready tasks.

While the engine is *running*, `tick()` (called every ENGINE_INTERVAL seconds
by a background thread in the FastAPI process) starts runs on queued tasks
until ENGINE_MAX_RUNS runs are active. The queue holds work tasks (kind
'task') that are ready (open, blockers closed, no pending decision), are not
assigned to a person, have no active run, and

- were never run, or
- whose last run was 'lost' (e.g. interrupted by a backend restart), fewer
  than ENGINE_MAX_ATTEMPTS times in a row.

A task whose last run failed or was stopped is not retried automatically: a
human (or the planner) restarts it, so a failing task cannot loop. The
exceptions: a run that stopped to ask the owner (request_decision) is queued
again once the decision is answered, and a task a person sets to 'open' after
its last run ended (succeeded, failed or stopped) is queued again: moving it
back to open means "run it again". A task that succeeded is
'in-review'; the owner approves it or sends it back (taskgraph/loop.py).

`dispatch()` is the one way runs get started automatically: the engine loop
calls it while running, and the owner actions in loop.py (approve, decide,
POST /autostart) call it too. It first closes ready start/end milestones.

The state (running / stopped) lives in SQLite, so it survives restarts and any
process sharing the database (the MCP server) can flip it. Default: stopped.

Env: ENGINE_MAX_RUNS (2), ENGINE_INTERVAL (2 seconds), ENGINE_MAX_ATTEMPTS (3),
     ENGINE_AGENT_ID (worker id given to runs, default "gemini").
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from db import Database

from .loop import close_ready_milestones
from .models import Actor, AgentRun
from .runs import RunManager
from .service import TaskGraphError, TaskGraphService

log = logging.getLogger("taskgraph.engine")

ENGINE_ACTOR = Actor(type="agent", id="engine")


def _answered_since(task: dict[str, Any], run: AgentRun) -> bool:
    """The task's decision was answered after `run` ended (the run asked for it)."""
    decision = task.get("decision") or {}
    answered = decision.get("resolvedAt")
    return bool(decision.get("state") == "resolved" and answered and run.ended_at
                and answered >= run.ended_at)


def _reopened_since(service: TaskGraphService, run: AgentRun) -> bool:
    """A person set the task to 'open' after `run` ended: "run it again".

    Only deliberate status changes count (a human's changeset, after the
    run.ended event), not the automatic move back to 'open' that ending a
    failed or stopped run makes in the same write."""
    if run.ended_at is None:
        return False
    for ev in reversed(service.state.events):  # newest first, back to the run's end
        if ev.type == "run.ended" and ev.payload.get("runId") == run.id:
            return False
        if (ev.type == "task.set_status" and ev.actor.type == "human" and ev.changeset_id
                and run.task_id in ev.task_ids and ev.payload.get("status") == "open"):
            return True
    return False


def _assigned_to_person(task: dict[str, Any]) -> bool:
    """A person took the task: the engine does not give it to an agent."""
    return (task.get("assignee") or {}).get("type") == "human"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Engine:
    def __init__(
        self,
        service: TaskGraphService,
        runs: RunManager,
        db: Database,
        *,
        max_runs: int | None = None,
        max_attempts: int | None = None,
        interval: float | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.service = service
        self.runs = runs
        self.db = db
        self.max_runs = max_runs if max_runs is not None else int(os.environ.get("ENGINE_MAX_RUNS", "2"))
        self.max_attempts = (
            max_attempts if max_attempts is not None else int(os.environ.get("ENGINE_MAX_ATTEMPTS", "3"))
        )
        self.interval = interval if interval is not None else float(os.environ.get("ENGINE_INTERVAL", "2"))
        self.agent_id = agent_id or os.environ.get("ENGINE_AGENT_ID", "gemini")
        self._tick_lock = threading.Lock()
        self._stop_loop = threading.Event()
        self._thread: threading.Thread | None = None
        with db.tx() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS engine_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    running INTEGER NOT NULL,
                    updated_at TEXT,
                    updated_by TEXT,
                    reason TEXT
                )"""
            )
            conn.execute("INSERT OR IGNORE INTO engine_state (id, running) VALUES (1, 0)")

    # ---- state -----------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        with self.db.read() as conn:
            r = conn.execute(
                "SELECT running, updated_at, updated_by, reason FROM engine_state WHERE id = 1"
            ).fetchone()
        return {"running": bool(r[0]), "updatedAt": r[1], "updatedBy": r[2], "reason": r[3]}

    def _set(self, running: bool, actor: Actor, reason: str | None) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE engine_state SET running = ?, updated_at = ?, updated_by = ?, reason = ? WHERE id = 1",
                (int(running), _now(), actor.id, reason),
            )

    @property
    def running(self) -> bool:
        return self._state()["running"]

    def start(self, actor: Actor, reason: str | None = None) -> dict[str, Any]:
        if self.runs.runner.name == "record":
            raise TaskGraphError(
                "conflict",
                "the engine needs a runner that executes runs (set GEMINI_API_KEY or TASKGRAPH_RUNNER)",
            )
        self._set(True, actor, reason)
        self.wake()
        return self.status()

    def stop(self, actor: Actor, reason: str | None = None, *, stop_runs: bool = False) -> dict[str, Any]:
        self._set(False, actor, reason)
        if stop_runs:
            for run in self.runs.active():
                try:
                    self.runs.stop(actor, run.id, reason or "engine stopped")
                except TaskGraphError as e:  # ended meanwhile
                    log.info("engine stop: %s", e)
        return self.status()

    # ---- queue and dispatch -----------------------------------------------

    def queue(self) -> list[dict[str, Any]]:
        """Tasks the engine would start next, in graph order."""
        by_task: dict[str, list[AgentRun]] = {}
        for run in self.service.runs():
            by_task.setdefault(run.task_id, []).append(run)
        out = []
        for task in self.service.ready():
            if task["kind"] != "task" or _assigned_to_person(task):
                continue
            history = by_task.get(task["id"], [])
            if any(r.active for r in history):
                continue
            if history:
                last = history[-1]
                if last.status != "lost" and not _answered_since(task, last) \
                        and not _reopened_since(self.service, last):
                    continue
                lost_streak = 0
                for r in reversed(history):
                    if r.status != "lost":
                        break
                    lost_streak += 1
                if lost_streak >= self.max_attempts:
                    continue
            out.append(task)
        return out

    def tick(self) -> list[AgentRun]:
        """While running: dispatch. Returns the new runs."""
        if not self.running:
            return []
        return self.dispatch(ENGINE_ACTOR, "engine: task is ready")

    def dispatch(self, actor: Actor, reason: str) -> list[AgentRun]:
        """Close ready milestones, then start runs on queued tasks while there
        are free slots (whether or not the engine is running)."""
        if actor.type == "agent" and self.service.state.settings.autonomy == "ask-all":
            return []  # a human has to start runs
        with self._tick_lock:
            close_ready_milestones(self.service, actor)
            started: list[AgentRun] = []
            free = self.max_runs - len(self.runs.active())
            for task in self.queue():
                if free <= 0:
                    break
                last = self.service.runs(task_id=task["id"])
                try:
                    run = self.runs.start(
                        actor, task["id"], reason,
                        agent_id=self.agent_id,
                        restart_of=last[-1].id if last else None,
                    )
                except TaskGraphError as e:
                    log.warning("engine could not start %s: %s", task["id"], e)
                    continue
                started.append(run)
                free -= 1
            return started

    def status(self) -> dict[str, Any]:
        state = self._state()
        active = self.runs.active()
        queue = self.queue()
        return {
            "state": "running" if state["running"] else "stopped",
            **state,
            "runner": self.runs.runner.name,
            "maxRuns": self.max_runs,
            "activeRuns": [r.dump() for r in active],
            "queue": [{"id": t["id"], "title": t["title"]} for t in queue],
            "waiting": self.waiting(active, queue),
        }

    def waiting(self, active: list[AgentRun] | None = None,
                queue: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Why work is not moving: what waits for the owner (review, decisions,
        proposed changes), which open tasks are blocked and by what, which
        ready tasks the engine will not start (assigned to a person; failed,
        stopped or lost too often), and whether autonomy or the run limit
        holds the queue.

        `reason` is the main cause (a code, or None when nothing waits),
        `summary` one line for people, `focus` the task to look at first."""
        active = self.runs.active() if active is None else active
        queue = self.queue() if queue is None else queue
        g = self.service
        tasks = {t["id"]: t for t in g.graph()["tasks"]}
        brief = lambda t: {"id": t["id"], "title": t["title"]}  # noqa: E731

        review = [brief(t) for t in tasks.values() if t["status"] == "in-review"]
        decisions = [
            {"id": d["taskId"], "title": d["title"], "question": d["question"]}
            for d in g.decisions("pending")
        ]
        proposals = [{"id": c["id"], "reason": c.get("reason")} for c in g.changesets("proposed")]
        blocked = [
            {"id": b["id"], "title": b["title"],
             "blockedBy": [{"id": w["id"], "title": tasks[w["id"]]["title"], "status": w["status"]}
                           for w in b["waitingOn"]]}
            for b in g.blocked() if tasks[b["id"]]["status"] == "open"
        ]
        queued = {t["id"] for t in queue}
        active_tasks = {r.task_id for r in active}
        ready = g.ready()
        milestones = [t for t in ready if t["kind"] in ("start", "end")]
        assigned_to_people, needs_restart = [], []
        for t in ready:
            if t["kind"] != "task" or t["id"] in queued or t["id"] in active_tasks:
                continue
            if _assigned_to_person(t):
                assigned_to_people.append({**brief(t), "assignee": t["assignee"]["id"]})
                continue
            history = g.runs(task_id=t["id"])
            if history:
                needs_restart.append({**brief(t), "lastRun": history[-1].status})
        ask_all = g.state.settings.autonomy == "ask-all"
        at_limit = bool(queue) and len(active) >= self.max_runs

        def first(items: list[dict[str, Any]], what: str) -> str:
            more = f" (+{len(items) - 1} more)" if len(items) > 1 else ""
            return f"{what}: {items[0]['title']}{more}"

        parts: list[tuple[str, str]] = []
        held = len(queue) + len(milestones)  # ask-all: the engine does not even close milestones
        if held and ask_all:
            parts.append(("ask-all", f"Autonomy is ask-all, so the engine starts nothing by itself "
                                     f"({held} ready)"))
        elif at_limit:
            parts.append(("run-limit", f"At the run limit ({len(active)}/{self.max_runs}); "
                                       f"{len(queue)} queued"))
        if review:
            parts.append(("review", first(review, "Waiting for your review")))
        if decisions:
            parts.append(("decision", first(decisions, "Waiting for your decision")))
        if proposals:
            n = len(proposals)
            parts.append(("proposal", f"Waiting for you to approve {n} proposed change{'s' * (n != 1)}"))
        if assigned_to_people:
            t = assigned_to_people[0]
            parts.append(("assigned-to-person", f"Assigned to a person, so the engine leaves it: "
                                                f"{t['title']} ({t['assignee']})"))
        if needs_restart:
            t = needs_restart[0]
            parts.append(("needs-restart", f"Not restarted automatically: {t['title']} "
                                           f"(last run {t['lastRun']}; restart it, or set it to "
                                           f"open to queue it again)"))
        if not parts and blocked and not active and not queue:
            b = blocked[0]
            on = ", ".join(f"{w['title']} ({w['status']})" for w in b["blockedBy"])
            parts.append(("blocked", f"Blocked: {b['title']} waits on {on}"))

        focus = None
        if review:
            focus = {**review[0], "kind": "review"}
        elif decisions:
            focus = {"id": decisions[0]["id"], "title": decisions[0]["title"], "kind": "decision"}
        elif assigned_to_people:
            focus = {**brief(assigned_to_people[0]), "kind": "assigned"}
        elif needs_restart:
            focus = {**brief(needs_restart[0]), "kind": "restart"}

        if parts:
            summary = "; ".join(p for _, p in parts)
        elif active:
            summary = f"Agents are working on {len(active)} task{'s' * (len(active) != 1)}"
        elif queue:
            summary = f"{len(queue)} task{'s' * (len(queue) != 1)} ready to start"
        elif tasks and all(t["status"] == "closed" for t in tasks.values()):
            summary = "All tasks are done"
        elif not tasks:
            summary = "No tasks yet"
        else:
            summary = "Nothing is ready to start"
        return {
            "reason": parts[0][0] if parts else None,
            "summary": summary,
            "focus": focus,
            "review": review,
            "decisions": decisions,
            "proposals": proposals,
            "blocked": blocked,
            "assignedToPeople": assigned_to_people,
            "needsRestart": needs_restart,
            "autonomy": g.state.settings.autonomy,
            "atRunLimit": at_limit,
        }

    # ---- background loop --------------------------------------------------

    def start_loop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_loop.clear()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop_loop(self) -> None:
        self._stop_loop.set()
        self.wake()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wake(self) -> None:
        wake = getattr(self, "_wake", None)
        if wake is not None:
            wake.set()

    def _loop(self) -> None:
        while not self._stop_loop.is_set():
            try:
                self.tick()
            except Exception:  # never let the loop die
                log.exception("engine tick failed")
            self._wake.wait(self.interval)
            self._wake.clear()
