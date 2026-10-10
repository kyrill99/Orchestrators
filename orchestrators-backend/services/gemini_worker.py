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
4. If the server rejects the chained conversation itself (a 400 invalid_request
   on a request with previous_interaction_id, e.g. "Model turns with thought
   summaries must start with a thought block"), retrying cannot help: the run
   starts a fresh interaction from a recap (brief, scratchpad, artifacts, last
   tool calls, the tool results not yet delivered), at most MAX_RESEEDS times.

When the task's project has a repository (workspaces.py), the run first gets a
fresh git worktree from origin/main, the agent gets the code tools and the
repository rules (CODE), and finish_run(succeeded) lands its commits on main.

Runs live in this process. If it exits (self-update restarts the service),
the next process marks them 'lost' via RunManager.reconcile, and the engine
or a human restarts them; the new attempt's brief carries the scratchpad,
comments and what the previous attempt did.

Env: GEMINI_WORKER_MODEL (gemini-3.8-flash), GEMINI_WORKER_MAX_STEPS (40),
     GEMINI_WORKER_MAX_SECONDS (1200),
     GEMINI_WORKER_BUILTIN_TOOLS (google_search,url_context,code_execution; "" for none;
     url_context is dropped automatically if the API rejects the tool).
"""

from __future__ import annotations

import logging
import os
import re
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
from workspaces import RunWorkspace, Workspace, WorkspaceError

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
- You have no shell, and no file system unless your project has a repository (then see \
"Working on the repository"). You have the web: use google_search to find \
pages and url_context to open and read any URL (docs, a GitHub repo, an article). \
Use google_search for facts you are unsure of \
and code_execution for calculations. save_artifact stores files on your task.
- Split off follow-up work you will not do now with create_subtask.
- Only ask a human (request_decision) about strategy: goals, priorities, success \
criteria. Decide implementation details yourself. After asking, finish with outcome \
'failed' and say you are waiting for the answer.
- Use recall before asking anything memory might answer, and remember lasting lessons.
- Be efficient: you have a limited number of steps. Do not narrate; act.
- End with finish_run(outcome, summary) exactly once. 'succeeded' sends the task to \
human review.

## Your report

The owner reviews your work through one HTML report. Before finish_run, save it with \
save_artifact(filename="report.html", title=<what it is>, content=<the page>); that \
also lists it on the task.
- It holds the deliverable itself, not a summary of it: what was asked, what you did or \
found, the full result (comparison tables, recommendation and why, the text, the plan), \
sources (the pages google_search gave you, as links) and open questions.
- Self-contained: inline CSS; no external images, fonts or stylesheets. The page cannot \
fetch anything when viewed, so put all data in it. For charts prefer inline SVG or CSS \
bars; at most one library from https://cdn.jsdelivr.net if you need it.
- Concise: the owner should read it in under two minutes. Lead with the answer or \
result in 2-3 sentences, then only what supports it. Aim for about 300-500 words; use \
short bullets and one table instead of paragraphs; no filler, no restating the task, \
no generic background, no repeating the same point in several sections. Put long \
material (raw data, full text) in a collapsed <details> block or leave it out. Omit \
empty sections (e.g. no "Open questions" if there are none).
- Polished: clean typography, a readable column, tables for comparisons, light and dark \
mode, print-friendly. Keep the CSS small. Start from this skeleton and keep its style:

```html
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TITLE</title><base target="_blank">
<style>
:root{color-scheme:light dark;--fg:#1d1d1f;--muted:#6e6e73;--bg:#fff;--line:#e5e5ea;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--fg:#f2f2f4;--muted:#a1a1a8;--bg:#151517;--line:#2d2d32;--accent:#7aa7ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:780px;margin:0 auto;padding:48px 24px 64px}
h1{font-size:2rem;line-height:1.2;letter-spacing:-.02em;margin:0 0 .3em}
h2{font-size:1.15rem;margin:2.2em 0 .7em;padding-bottom:.35em;border-bottom:1px solid var(--line)}
.meta{color:var(--muted);font-size:.9rem}
.answer{border-left:3px solid var(--accent);padding:.7em 1em;border-radius:0 8px 8px 0;background:color-mix(in srgb,var(--accent) 8%,transparent)}
table{border-collapse:collapse;width:100%;font-size:.94rem}
th,td{text-align:left;vertical-align:top;padding:.55em .6em;border-bottom:1px solid var(--line)}
th{font-weight:600}a{color:var(--accent)}
@media print{main{padding:0;max-width:none}a{color:inherit}h2,tr{break-inside:avoid}}
</style></head><body><main>
<h1>TITLE</h1><p class="meta">TASK ID · DATE</p>
<p class="answer">The answer or outcome in two or three sentences.</p>
<h2>What was asked</h2>… <h2>What I did</h2>… <h2>Result</h2>…
<h2>Sources</h2>… <h2>Open questions</h2>…
</main></body></html>
```

- Write the HTML yourself and pass it straight to save_artifact. Do not print whole \
documents (HTML, code files) as chat messages, and do not assemble the report through \
code_execution; use code_execution only for computing data.
- Then finish_run with a short summary that names the report ("Report: <title>").
- Skip the report only when there is nothing to read: pure bookkeeping or restructuring \
the graph. Then say in the summary why there is no report."""

CODE = """## Working on the repository

Your task's project has a git repository. You work in your own checkout of it (a fresh \
worktree from the latest main; see "Repository" in the brief) with the code tools: \
list_files, search_code, read_file, edit_file, write_file, delete_file, git_status, \
git_diff, run_check and commit. There is no shell: run_check runs only the project's \
named checks.
- Investigate before you edit: find the relevant code with list_files and search_code and \
read it. Never guess what a file contains.
- Make small, focused changes that do the task and match the repository's style (naming, \
formatting, structure). No unrelated refactors, reformatting or drive-by fixes; file \
those as subtasks. Use edit_file for changes, write_file for new files.
- Run the relevant checks (run_check) after editing and fix what they report.
- Commit with a clear message (commit): a short imperative first line, details after a \
blank line if useful. A few focused commits at most.
- finish_run(succeeded) rebases your commits onto the latest main, runs the checks again \
and pushes them straight to main. If you cannot make it work, finish 'failed': nothing \
is pushed.
- Your report lists what changed: git_diff(stat_only=true) gives the files and +/- \
lines; one line per file on why.
- Never read, write or commit secrets or deployment credentials (.env files, keys, \
tokens, credentials in deploy configuration), and never weaken auth, security or \
deployment settings.
- The Orchestrators app itself: pushing to main does not deploy it. Production updates \
when someone presses Update, and then everything on main ships, so stay conservative: \
fix bugs with small, safe changes and keep the demo working."""

NUDGE = (
    "[engine] You replied without calling a tool. Continue the task with your tools, "
    "or call finish_run(outcome, summary) if you are done or cannot continue."
)
MAX_NUDGES = 2
MAX_RESEEDS = 2  # fresh conversations per run after the server-side chain breaks


def worker_instructions(code: bool = False) -> str:
    """Shared identity + worker role card (agent_identity/), then the rules of this
    runner (+ the repository rules when the run has a worktree)."""
    text = f"{system_prompt('worker')}\n\n## This runner\n\n{SYSTEM}"
    return f"{text}\n\n{CODE}" if code else text


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
        artifacts: Any = None,
        client_factory: Callable[[], Any] | None = None,
        model: str | None = None,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        builtin_tools: list[str] | None = None,
        retry_delays: tuple[float, ...] = (2.0, 6.0),
        workspace: Callable[[], Any] | None = None,
        project_id: str | None = None,
    ) -> None:
        self.service = service
        self.runlog = runlog
        self.scratchpads = scratchpads
        self.memory = memory
        self.issues = issues  # IssueService: workers can read and file issues
        self.artifacts = artifacts  # artifacts.ProjectArtifacts: reports saved on the task
        self.client_factory = client_factory or _default_client
        self.model = model or os.environ.get("GEMINI_WORKER_MODEL", MODEL)
        self.max_steps = max_steps if max_steps is not None else _env_int("GEMINI_WORKER_MAX_STEPS", 40)
        self.max_seconds = (
            max_seconds if max_seconds is not None else _env_int("GEMINI_WORKER_MAX_SECONDS", 1200)
        )
        if builtin_tools is None:
            raw = os.environ.get(
                "GEMINI_WORKER_BUILTIN_TOOLS", "google_search,url_context,code_execution"
            )
            builtin_tools = [t.strip() for t in raw.split(",") if t.strip()]
        self.builtin_tools = builtin_tools
        self.retry_delays = retry_delays
        # The project's repository config (workspaces.WorkspaceConfig) or None,
        # read when a run starts; main.py sets it per project.
        self.workspace = workspace
        if project_id is not None:
            self.project_id = project_id
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
        loop = None
        try:
            loop = _Loop(self, run, task, on_exit, live)
            loop.run()
        except Exception as e:  # last resort: never leave a run dangling
            log.exception("run %s crashed", run.id)
            self.runlog.add(run.id, run.task_id, "error", {"message": f"worker crashed: {e}"})
            if self.service.run(run.id).active:
                on_exit("failed", None, f"worker crashed: {e}")
        finally:
            with self._lock:
                self._live.pop(run.id, None)
                active = set(self._live)
            rw = loop.workspace if loop is not None else None
            if rw is not None:  # keep the newest keepRuns worktrees for debugging
                try:
                    rw.ws.cleanup(active=active)
                except Exception:  # noqa: BLE001 - disk housekeeping only
                    log.exception("worktree cleanup failed")


