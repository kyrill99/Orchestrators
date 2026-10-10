"""The engine: keeps agents working on ready tasks, and keeps decisions moving.

While the engine is *running*, `tick()` (called every ENGINE_INTERVAL seconds
by a background thread in the FastAPI process) starts runs on queued tasks
until ENGINE_MAX_RUNS runs are active. The queue holds work tasks (kind
'task') that are ready (open, blockers closed, no pending decision), are not
assigned to a person, have no active run, and

- were never run, or
- whose last run was 'lost' (e.g. interrupted by a backend restart), fewer
  than ENGINE_MAX_ATTEMPTS times in a row, or
- whose last run failed because of the infrastructure, not the agent
  (failure kind "api": the model API kept failing, or "infra": the runner
  crashed or the checkout could not be made; see models.FailureKind), at most
  ENGINE_MAX_RETRIES times in a row and ENGINE_RETRY_BACKOFF seconds after it
  ended. The retry's reason shows in the task's activity.

A task whose last run failed otherwise (the agent finished 'failed', its
checks failed, it ran out of budget) or was stopped is not retried
automatically: a human (or the planner) restarts it, so a failing task cannot
loop. The exceptions: a run that stopped to ask the owner (request_decision)
is queued again once the decision is answered, and a task a person sets to
'open' after its last run ended (succeeded, failed or stopped) is queued
again: moving it back to open means "run it again". A task that succeeded is
'in-review'; the owner approves it or sends it back (taskgraph/loop.py).

Decision tasks (kind 'decision'):

1. Actionable once every blocker is closed *or in review* (models.blocker_done):
   finished work is input to the decision, which is in effect the owner's
   review of it. The blockers are not approved automatically.
2. Prepared: an actionable decision task without a prepared decision (none,
   one without a recommendation, or one the owner sent input back on) is
   queued for a decision-prep run, a Gemini run with the decider role
   (services/gemini_worker.py) that reads the blockers' results, saves a
   comparison report and calls prepare_decision: question, 2-5 options, the
   recommended option and why. The run does not assign the task, and the task
   goes back to 'open' afterwards (not to review): it waits for the decision.
3. Decided: by the owner (loop.decide), or, while the engine is running and
   autonomy is not 'ask-all', by the agent (`auto_decide`): a prepared
   decision still pending DECISION_AUTO_AFTER_SECONDS after it was prepared
   (default 0: right away) is resolved with the recommended option and the
   prep run's reasons, marked auto (resolved by agent 'agent'). Under
   'ask-structural' that resolution is proposed for approval like any
   structural agent change. Not auto-decided: decisions the owner sent input
   back on (they are engaged; it waits for them), decision tasks assigned to
   a person, and questions workers asked on work tasks.
4. Answering closes the decision task, so the tasks after it start; their
   briefs lead with the question, the chosen option and the rationale. The
   owner can change the answer later; see loop.propagate_decision_change.

`dispatch()` is the one way runs get started automatically: the engine loop
calls it while running, and the owner actions in loop.py (approve, decide,
POST /autostart) call it too. It first closes ready start/end milestones and
decision tasks answered before their blockers were done.

The state (running / stopped) lives in SQLite, so it survives restarts and any
process sharing the database (the MCP server) can flip it. Default: stopped.

Env: ENGINE_MAX_RUNS (2), ENGINE_INTERVAL (2 seconds), ENGINE_MAX_ATTEMPTS (3),
     ENGINE_MAX_RETRIES (2), ENGINE_RETRY_BACKOFF (60 seconds),
     ENGINE_AGENT_ID (worker id given to runs, default "gemini"),
     DECISION_AUTO_AFTER_SECONDS (0; raise it to give the owner time to
     answer before the agent decides).
"""

from __future__ import annotations

import logging
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any

from db import Database

from .loop import close_ready_milestones
from .models import AGENT_ASSIGNEE, RETRYABLE_FAILURES, Actor, AgentRun, ResolveDecision
from .runs import RunManager
from .service import TaskGraphError, TaskGraphService

log = logging.getLogger("taskgraph.engine")

