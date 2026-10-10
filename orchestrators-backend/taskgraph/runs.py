"""Agent runs: start, stop and restart agents working on tasks.

`RunManager` records every run in the task graph event log (via
`TaskGraphService.start_run` / `end_run`) and hands execution to a runner:

- `RecordRunner` ("record", the default) launches nothing. A run is a record
  that an agent is working on a task; the agent (or whoever does the work)
  reports the outcome with `finish`. Stop only records the stop.
- `SubprocessRunner` ("subprocess") launches one local process per run from a
  command template, writes its output to a log file, records the exit code
  when it ends, and stops it with SIGTERM (SIGKILL after a grace period).

Because runs live in the shared log, every process sees the same runs. A
subprocess run belongs to the process that launched it; another process on the
same host can still stop it by pid. If the launching process went away and the
pid is gone, `reconcile` marks the run 'lost' (exit code unknown).

Env (see `runner_from_env`):
  TASKGRAPH_RUNNER      record (default) | subprocess
  TASKGRAPH_RUNNER_CMD  command for subprocess runs; {run_id}, {task_id},
                        {agent_id} are filled in. The prompt goes to stdin.
  TASKGRAPH_RUN_LOGS    log directory (default: <dir of TASKGRAPH_PATH>/runs)
"""

from __future__ import annotations

import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .models import Actor, AgentRun, RunStatus, Task
from .service import TaskGraphError, TaskGraphService

# (status, exit_code, note) reported by a runner when a run ends on its own.
OnExit = Callable[[RunStatus, "int | None", "str | None"], None]


class Runner(Protocol):
    name: str

    def start(self, run: AgentRun, task: Task, on_exit: OnExit) -> dict[str, Any]:
        """Begin executing `run`. Returns details to record (pid, host, log_path)."""
        ...

    def stop(self, run: AgentRun) -> None:
        """Stop executing `run` (best effort; must not raise if already gone)."""
        ...

    def is_alive(self, run: AgentRun) -> bool | None:
        """True/False if known, None if this runner cannot tell."""
        ...


class RecordRunner:
    """Executes nothing: runs are records the planner and workers keep up to date."""

    name = "record"

    def start(self, run: AgentRun, task: Task, on_exit: OnExit) -> dict[str, Any]:
        return {}

    def stop(self, run: AgentRun) -> None:
        return None

    def is_alive(self, run: AgentRun) -> bool | None:
        return None


def render_prompt(run: AgentRun, task: Task) -> str:
    """The brief a worker gets on stdin."""
    lines = [f"Task {task.id}: {task.title}"]
    if task.description:
        lines += ["", task.description]
    if run.instructions:
        lines += ["", "Instructions from the planner:", run.instructions]
    lines += [
        "",
        f"You are agent '{run.agent.id}', run {run.id} (attempt {run.attempt}).",
        "Report progress with add_comment on this task and finish with finish_run.",
    ]
    return "\n".join(lines) + "\n"