class _Loop:
    def __init__(self, runner: GeminiRunner, run: AgentRun, task: Task, on_exit: OnExit, live: _Live):
        self.r = runner
        self.run_ = run
        self.task = task
        self.on_exit = on_exit
        self.live = live
        self.workspace: RunWorkspace | None = None
        self.tokens = 0  # model tokens used by this run so far
        self.tools = self.make_tools()

    def make_tools(self) -> AgentTools:
        r, run, task = self.r, self.run_, self.task
        return AgentTools(
            r.memory, r.service, agent_id=run.agent.id,
            project_id=getattr(r, "project_id", None),  # set per project in main.py
            toolset=lambda ctx: worker_tools(ctx, run_id=run.id, task_id=task.id),
            runs=RunManager(r.service), scratchpads=r.scratchpads, runlog=r.runlog,
            issues=r.issues, artifacts=r.artifacts, workspace=self.workspace,
        )

    def open_workspace(self) -> bool:
        """A fresh worktree when the project has a repository. False: the run failed."""
        get = getattr(self.r, "workspace", None)
        try:
            config = get() if get is not None else None
        except Exception as e:  # noqa: BLE001
            log.warning("could not read the workspace config: %s", e)
            config = None
        if config is None:
            return True
        try:
            ws = Workspace(config, getattr(self.r, "project_id", None) or "default")
            previous = [p.id for p in reversed(self.r.service.runs(task_id=self.task.id))
                        if p.id != self.run_.id]
            self.workspace = ws.start_run(self.run_.id, self.task.id, previous)
        except (WorkspaceError, OSError) as e:
            self.end("failed", f"could not prepare the repository checkout: {truncate(str(e), 500)}")
            return False
        rw = self.workspace
        self.log("status", {"state": "workspace", "note": f"{config.remote} @ "
                            f"{(rw.base or 'empty repository')[:8]} on {rw.branch}",
                            "path": str(rw.path), "base": rw.base, "carried": rw.carried})
        self.tools = self.make_tools()
        return True

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
        self.log("status", {"state": "started", "model": r.model, "attempt": self.run_.attempt,
                            "maxSteps": r.max_steps, "maxSeconds": r.max_seconds})
        if not self.open_workspace():
            return
        tools = self.tools.declarations() + [{"type": t} for t in r.builtin_tools]
        client = r.client_factory()
        deadline = time.monotonic() + r.max_seconds
        base = {"model": r.model, "tools": tools,
                "system_instruction": worker_instructions(code=self.workspace is not None),
                "generation_config": {"thinking_summaries": "auto"}}
        input_: Any = build_brief(r.service, r.scratchpads, r.runlog, r.memory, self.run_, self.task,
                                  project_id=getattr(r, "project_id", None))
        if self.workspace is not None:
            input_ = input_.replace("\n\nDo the task now.", f"\n\n{repo_brief(self.workspace)}\n\nDo the task now.")
        brief_fn = getattr(r, "project_brief", None)  # set per project in main.py
        if brief_fn is not None and isinstance(input_, str):
            try:
                project_brief = brief_fn()
                if project_brief:
                    input_ = f"{project_brief}\n\n{input_}"
            except Exception:  # noqa: BLE001 - context only
                pass
        self.brief = input_ if isinstance(input_, str) else ""
        previous: str | None = None
        steps = nudges = reseeds = 0
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
            if isinstance(interaction, _ChainBroken):
                # The server-side conversation no longer accepts requests (e.g. a stored
                # model turn became invalid): start over from a recap.
                if reseeds >= MAX_RESEEDS:
                    return self.fail_api(interaction.error, f"after {reseeds} conversation resets")
                reseeds += 1
                self.log("status", {"state": "reseeded", "note": "conversation reset after API error; "
                                    f"continuing from a recap ({reseeds}/{MAX_RESEEDS})",
                                    "error": truncate(str(interaction.error), 500)})
                input_, previous = self.recap(input_), None
                continue  # counts as a step
            if interaction is None:
                return  # error recorded / run ended
            self.count_tokens(interaction)
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
        """interactions.create with retries for transient errors.

        Returns the interaction; None once the run has ended (error logged); or
        _ChainBroken when a chained request (previous_interaction_id) keeps being
        rejected as invalid, so the caller can start a fresh conversation."""
        delays = list(self.r.retry_delays)
        chained = bool(kwargs.get("previous_interaction_id"))
        bad_requests = 0
        while True:
            try:
                return client.interactions.create(**kwargs)
            except Exception as e:  # noqa: BLE001 - the SDK raises many types
                tools = kwargs.get("tools") or []
                if any(t.get("type") == "url_context" for t in tools if isinstance(t, dict)) \
                        and _is_bad_request(e) and _about_tools(e):
                    # Web page reading not accepted with this tool mix: keep going without it.
                    kwargs["tools"] = [t for t in tools if not (isinstance(t, dict) and t.get("type") == "url_context")]
                    self.r.builtin_tools = [t for t in self.r.builtin_tools if t != "url_context"]
                    self.log("error", {"message": f"url_context rejected, continuing without it: {e}"})
                    continue
                if chained and _is_bad_request(e) and not self.cancelled():
                    bad_requests += 1
                    # invalid_request is not transient; other 400s get one retry.
                    if _is_invalid_request(e) or bad_requests > 1:
                        self.log("error", {"message": f"Gemini rejected the conversation: {e}"})
                        return _ChainBroken(e)
                if not delays or self.cancelled():
                    self.log("error", {"message": f"Gemini request failed: {e}"})
                    if not self.cancelled():
                        self.fail_api(e)
                    return None
                delay = delays.pop(0)
                self.log("error", {"message": f"Gemini request failed, retrying in {delay:g} s: {e}"})
                self.live.cancel.wait(delay)

    def fail_api(self, error: Exception, context: str = "") -> None:
        """End the run after a fatal API error; say which artifacts it already saved."""
        note = f"Gemini request failed{' ' + context if context else ''}: {truncate(str(error), 300)}"
        mine = [a for a in self.saved_artifacts() if a.get("runId") in (None, self.run_.id)]
        if mine:
            names = ", ".join(a.get("filename") or a.get("title") or "?" for a in mine)
            note += f". API error after saving artifacts ({names}); restart to finish"
        self.end("failed", note)

    def saved_artifacts(self) -> list[dict[str, Any]]:
        """Artifacts on this task (store), or those this run saved (run log) without a store."""
        store = self.r.artifacts
        if store is not None:
            try:
                return list(store.list(self.task.id))
            except Exception:  # noqa: BLE001 - context only
                pass
        out: dict[str, dict[str, Any]] = {}
        for s in self.r.runlog.steps(self.run_.id, limit=5000):
            p = s["payload"]
            if s["kind"] == "tool_call" and p.get("name") == "save_artifact":
                args = p.get("args") or {}
                out[args.get("filename", "?")] = {"filename": args.get("filename"),
                                                  "title": args.get("title"), "runId": self.run_.id}
        return list(out.values())

    def recap(self, pending: Any) -> str:
        """The first message of a fresh conversation: the brief, the current state,
        what was done so far and the input the broken conversation did not take."""
        lines = ["[engine] The previous conversation was reset after an API error. You are "
                 "continuing the same run; the work below is already done and saved, so do not "
                 "redo it.", "", "## Original brief", truncate(self.brief, 8000) or "(see the task)"]
        pad = self.r.scratchpads.get(self.task.id)["content"].strip()
        lines += ["", "## Scratchpad now", truncate(pad, 6000) if pad else "(empty)"]
        arts = self.saved_artifacts()
        lines += ["", "## Artifacts saved on this task"]
        lines += [f"- {a.get('title') or '(untitled)'}: {a.get('filename')}"
                  + (" (this run)" if a.get("runId") == self.run_.id else "") for a in arts] or ["(none)"]
        if self.workspace is not None:  # code changes live in the worktree, not the conversation
            try:
                st = self.workspace.status()
                lines += ["", "## Your repository checkout now",
                          f"Branch {st['branch']} from {st['base']}.",
                          "Commits so far: " + ("; ".join(st["commitsAhead"]) or "none"),
                          "Uncommitted: " + (", ".join(st["uncommitted"][:40]) or "nothing")]
            except Exception:  # noqa: BLE001 - context only
                pass
        steps = self.r.runlog.steps(self.run_.id, limit=5000)
        calls = [s["payload"] for s in steps if s["kind"] == "tool_call"][-20:]
        lines += ["", "## Your last tool calls (oldest first)"]
        lines += [f"- {c.get('name')}({_brief_args(c.get('args'))})" for c in calls] or ["(none)"]
        said = [s["payload"].get("text", "") for s in steps if s["kind"] == "message"][-3:]
        if said:
            lines += ["", "## What you last said"] + [f"- {truncate(t, 400)}" for t in said]
        if isinstance(pending, list) and pending:
            lines += ["", "## Results of your last tool calls (not yet seen by you)"]
            for item in pending:
                if isinstance(item, dict):
                    lines.append(f"### {item.get('name')}\n{truncate(str(item.get('result', '')), 4000)}")
        elif isinstance(pending, str) and pending and pending != self.brief:
            lines += ["", "## Last message to you", truncate(pending, 2000)]
        lines += ["", "Continue from here with your tools. Save the report with save_artifact if it "
                  "is not saved yet, then call finish_run(outcome, summary)."]
        return "\n".join(lines)

    def count_tokens(self, interaction: Any) -> None:
        """Add the interaction's token usage (if the API reports it) to the run."""
        try:
            n = _usage_tokens(interaction)
            if n <= 0:
                return
            self.tokens += n
            self.r.service.update_run(self.run_.id, tokens=self.tokens)
        except Exception:  # noqa: BLE001 - usage is best effort
            log.debug("token usage not recorded", exc_info=True)

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


