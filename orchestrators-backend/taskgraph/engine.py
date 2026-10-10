"""The engine: keeps agents working on ready tasks.

While the engine is *running*, `tick()` (called every ENGINE_INTERVAL seconds
by a background thread in the FastAPI process) starts runs on queued tasks
until ENGINE_MAX_RUNS runs are active. The queue holds work tasks (kind
'task') that are ready (open, blockers closed, no pending decision), have no
active run, and

- were never run, or
- whose last run was 'lost' (e.g. interrupted by a backend restart), fewer
  than ENGINE_MAX_ATTEMPTS times in a row.

A task whose last run failed or was stopped is not retried automatically: a
human (or the planner) restarts it, so a failing task cannot loop. The
exception is a run that stopped to ask the owner (request_decision): once the
decision is answered, the task is queued again. A task that succeeded is
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
            if task["kind"] != "task":
                continue
            history = by_task.get(task["id"], [])
            if any(r.active for r in history):
                continue
            if history:
                last = history[-1]
                if last.status != "lost" and not _answered_since(task, last):
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