class SubprocessRunner:
    """One local process per run, started from a command template."""

    name = "subprocess"

    def __init__(
        self,
        command: str | list[str],
        *,
        log_dir: str | os.PathLike[str] = "data/runs",
        env: dict[str, str] | None = None,
        grace_seconds: float = 5.0,
    ) -> None:
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        if not self.command:
            raise ValueError("SubprocessRunner needs a command")
        self.log_dir = Path(log_dir)
        self.env = env or {}
        self.grace_seconds = grace_seconds
        self.host = socket.gethostname()
        self._procs: dict[str, subprocess.Popen] = {}
        self._stopping: set[str] = set()
        self._lock = threading.Lock()

    def start(self, run: AgentRun, task: Task, on_exit: OnExit) -> dict[str, Any]:
        fill = {"run_id": run.id, "task_id": task.id, "agent_id": run.agent.id}
        argv = [arg.format(**fill) for arg in self.command]
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{run.id}.log"
        env = {
            **os.environ,
            **self.env,
            "TASKGRAPH_RUN_ID": run.id,
            "TASKGRAPH_TASK_ID": task.id,
            "TASKGRAPH_AGENT_ID": run.agent.id,
        }
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,  # own process group, so stop() reaches children
            )
        with self._lock:
            self._procs[run.id] = proc
        prompt = render_prompt(run, task).encode("utf-8")
        threading.Thread(
            target=self._watch, args=(run.id, proc, prompt, on_exit), daemon=True,
            name=f"run-{run.id}",
        ).start()
        return {"pid": proc.pid, "host": self.host, "log_path": str(log_path)}

    def _watch(self, run_id: str, proc: subprocess.Popen, prompt: bytes, on_exit: OnExit) -> None:
        try:
            proc.communicate(prompt)
        except (BrokenPipeError, OSError):
            proc.wait()
        code = proc.returncode
        with self._lock:
            self._procs.pop(run_id, None)
            stopped = run_id in self._stopping
            self._stopping.discard(run_id)
        if stopped:
            return  # stop() records the outcome
        on_exit("succeeded" if code == 0 else "failed", code, None)

    def stop(self, run: AgentRun) -> None:
        with self._lock:
            proc = self._procs.get(run.id)
            if proc is not None:
                self._stopping.add(run.id)
        if proc is not None:
            _terminate(proc.pid, self.grace_seconds, proc.wait)
        elif run.pid and run.host == self.host:
            _terminate(run.pid, self.grace_seconds, None)

    def is_alive(self, run: AgentRun) -> bool | None:
        with self._lock:
            proc = self._procs.get(run.id)
        if proc is not None:
            return proc.poll() is None
        if run.host != self.host or not run.pid:
            return None
        return _pid_alive(run.pid)

    def owns(self, run: AgentRun) -> bool:
        with self._lock:
            return run.id in self._procs


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate(pid: int, grace: float, wait: Callable[..., Any] | None) -> None:
    """SIGTERM the process group, SIGKILL it if it is still there after `grace`."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    if wait is not None:
        try:
            wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass
    else:  # not our child: poll until it is gone
        for _ in range(int(grace * 10)):
            if not _pid_alive(pid):
                return
            time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class RunManager:
    """Start / stop / restart / list agent runs on top of TaskGraphService."""

    def __init__(self, service: TaskGraphService, runner: Runner | None = None) -> None:
        self.service = service
        self.runner: Runner = runner if runner is not None else RecordRunner()

    # ---- control -------------------------------------------------------

    def start(
        self,
        actor: Actor,
        task_id: str,
        reason: str | None = None,
        *,
        agent_id: str = "worker",
        instructions: str | None = None,
        restart_of: str | None = None,
    ) -> AgentRun:
        self.reconcile()
        run = self.service.start_run(
            actor, task_id, agent=Actor(type="agent", id=agent_id), reason=reason,
            runner=self.runner.name, instructions=instructions, restart_of=restart_of,
        )
        task = self.service.state.tasks[task_id].model_copy(deep=True)

        def on_exit(status: RunStatus, code: int | None, note: str | None) -> None:
            self.service.end_run(run.agent, run.id, status, exit_code=code, note=note)

        try:
            meta = self.runner.start(run, task, on_exit)
        except Exception as e:  # launching failed: record it, then report
            self.service.end_run(actor, run.id, "failed", note=f"could not start: {e}")
            raise TaskGraphError("runner", f"could not start {run.id}: {e}") from None
        if meta:
            run = self.service.update_run(run.id, **meta)
        return self.service.run(run.id)

    def stop(self, actor: Actor, run_id: str, reason: str | None = None) -> AgentRun:
        run = self.service.run(run_id)
        if not run.active:
            raise TaskGraphError("conflict", f"run '{run_id}' already ended ({run.status})")
        if actor.type == "agent" and not (reason and reason.strip()):
            raise TaskGraphError("invalid", "agents must give a reason for every change")
        self.runner.stop(run)
        return self.service.end_run(actor, run_id, "stopped", note=reason, reason=reason)

    def restart(
        self,
        actor: Actor,
        task_id: str,
        reason: str | None = None,
        *,
        agent_id: str | None = None,
        instructions: str | None = None,
    ) -> AgentRun:
        """Stop the task's active run (if any) and start a fresh one. Agent and
        instructions carry over from the last run unless given."""
        previous = self.service.runs(task_id=task_id)
        last = previous[-1] if previous else None
        if last is not None and last.active:
            self.stop(actor, last.id, reason)
        return self.start(
            actor, task_id, reason,
            agent_id=agent_id or (last.agent.id if last else "worker"),
            instructions=instructions if instructions is not None else (last.instructions if last else None),
            restart_of=last.id if last else None,
        )

    def finish(
        self, actor: Actor, run_id: str, status: RunStatus, note: str | None = None
    ) -> AgentRun:
        """An agent reports how its run ended (succeeded / failed)."""
        if status not in ("succeeded", "failed"):
            raise TaskGraphError("invalid", "finish a run as 'succeeded' or 'failed'")
        run = self.service.run(run_id)
        if not run.active:
            raise TaskGraphError("conflict", f"run '{run_id}' already ended ({run.status})")
        return self.service.end_run(actor, run_id, status, note=note, reason=note)

    # ---- queries ---------------------------------------------------------

    def active(self) -> list[AgentRun]:
        self.reconcile()
        return self.service.runs(active=True)

    def reconcile(self) -> list[AgentRun]:
        """Mark runs whose process is gone (and nobody is watching) as 'lost'."""
        lost = []
        owns = getattr(self.runner, "owns", None)
        for run in self.service.runs(active=True):
            if run.runner != self.runner.name or (owns and owns(run)):
                continue
            if self.runner.is_alive(run) is False:
                lost.append(
                    self.service.end_run(
                        Actor(type="agent", id="runner"), run.id, "lost",
                        note="process ended while no server was watching; exit code unknown",
                    )
                )
        return lost


def runner_from_env() -> Runner:
    kind = os.environ.get("TASKGRAPH_RUNNER", "record")
    if kind == "record":
        return RecordRunner()
    if kind == "subprocess":
        cmd = os.environ.get("TASKGRAPH_RUNNER_CMD", "").strip()
        if not cmd:
            raise SystemExit("TASKGRAPH_RUNNER=subprocess needs TASKGRAPH_RUNNER_CMD")
        graph_path = Path(os.environ.get("TASKGRAPH_PATH", "data/taskgraph.jsonl"))
        log_dir = os.environ.get("TASKGRAPH_RUN_LOGS", str(graph_path.parent / "runs"))
        return SubprocessRunner(cmd, log_dir=log_dir)
    raise SystemExit(f"unknown TASKGRAPH_RUNNER '{kind}' (record | subprocess)")
