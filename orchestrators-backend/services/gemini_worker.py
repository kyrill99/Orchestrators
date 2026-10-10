"""GeminiRunner: executes task-graph runs with a Gemini agent, in-process.

One background thread per run drives a tool loop over the Gemini Interactions
API (google-genai `client.interactions.create`):

1. The first call gets the system instruction, the brief (task, scratchpad,
   recent comments, previous attempts, memory) and the worker tools
   (`services.agent_tools.worker_tools`) plus the built-in google_search and
   code_execution tools.
2. Each response is a list of steps. Thoughts, text, built-in tool calls and
   results are recorded in the run log as they arrive; function calls are
   executed against the shared services and their results sent back with
   `previous_interaction_id`, so the server keeps the conversation.
3. The loop ends when the agent calls finish_run, when the run is stopped
   (a flag checked between steps, or the run ended in the shared log), or
   when a budget runs out (GEMINI_WORKER_MAX_STEPS model calls,
   GEMINI_WORKER_MAX_SECONDS wall time); the last two fail the run.

Runs live in this process. If it exits (self-update restarts the service),
the next process marks them 'lost' via RunManager.reconcile, and the engine
or a human restarts them; the new attempt's brief carries the scratchpad,
comments and what the previous attempt did.

Env: GEMINI_WORKER_MODEL (gemini-3.8-flash), GEMINI_WORKER_MAX_STEPS (40),
     GEMINI_WORKER_MAX_SECONDS (1200),
     GEMINI_WORKER_BUILTIN_TOOLS (google_search,code_execution; "" for none).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_identity import system_prompt
from memory.service import MemoryService
from services.agent_tools import AgentTools, result_as_text, worker_tools
from taskgraph.models import AgentRun, RunStatus, Task
from taskgraph.runlog import RunLog, Scratchpads, truncate
from taskgraph.runs import OnExit, RunManager
from taskgraph.service import TaskGraphService

log = logging.getLogger("services.gemini_worker")

MODEL = "gemini-3.8-flash"
RESULT_LIMIT = 20_000  # characters of a tool result sent back to the model
LOG_LIMIT = 4_000  # characters of a tool result kept in the run log

SYSTEM = """You run in-process as a Gemini worker: you have been given one task. Do it \
now, end to end, using your tools, then call finish_run.