@dataclass
class _ChainBroken:
    """create(): the chained conversation is rejected; start a fresh one."""

    error: Exception


def _usage_tokens(interaction: Any) -> int:
    """Total tokens of one interaction, from whichever usage field the SDK provides."""
    usage = getattr(interaction, "usage", None) or getattr(interaction, "usage_metadata", None)
    if usage is None:
        return 0

    def num(*names: str) -> int:
        for name in names:
            v = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return int(v)
        return 0

    total = num("total_tokens", "total_token_count")
    if total:
        return total
    return (num("input_tokens", "total_input_tokens", "prompt_token_count")
            + num("output_tokens", "total_output_tokens", "candidates_token_count")
            + num("thought_tokens", "total_thought_tokens", "thoughts_token_count"))


def _is_bad_request(e: Exception) -> bool:
    code = getattr(e, "status_code", None)
    if code is not None:
        return code == 400
    msg = str(e)
    return bool(re.search(r"\b400\b", msg)) or "INVALID_ARGUMENT" in msg


def _is_invalid_request(e: Exception) -> bool:
    msg = str(e)
    return "invalid_request" in msg or "INVALID_ARGUMENT" in msg


def _about_tools(e: Exception) -> bool:
    """Is a 400 plausibly about the tool set (url_context not accepted) rather than
    about the conversation? e.g. "'url_context' and 'file_search' cannot be combined"."""
    msg = str(e).lower()
    if "url_context" in msg or "url context" in msg:
        return True
    return bool(re.search(r"\btools?\b", msg)) and bool(
        re.search(r"combin|unsupported|not supported|not allowed|not enabled|incompatible", msg))


