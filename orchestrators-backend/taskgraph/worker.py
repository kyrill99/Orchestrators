"""Minimal worker agent, launched once per run by the subprocess runner.

    TASKGRAPH_RUNNER=subprocess
    TASKGRAPH_RUNNER_CMD="python -m taskgraph.worker"

Reads the task brief on stdin, looks at what the tasks it depended on produced,
asks Gemini for the deliverable, posts it as a comment, saves it as an artifact
under data/artifacts/ and exits 0. The runner then records the run as succeeded
and the task moves to in-review for the owner to approve or send back.
Without GEMINI_API_KEY it writes a clearly marked placeholder so the loop still
runs in a demo.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

MODEL = os.environ.get("WORKER_MODEL", "gemini-3.8-flash")
MAX_COMMENT = 4000


def _upstream_results(svc, task: dict) -> str:
    """Last worker comment on each blocker: what earlier tasks produced."""
    parts = []
    for bid in task.get("blockedBy", []):
        try:
            blocker = svc.task(bid)
        except Exception:
            continue
        results = [a for a in blocker.get("activity", []) if a.get("op") == "comment.add"
                   and a.get("actor", {}).get("type") == "agent"]
        decision = blocker.get("decision") or {}
        if decision.get("state") == "resolved":
            parts.append(f"### {blocker['title']} (decision)\nChosen: {decision.get('chosen')}\n"
                         f"Rationale: {decision.get('rationale')}")
        elif results:
            parts.append(f"### {blocker['title']}\n{results[-1]['text']}")
    return "\n\n".join(parts)


def _comments(task: dict) -> str:
    notes = [a["text"] for a in task.get("activity", []) if a.get("op") == "comment.add"]
    return "\n".join(f"- {n}" for n in notes[-5:])


def _generate(brief: str, upstream: str, comments: str) -> str:
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return ("(placeholder: GEMINI_API_KEY is not set on the server, so no model ran)\n\n"
                "Planned approach for this task based on the brief:\n"
                + "\n".join(f"- {line}" for line in brief.splitlines()[:3] if line.strip()))
    from google import genai

    client = genai.Client()
    prompt = "\n\n".join(p for p in [
        brief,
        f"## What the tasks before this one produced\n{upstream}" if upstream else "",
        f"## Earlier comments on this task (including reviewer feedback)\n{comments}" if comments else "",
        "## Your output\nDo the task now as far as you can in writing (you have no tools "
        "or code execution in this run). Produce the deliverable itself as concise markdown, "
        "at most ~350 words: findings, plan, or result. Make decisions yourself in the spirit "
        "of the strategy and say why in one line each. End with a line starting 'Checked:' "
        "saying how you checked it, and one starting 'Next:' with what the next task needs.",
    ] if p)
    interaction = client.interactions.create(
        model=MODEL, input=prompt, generation_config={"thinking_level": "low"},
    )
    text = (getattr(interaction, "output_text", None) or "").strip()
    if not text:
        for step in getattr(interaction, "steps", None) or getattr(interaction, "outputs", None) or []:
            if getattr(step, "text", None):
                text = str(step.text).strip()
    if not text:
        raise RuntimeError("model returned no text")
    return text


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    os.chdir(here)
    sys.path.insert(0, str(here))
    load_dotenv(here / ".env")

    from storage import open_taskgraph_store
    from taskgraph import Actor, TaskGraphService
    from taskgraph.models import AddComment, Artifact, UpdateTask

    task_id = os.environ["TASKGRAPH_TASK_ID"]
    run_id = os.environ.get("TASKGRAPH_RUN_ID", "run")
    me = Actor(type="agent", id=os.environ.get("TASKGRAPH_AGENT_ID", "worker"))
    brief = sys.stdin.read()
    project_db = os.environ.get("TASKGRAPH_DB")
    if project_db:  # a non-default project: its own store (docs/architecture/projects.md)
        from db import Database
        from taskgraph.sqlite_store import SqliteEventStore

        svc = TaskGraphService(SqliteEventStore(Database(project_db)))
    else:
        svc = TaskGraphService(open_taskgraph_store())
    try:
        from projects import ProjectRegistry
        from storage import default_db

        project_brief = ProjectRegistry(default_db()).brief(os.environ.get("TASKGRAPH_PROJECT") or "default")
        if project_brief:
            brief = f"{project_brief}\n\n{brief}"
    except Exception:  # noqa: BLE001 - the brief is context, not required
        traceback.print_exc()

    try:
        task = svc.task(task_id)
        output = _generate(brief, _upstream_results(svc, task), _comments(task))
        out_dir = Path(os.environ.get("WORKER_ARTIFACTS", "data/artifacts"))
        out_dir.mkdir(parents=True, exist_ok=True)
        rel = str(out_dir / f"{task_id}-{run_id}.md")
        Path(rel).write_text(f"# {task['title']}\n\n{output}\n", encoding="utf-8")
        artifacts = [Artifact.model_validate(a) for a in task.get("artifacts", [])]
        artifacts.append(Artifact(path=rel, label=f"{run_id} result"))
        body = output if len(output) <= MAX_COMMENT else output[:MAX_COMMENT] + f"\n\n… (full result: {rel})"
        svc.submit(me, [
            AddComment(task_id=task_id, body=body),
            UpdateTask(task_id=task_id, artifacts=artifacts, progress=1.0),
        ], reason=f"result of {run_id}")
        print(f"posted result for {task_id} ({len(output)} chars) -> {rel}")
        return 0
    except Exception as e:  # report on the task, then fail the run
        traceback.print_exc()
        try:
            svc.submit(me, [AddComment(task_id=task_id, body=f"Run {run_id} failed: {e}")],
                       reason="worker error")
        except Exception:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