How to work here:
- Start by reading your scratchpad: it holds notes from earlier attempts. Continue from \
there instead of starting over.
- Keep the scratchpad current (write_scratchpad / append_scratchpad): plan, findings, \
results, open questions, next steps. It is how you, a later attempt and the humans pick \
up the work. Put substantial results there or in the task description, not only in chat.
- Post short progress notes with add_comment when you reach a milestone, and set \
progress with update_my_task.
- You have no shell and no file system. Use google_search for facts you are unsure of \
and code_execution for calculations.
- Split off follow-up work you will not do now with create_subtask.
- Only ask a human (request_decision) about strategy: goals, priorities, success \
criteria. Decide implementation details yourself. After asking, finish with outcome \
'failed' and say you are waiting for the answer.
- Use recall before asking anything memory might answer, and remember lasting lessons.
- Be efficient: you have a limited number of steps. Do not narrate; act.
- End with finish_run(outcome, summary) exactly once. 'succeeded' sends the task to \
human review."""

NUDGE = (
    "[engine] You replied without calling a tool. Continue the task with your tools, "
    "or call finish_run(outcome, summary) if you are done or cannot continue."
)
MAX_NUDGES = 2


def worker_instructions() -> str:
    """Shared identity + worker role card (agent_identity/), then the rules of this runner."""
    return f"{system_prompt('worker')}\n\n## This runner\n\n{SYSTEM}"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class _Live:
    run_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class GeminiRunner:
    name = "gemini"
    lost_note = "interrupted: the backend stopped while the run was in progress (restart to continue)"

    def __init__(
        self,
        service: TaskGraphService,
        runlog: RunLog,
        scratchpads: Scratchpads,
        *,
        memory: MemoryService | None = None,
        issues: Any = None,
        client_factory: Callable[[], Any] | None = None,
        model: str | None = None,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        builtin_tools: list[str] | None = None,
        retry_delays: tuple[float, ...] = (2.0, 6.0),
    ) -> None:
        self.service = service
        self.runlog = runlog
        self.scratchpads = scratchpads
        self.memory = memory
        self.issues = issues  # IssueService: workers can read and file issues
        self.client_factory = client_factory or _default_client
        self.model = model or os.environ.get("GEMINI_WORKER_MODEL", MODEL)
        self.max_steps = max_steps if max_steps is not None else _env_int("GEMINI_WORKER_MAX_STEPS", 40)
        self.max_seconds = (
            max_seconds if max_seconds is not None else _env_int("GEMINI_WORKER_MAX_SECONDS", 1200)
        )
        if builtin_tools is None:
            raw = os.environ.get("GEMINI_WORKER_BUILTIN_TOOLS", "google_search,code_execution")
            builtin_tools = [t.strip() for t in raw.split(",") if t.strip()]
        self.builtin_tools = builtin_tools
        self.retry_delays = retry_delays
        self.host = socket.gethostname()
        self._live: dict[str, _Live] = {}
        self._lock = threading.Lock()

    # ---- Runner protocol ---------------------------------------------------

    def start(self, run: AgentRun, task: Task, on_exit: OnExit) -> dict[str, Any]:
        live = _Live(run.id)
        live.thread = threading.Thread(
            target=self._thread, args=(run, task, on_exit, live), daemon=True, name=f"run-{run.id}"
        )
        with self._lock:
            self._live[run.id] = live
        live.thread.start()
        return {"pid": os.getpid(), "host": self.host}

    def stop(self, run: AgentRun) -> None:
        with self._lock:
            live = self._live.get(run.id)
        if live is not None:
            live.cancel.set()

    def is_alive(self, run: AgentRun) -> bool | None:
        if self.owns(run):
            return True
        if run.host != self.host or not run.pid:
            return None
        if run.pid == os.getpid():
            return False  # ours, but no thread runs it any more
        return _python_pid_alive(run.pid)

    def owns(self, run: AgentRun) -> bool:
        with self._lock:
            return run.id in self._live

    def join(self, run_id: str, timeout: float | None = None) -> bool:
        """Wait for a run's thread (tests). True if it has finished."""
        with self._lock:
            live = self._live.get(run_id)
        if live is None or live.thread is None:
            return True
        live.thread.join(timeout)
        return not live.thread.is_alive()

    # ---- the loop ----------------------------------------------------------

    def _thread(self, run: AgentRun, task: Task, on_exit: OnExit, live: _Live) -> None:
        try:
            _Loop(self, run, task, on_exit, live).run()
        except Exception as e:  # last resort: never leave a run dangling
            log.exception("run %s crashed", run.id)
            self.runlog.add(run.id, run.task_id, "error", {"message": f"worker crashed: {e}"})
            if self.service.run(run.id).active:
                on_exit("failed", None, f"worker crashed: {e}")
        finally:
            with self._lock:
                self._live.pop(run.id, None)