def _brief_args(args: Any) -> str:
    """Key arguments of a logged tool call, without long bodies (content, code)."""
    if not isinstance(args, dict):
        return ""
    parts = []
    for k, v in args.items():
        v = v if isinstance(v, str) else str(v)
        parts.append(f"{k}=<{len(v)} chars>" if len(v) > 120 else f"{k}={v!r}")
    return ", ".join(parts)


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


def repo_brief(rw: RunWorkspace) -> str:
    """The brief's section on the run's repository checkout."""
    cfg = rw.ws.config
    lines = ["## Repository", f"Remote: {cfg.remote} (branch {cfg.branch})"]
    if rw.base:
        lines.append(f"Your worktree starts at {rw.base[:8]}, the latest origin/{cfg.branch}, "
                     f"on branch {rw.branch}.")
    else:
        lines.append("The repository is still empty: your commits will be its first.")
    lines += rw.notes
    if cfg.checks:
        lines.append("Checks (run_check):")
        lines += [f"- {c.name}: {c.description or c.command}" for c in cfg.checks]
    else:
        lines.append("No checks are configured: read your change over carefully before you commit.")
    return "\n".join(lines)


def build_brief(
    service: TaskGraphService,
    scratchpads: Scratchpads,
    runlog: RunLog,
    memory: MemoryService | None,
    run: AgentRun,
    task: Task,
    *,
    project_id: str | None = None,
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
            ctx = memory.context(project_id=project_id, query=task.title)
        except Exception:  # memory is a nice-to-have here
            ctx = {"text": ""}
        if ctx.get("text"):
            lines += ["", ctx["text"]]
    lines += ["", "Do the task now. Finish with finish_run."]
    return "\n".join(lines)