ENGINE_ACTOR = Actor(type="agent", id="engine")
# Who resolves a decision automatically: the GUI's "Agent".
DECIDER_ACTOR = AGENT_ASSIGNEE
AUTO_NOTE = "Decided automatically by the agent (the engine was running); the owner can change it."
FAILURE_LABELS = {"api": "an API error", "infra": "an infrastructure error", "budget": "its budget ran out",
                  "agent": "the agent could not finish", "checks": "its checks failed"}


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _retry_streak(history: list[AgentRun]) -> int:
    """Failed runs with a retryable failure kind at the end of `history`."""
    n = 0
    for r in reversed(history):
        if r.status != "failed" or r.failure_kind not in RETRYABLE_FAILURES:
            break
        n += 1
    return n


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


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


def _needs_prep(task: dict[str, Any]) -> bool:
    """A decision task whose decision is not prepared: none yet, a pending one
    without a recommendation, or one the owner sent input back on since."""
    d = task.get("decision")
    if not d:
        return True
    if d.get("state") != "pending":
        return False
    if not d.get("recommended"):
        return True
    return bool(d.get("feedbackAt") and d["feedbackAt"] > (d.get("requestedAt") or ""))


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
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        decision_grace: float | None = None,
        clock: Any = None,
    ) -> None:
        self.max_retries = (max_retries if max_retries is not None
                            else int(_env_float("ENGINE_MAX_RETRIES", 2)))
        self.retry_backoff = (retry_backoff if retry_backoff is not None
                              else _env_float("ENGINE_RETRY_BACKOFF", 60))
        self.decision_grace = (decision_grace if decision_grace is not None
                               else _env_float("DECISION_AUTO_AFTER_SECONDS", 0))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
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

    def _seconds_since(self, ts: str | None) -> float | None:
        t = _parse_ts(ts)
        return None if t is None else (self.clock() - t).total_seconds()

    def _verdict(self, task: dict[str, Any], history: list[AgentRun]) -> tuple[bool, str | None]:
        """Queue a task with this run history? Returns (yes/no, the reason to
        give the new run when it is an automatic retry)."""
        if any(r.active for r in history):
            return False, None
        if not history:
            return True, None
        last = history[-1]
        if _answered_since(task, last) or _reopened_since(self.service, last):
            return True, None
        if last.status == "lost":
            lost_streak = 0
            for r in reversed(history):
                if r.status != "lost":
                    break
                lost_streak += 1
            return lost_streak < self.max_attempts, None
        if last.status == "failed" and last.failure_kind in RETRYABLE_FAILURES:
            streak = _retry_streak(history)
            if streak > self.max_retries:
                return False, None
            age = self._seconds_since(last.ended_at)
            if age is not None and age < self.retry_backoff:
                return False, None
            return True, (f"engine: retrying after {FAILURE_LABELS[last.failure_kind]} "
                          f"(automatic retry {streak} of {self.max_retries})")
        return False, None

    def _runs_by_task(self) -> dict[str, list[AgentRun]]:
        by_task: dict[str, list[AgentRun]] = {}
        for run in self.service.runs():
            by_task.setdefault(run.task_id, []).append(run)
        return by_task

    def _queue(self) -> list[tuple[dict[str, Any], str | None]]:
        by_task = self._runs_by_task()
        out = []
        for task in self.service.ready():
            if task["kind"] != "task" or _assigned_to_person(task):
                continue
            ok, why = self._verdict(task, by_task.get(task["id"], []))
            if ok:
                out.append((task, why))
        return out

    def queue(self) -> list[dict[str, Any]]:
        """Work tasks the engine would start next, in graph order."""
        return [t for t, _ in self._queue()]

    def _decision_queue(self) -> list[tuple[dict[str, Any], str]]:
        by_task = self._runs_by_task()
        out = []
        for t in self.service.actionable_decisions():
            if t["status"] != "open" or _assigned_to_person(t) or not _needs_prep(t):
                continue
            history = by_task.get(t["id"], [])
            if any(r.active for r in history):
                continue
            d = t.get("decision") or {}
            last = history[-1] if history else None
            if last is not None and d.get("feedbackAt") and last.ended_at and d["feedbackAt"] >= last.ended_at:
                out.append((t, "engine: preparing the decision again with the owner's input"))
                continue
            ok, why = self._verdict(t, history)
            if ok:
                out.append((t, why or "engine: preparing the decision"))
        return out

    def decision_queue(self) -> list[dict[str, Any]]:
        """Decision tasks the engine would start a decision-prep run on."""
        return [t for t, _ in self._decision_queue()]

    def tick(self) -> list[AgentRun]:
        """While running: dispatch, decide what the agent may decide, and
        dispatch again for what that unblocked. Returns the new runs."""
        if not self.running:
            return []
        started = self.dispatch(ENGINE_ACTOR, "engine: task is ready")
        if self.auto_decide():
            started += self.dispatch(ENGINE_ACTOR, "engine: unblocked by a decision")
        return started

    def dispatch(self, actor: Actor, reason: str) -> list[AgentRun]:
        """Close ready milestones (and decisions answered early), then start
        decision-prep runs and work runs on queued tasks while there are free
        slots (whether or not the engine is running)."""
        if actor.type == "agent" and self.service.state.settings.autonomy == "ask-all":
            return []  # a human has to start runs
        with self._tick_lock:
            close_ready_milestones(self.service, actor)
            started: list[AgentRun] = []
            free = self.max_runs - len(self.runs.active())
            for task, why in [*self._decision_queue(), *self._queue()]:
                if free <= 0:
                    break
                last = self.service.runs(task_id=task["id"])
                try:
                    run = self.runs.start(
                        actor, task["id"], why or reason,
                        agent_id=self.agent_id,
                        restart_of=last[-1].id if last else None,
                    )
                except TaskGraphError as e:
                    log.warning("engine could not start %s: %s", task["id"], e)
                    continue
                started.append(run)
                free -= 1
            return started

    # ---- decisions --------------------------------------------------------

    def _auto_pending(self) -> list[tuple[dict[str, Any], float]]:
        """Prepared, pending decisions the agent may take while the engine
        runs, with the seconds left before it does (DECISION_AUTO_AFTER_SECONDS
        after the decision was prepared)."""
        if self.service.state.settings.autonomy == "ask-all":
            return []
        out = []
        for t in self.service.actionable_decisions():
            d = t.get("decision") or {}
            if t["status"] != "open" or d.get("state") != "pending" or not d.get("recommended"):
                continue
            if d.get("feedback") or _assigned_to_person(t):
                continue  # the owner is engaged / took it
            age = self._seconds_since(d.get("requestedAt"))
            left = 0.0 if age is None else max(0.0, self.decision_grace - age)
            out.append((t, left))
        return out

    def auto_decide(self) -> list[str]:
        """While running: resolve each prepared decision whose grace period is
        over with the recommended option, as the agent. Returns the task ids."""
        if not self.running:
            return []
        proposed = {op.get("taskId") for c in self.service.changesets("proposed")
                    for op in c["ops"] if op.get("op") == "decision.resolve"}
        decided = []
        for t, left in self._auto_pending():
            if left > 0 or t["id"] in proposed:
                continue
            d = t["decision"]
            rationale = f"{(d.get('recommendation') or 'The prepared recommendation.').strip()}\n\n{AUTO_NOTE}"
            try:
                seq = self.service.state.seq
                current = self.service.task(t["id"]).get("decision") or {}
                if current.get("state") != "pending" or current.get("requestedAt") != d.get("requestedAt"):
                    continue  # answered or prepared again meanwhile
                self.service.submit(
                    DECIDER_ACTOR,
                    [ResolveDecision(task_id=t["id"], chosen=d["recommended"], rationale=rationale, auto=True)],
                    reason="auto-decide: the engine is running and autonomy lets agents act, "
                           "so the agent takes the prepared recommendation",
                    base_seq=seq,
                )
            except TaskGraphError as e:
                log.info("auto-decide %s skipped: %s", t["id"], e)
                continue
            decided.append(t["id"])
        return decided

    def status(self) -> dict[str, Any]:
        state = self._state()
        active = self.runs.active()
        queue = self.queue()
        prep = self.decision_queue()
        return {
            "state": "running" if state["running"] else "stopped",
            **state,
            "runner": self.runs.runner.name,
            "maxRuns": self.max_runs,
            "decisionAutoAfterSeconds": self.decision_grace,
            "activeRuns": [r.dump() for r in active],
            "queue": [{"id": t["id"], "title": t["title"], "kind": t["kind"]} for t in [*prep, *queue]],
            "waiting": self.waiting(active, queue, prep, running=state["running"]),
        }

    def waiting(self, active: list[AgentRun] | None = None,
                queue: list[dict[str, Any]] | None = None,
                prep: list[dict[str, Any]] | None = None,
                *, running: bool | None = None) -> dict[str, Any]:
        """Why work is not moving: what waits for the owner (review, decisions,
        proposed changes), which decisions the agent is preparing or will take
        itself, which open tasks are blocked and by what, which ready tasks the
        engine will not start (assigned to a person; failed, stopped or lost
        too often), which it retries after an infrastructure failure, and
        whether autonomy or the run limit holds the queue.

        `reason` is the main cause (a code, or None when nothing waits),
        `summary` one line for people, `focus` the task to look at first."""
        active = self.runs.active() if active is None else active
        queue = self.queue() if queue is None else queue
        prep = self.decision_queue() if prep is None else prep
        running = self.running if running is None else running
        g = self.service
        tasks = {t["id"]: t for t in g.graph()["tasks"]}
        brief = lambda t: {"id": t["id"], "title": t["title"]}  # noqa: E731
        active_tasks = {r.task_id for r in active}

        review = [brief(t) for t in tasks.values() if t["status"] == "in-review"]
        auto = {t["id"]: left for t, left in self._auto_pending()} if running else {}
        decisions = []
        for d in g.decisions("pending"):
            task = tasks[d["taskId"]]
            if task["id"] in active_tasks:
                continue  # being prepared again
            decisions.append({
                "id": d["taskId"], "title": d["title"], "question": d["question"],
                "recommended": d.get("recommended"), "auto": d["taskId"] in auto,
                "autoInSeconds": math.ceil(auto[d["taskId"]]) if d["taskId"] in auto else None,
                "feedback": bool(d.get("feedback")),
            })
        preparing = [brief(tasks[r.task_id]) for r in active
                     if r.task_id in tasks and tasks[r.task_id]["kind"] == "decision"]
        to_prepare = [brief(t) for t in prep]
        proposals = [{"id": c["id"], "reason": c.get("reason")} for c in g.changesets("proposed")]
        blocked = [
            {"id": b["id"], "title": b["title"],
             "blockedBy": [{"id": w["id"], "title": tasks[w["id"]]["title"], "status": w["status"]}
                           for w in b["waitingOn"]]}
            for b in g.blocked() if tasks[b["id"]]["status"] == "open"
        ]
        queued = {t["id"] for t in queue} | {t["id"] for t in prep}
        ready = g.ready()
        milestones = [t for t in ready if t["kind"] in ("start", "end")]
        assigned_to_people, needs_restart, retrying = [], [], []
        candidates = [t for t in ready if t["kind"] == "task"]
        candidates += [t for t in g.actionable_decisions()
                       if t["status"] == "open" and _needs_prep(t)]
        for t in candidates:
            if t["id"] in queued or t["id"] in active_tasks:
                continue
            if _assigned_to_person(t):
                assigned_to_people.append({**brief(t), "assignee": t["assignee"]["id"]})
                continue
            history = g.runs(task_id=t["id"])
            if not history:
                continue
            last = history[-1]
            streak = _retry_streak(history)
            if last.status == "failed" and last.failure_kind in RETRYABLE_FAILURES \
                    and streak <= self.max_retries:
                age = self._seconds_since(last.ended_at) or 0.0
                retrying.append({**brief(t), "failureKind": last.failure_kind,
                                 "inSeconds": math.ceil(max(0.0, self.retry_backoff - age)),
                                 "retry": streak, "maxRetries": self.max_retries})
                continue
            why = f"last run {last.status}"
            if last.status == "failed" and last.failure_kind:
                why += f": {FAILURE_LABELS.get(last.failure_kind, last.failure_kind)}"
                if last.failure_kind in RETRYABLE_FAILURES:
                    why += f", after {self.max_retries} automatic retries"
            needs_restart.append({**brief(t), "lastRun": last.status,
                                  "failureKind": last.failure_kind, "why": why})
        changes = []
        for t in tasks.values():
            d = t.get("decision") or {}
            if t["kind"] != "decision" or d.get("state") != "resolved" or not d.get("history"):
                continue
            redo = [tasks[x]["title"] for x in tasks if t["id"] in tasks[x]["blockedBy"]
                    and tasks[x]["status"] != "closed"]
            if redo:
                changes.append({**brief(t), "chosen": d.get("chosen"),
                                "previous": d["history"][-1]["chosen"], "redoing": redo})
        ask_all = g.state.settings.autonomy == "ask-all"
        at_limit = bool(queue or prep) and len(active) >= self.max_runs

        def first(items: list[dict[str, Any]], what: str) -> str:
            more = f" (+{len(items) - 1} more)" if len(items) > 1 else ""
            return f"{what}: {items[0]['title']}{more}"

        parts: list[tuple[str, str]] = []
        held = len(queue) + len(milestones) + len(prep)  # ask-all: not even milestones close
        if held and ask_all:
            parts.append(("ask-all", f"Autonomy is ask-all, so the engine starts nothing by itself "
                                     f"({held} ready)"))
        elif at_limit:
            parts.append(("run-limit", f"At the run limit ({len(active)}/{self.max_runs}); "
                                       f"{len(queue) + len(prep)} queued"))
        if review:
            parts.append(("review", first(review, "Waiting for your review")))
        mine = [d for d in decisions if not d["auto"]]
        agents = [d for d in decisions if d["auto"]]
        if mine:
            parts.append(("decision", first(mine, "Waiting for your decision")))
        if agents:
            d = agents[0]
            when = "now" if not d["autoInSeconds"] else f"in {d['autoInSeconds']} s"
            parts.append(("decision-auto", f"The agent decides {when} unless you choose: {d['title']} "
                                           f"(recommends {d['recommended']})"))
        if preparing:
            parts.append(("preparing-decision", first(preparing, "Agent is preparing a decision")))
        if proposals:
            n = len(proposals)
            parts.append(("proposal", f"Waiting for you to approve {n} proposed change{'s' * (n != 1)}"))
        if assigned_to_people:
            t = assigned_to_people[0]
            parts.append(("assigned-to-person", f"Assigned to a person, so the engine leaves it: "
                                                f"{t['title']} ({t['assignee']})"))
        if retrying:
            t = retrying[0]
            when = "now" if not t["inSeconds"] else f"in {t['inSeconds']} s"
            parts.append(("retrying", f"Retrying {when} after {FAILURE_LABELS[t['failureKind']]}: "
                                      f"{t['title']} (retry {t['retry']} of {t['maxRetries']})"))
        if needs_restart:
            t = needs_restart[0]
            parts.append(("needs-restart", f"Not restarted automatically: {t['title']} "
                                           f"({t['why']}; restart it, or set it to "
                                           f"open to queue it again)"))
        if changes:
            c = changes[0]
            parts.append(("decision-changed", f"Decision changed: {c['title']} is now '{c['chosen']}' "
                                              f"(was '{c['previous']}'); {len(c['redoing'])} task"
                                              f"{'s' * (len(c['redoing']) != 1)} after it redo their work"))
        if not parts and blocked and not active and not queue and not prep:
            b = blocked[0]
            on = ", ".join(f"{w['title']} ({w['status']})" for w in b["blockedBy"])
            parts.append(("blocked", f"Blocked: {b['title']} waits on {on}"))

        focus = None
        if mine:
            focus = {"id": mine[0]["id"], "title": mine[0]["title"], "kind": "decision"}
        elif review:
            focus = {**review[0], "kind": "review"}
        elif agents:
            focus = {"id": agents[0]["id"], "title": agents[0]["title"], "kind": "decision"}
        elif assigned_to_people:
            focus = {**brief(assigned_to_people[0]), "kind": "assigned"}
        elif needs_restart:
            focus = {**brief(needs_restart[0]), "kind": "restart"}

        if parts:
            summary = "; ".join(p for _, p in parts)
        elif active:
            summary = f"Agents are working on {len(active)} task{'s' * (len(active) != 1)}"
        elif queue or prep:
            n = len(queue) + len(prep)
            summary = f"{n} task{'s' * (n != 1)} ready to start"
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
            "preparingDecisions": preparing,
            "decisionsToPrepare": to_prepare,
            "decisionChanges": changes,
            "proposals": proposals,
            "blocked": blocked,
            "assignedToPeople": assigned_to_people,
            "needsRestart": needs_restart,
            "retrying": retrying,
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