class _Loop:
    def __init__(self, runner: GeminiRunner, run: AgentRun, task: Task, on_exit: OnExit, live: _Live):
        self.r = runner
        self.run_ = run
        self.task = task
        self.on_exit = on_exit
        self.live = live
        self.tools = AgentTools(
            runner.memory, runner.service, agent_id=run.agent.id,
            toolset=lambda ctx: worker_tools(ctx, run_id=run.id, task_id=task.id),
            runs=RunManager(runner.service), scratchpads=runner.scratchpads, runlog=runner.runlog,
            issues=runner.issues,
        )

    def log(self, kind: str, payload: dict[str, Any]) -> None:
        self.r.runlog.add(self.run_.id, self.task.id, kind, payload)  # type: ignore[arg-type]

    def cancelled(self) -> bool:
        if self.live.cancel.is_set():
            return True
        return not self.r.service.run(self.run_.id).active  # stopped by another process

    def end(self, status: RunStatus, note: str) -> None:
        self.log("status", {"state": status, "note": note})
        if self.r.service.run(self.run_.id).active:
            self.on_exit(status, None, note)

    def run(self) -> None:
        r = self.r
        tools = self.tools.declarations() + [{"type": t} for t in r.builtin_tools]
        self.log("status", {"state": "started", "model": r.model, "attempt": self.run_.attempt,
                            "maxSteps": r.max_steps, "maxSeconds": r.max_seconds})
        client = r.client_factory()
        deadline = time.monotonic() + r.max_seconds
        base = {"model": r.model, "tools": tools, "system_instruction": worker_instructions(),
                "generation_config": {"thinking_summaries": "auto"}}
        input_: Any = build_brief(r.service, r.scratchpads, r.runlog, r.memory, self.run_, self.task)
        brief_fn = getattr(r, "project_brief", None)  # set per project in main.py
        if brief_fn is not None and isinstance(input_, str):
            try:
                project_brief = brief_fn()
                if project_brief:
                    input_ = f"{project_brief}\n\n{input_}"
            except Exception:  # noqa: BLE001 - context only
                pass
        previous: str | None = None
        steps = nudges = 0
        while True:
            if self.cancelled():
                self.log("status", {"state": "stopped", "note": "stopped between steps"})
                return
            if steps >= r.max_steps:
                return self.end("failed", f"budget: used all {r.max_steps} model steps without finishing")
            if time.monotonic() > deadline:
                return self.end("failed", f"budget: ran longer than {r.max_seconds} s without finishing")
            steps += 1
            kwargs = {**base, "input": input_}
            if previous:
                kwargs["previous_interaction_id"] = previous
            interaction = self.create(client, kwargs)
            if interaction is None:
                return  # error recorded / run ended
            if self.cancelled():
                self.log("status", {"state": "stopped", "note": "stopped while the model was working"})
                return
            previous = getattr(interaction, "id", None)
            calls = self.record(interaction)
            status = getattr(interaction, "status", None)
            if status in ("failed", "cancelled"):
                return self.end("failed", f"model interaction {status}")
            if not calls:
                nudges += 1
                if nudges > MAX_NUDGES:
                    return self.end("failed", "the agent stopped without calling finish_run")
                input_ = NUDGE
                continue
            results, finished = [], False
            for call in calls:
                if finished:
                    break  # nothing runs after finish_run
                if self.cancelled():
                    self.log("status", {"state": "stopped", "note": "stopped before a tool call"})
                    return
                name = getattr(call, "name", "") or ""
                args = getattr(call, "arguments", None) or {}
                call_id = getattr(call, "id", None) or name
                self.log("tool_call", {"name": name, "args": args, "callId": call_id})
                out = self.tools.dispatch(name, args)
                is_error = isinstance(out, dict) and set(out) == {"error"}
                self.log("tool_result", {"name": name, "callId": call_id, "isError": is_error,
                                         "result": truncate(result_as_text(out), LOG_LIMIT)})
                results.append({"type": "function_result", "call_id": call_id, "name": name,
                                "result": result_as_text(out, RESULT_LIMIT)})
                if name == "finish_run" and not is_error:
                    finished = True
            if finished:
                run = r.service.run(self.run_.id)
                self.log("status", {"state": run.status, "note": run.note})
                return
            input_ = results

    def create(self, client: Any, kwargs: dict[str, Any]) -> Any:
        """interactions.create with retries for transient errors."""
        delays = list(self.r.retry_delays)
        while True:
            try:
                return client.interactions.create(**kwargs)
            except Exception as e:  # noqa: BLE001 - the SDK raises many types
                if not delays or self.cancelled():
                    self.log("error", {"message": f"Gemini request failed: {e}"})
                    if not self.cancelled():
                        self.end("failed", f"Gemini request failed: {truncate(str(e), 300)}")
                    return None
                delay = delays.pop(0)
                self.log("error", {"message": f"Gemini request failed, retrying in {delay:g} s: {e}"})
                self.live.cancel.wait(delay)

    def record(self, interaction: Any) -> list[Any]:
        """Log the interaction's steps; return its function calls."""
        calls = []
        steps = getattr(interaction, "steps", None) or []
        for step in steps:
            kind = getattr(step, "type", "")
            if kind == "function_call":
                calls.append(step)
            elif kind == "thought":
                text = _texts(getattr(step, "summary", None))
                if text:
                    self.log("thinking", {"text": text})
            elif kind == "model_output":
                text = _texts(getattr(step, "content", None))
                if text:
                    self.log("message", {"text": text})
                err = getattr(step, "error", None)
                if err is not None:
                    self.log("error", {"message": str(getattr(err, "message", err))})
            elif kind.endswith("_call"):  # built-in tools: google_search, code_execution, ...
                self.log("tool_call", {"name": kind[: -len("_call")], "builtin": True,
                                       "args": _plain(getattr(step, "arguments", None)),
                                       "callId": getattr(step, "id", None)})
            elif kind.endswith("_result") and kind != "function_result":
                self.log("tool_result", {"name": kind[: -len("_result")], "builtin": True,
                                         "callId": getattr(step, "call_id", None),
                                         "isError": bool(getattr(step, "is_error", False)),
                                         "result": truncate(_builtin_result(step), LOG_LIMIT)})
        if not steps and getattr(interaction, "output_text", None):
            self.log("message", {"text": interaction.output_text})
        return calls


# ---- helpers -----------------------------------------------------------------


def _default_client() -> Any:
    from google import genai

    return genai.Client()


def _texts(items: Any) -> str:
    parts = []
    for item in items or []:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def _builtin_result(step: Any) -> str:
    result = getattr(step, "result", None)
    if isinstance(result, list):  # google_search: drop the rendered HTML suggestions
        items = []
        for item in result:
            d = _plain(item)
            if isinstance(d, dict):
                d = {k: v for k, v in d.items() if k not in ("search_suggestions", "rendered_content")}
            if d:
                items.append(d)
        if not items:  # the API only returns rendered suggestions; the model got the results
            return "(results passed to the model for grounding)"
        result = items
    return result_as_text(_plain(result))


def _python_pid_alive(pid: int) -> bool:
    """Is `pid` a live Python process (the server that started the run)?"""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return b"python" in fh.read()
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def build_brief(
    service: TaskGraphService,
    scratchpads: Scratchpads,
    runlog: RunLog,
    memory: MemoryService | None,
    run: AgentRun,
    task: Task,
) -> str:
    """The first message of a run: the task and everything needed to continue it."""
    detail = service.task(task.id)
    lines = [f"# Your task: {task.title}", f"Task id: `{task.id}` · run `{run.id}` · attempt {run.attempt}"]
    if task.parent_id and task.parent_id in service.state.tasks:
        parent = service.state.tasks[task.parent_id]
        lines += [f"Part of: {parent.title} (`{parent.id}`)"]
    lines += ["", "## Description", task.description.strip() or "(no description)"]
    if run.instructions:
        lines += ["", "## Instructions", run.instructions]
    if task.decision is not None:
        d = task.decision
        lines += ["", "## Decision", f"Question: {d.question}", f"State: {d.state}"]
        if d.chosen:
            lines += [f"Chosen: {d.chosen}", f"Rationale: {d.rationale or ''}"]
    upstream = []
    for bid in task.blocked_by:  # what the tasks this one waited for produced
        blocker = service.state.tasks.get(bid)
        if blocker is None:
            continue
        part = [f"### {blocker.title} (`{bid}`, {blocker.status})"]
        if blocker.decision and blocker.decision.state == "resolved":
            part.append(f"Decided: {blocker.decision.chosen}. {blocker.decision.rationale or ''}")
        done = [r for r in service.runs(task_id=bid) if r.status == "succeeded"]
        if done and done[-1].note:
            part.append(f"Result: {done[-1].note}")
        notes = scratchpads.get(bid)["content"].strip()
        if notes:
            part.append(f"Its scratchpad:\n{truncate(notes, 1500)}")
        upstream.append("\n".join(part))
    if upstream:
        lines += ["", "## What the tasks before this one produced", *upstream]
    pad = scratchpads.get(task.id)
    lines += ["", "## Scratchpad (your notes so far)",
              pad["content"].strip() or "(empty: this is the first attempt or nothing was noted)"]
    comments = [a for a in detail.get("activity", []) if a.get("op") == "comment.add"][-10:]
    if comments:
        lines += ["", "## Recent comments"]
        lines += [f"- {a['at']}: {a['text']}" for a in comments]
    previous = [r for r in service.runs(task_id=task.id) if r.id != run.id][-3:]
    if previous:
        lines += ["", "## Previous attempts"]
        for p in previous:
            lines.append(f"- attempt {p.attempt} ({p.id}): {p.status}" + (f": {p.note}" if p.note else ""))
        last = previous[-1]
        if last.status in ("lost", "stopped"):
            msg = runlog.last(last.id, "message")
            if msg:
                lines.append(f"  Last thing it said: {truncate(msg['payload'].get('text', ''), 800)}")
    children = [service.state.tasks[c] for c in detail.get("children", []) if c in service.state.tasks]
    if children:
        lines += ["", "## Subtasks"]
        lines += [f"- `{c.id}` {c.title}: {c.status}" for c in children]
    if memory is not None:
        try:
            ctx = memory.context(query=task.title)
        except Exception:  # memory is a nice-to-have here
            ctx = {"text": ""}
        if ctx.get("text"):
            lines += ["", ctx["text"]]
    lines += ["", "Do the task now. Finish with finish_run."]
    return "\n".join(lines)
