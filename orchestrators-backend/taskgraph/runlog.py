"""Run activity log and task scratchpads, stored in the shared SQLite database.

    run_steps(run_id, seq, task_id, at, kind, payload_json)
        Everything an in-process worker does, recorded as it happens:
        thinking / message (model text), tool_call (name + args),
        tool_result (truncated), error, status, landing (commits pushed to the
        project repository, or why not; workspaces.py). `seq` counts per run from 1, so
        the GUI polls GET /runs/{id}/steps?after=<last seq>.

    scratchpads(task_id, content, updated_at, updated_by)
        One persistent markdown scratchpad per task. The worker reads and
        writes it; humans edit it on the task page. A restarted run picks up
        from it.

Both take an optional `notify(type, data)` callback, called after each write
(main.py publishes these on the "runs" SSE channel: "step" with the step,
"scratchpad" with {taskId, updatedAt, updatedBy}).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from db import Database

StepKind = Literal["thinking", "message", "tool_call", "tool_result", "error", "status", "landing"]
STEP_KINDS = ("thinking", "message", "tool_call", "tool_result", "error", "status", "landing")

MAX_SCRATCHPAD_CHARS = 100_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} more characters]"


Notify = Callable[[str, dict[str, Any]], None]


class RunLog:
    def __init__(self, db: Database, notify: Notify | None = None) -> None:
        self.db = db
        self.notify = notify
        with db.tx() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS run_steps (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq)
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS run_steps_task ON run_steps(task_id)")

    def add(self, run_id: str, task_id: str, kind: StepKind, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in STEP_KINDS:
            raise ValueError(f"unknown step kind {kind!r}")
        at = _now()
        body = json.dumps(payload, ensure_ascii=False, default=str)
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM run_steps WHERE run_id = ?", (run_id,)
            ).fetchone()
            seq = row[0] + 1
            conn.execute(
                "INSERT INTO run_steps (run_id, seq, task_id, at, kind, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, seq, task_id, at, kind, body),
            )
        step = {"runId": run_id, "seq": seq, "taskId": task_id, "at": at, "kind": kind, "payload": payload}
        if self.notify is not None:
            self.notify("step", step)
        return step

    def steps(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT run_id, seq, task_id, at, kind, payload_json FROM run_steps "
                "WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        return [
            {"runId": r[0], "seq": r[1], "taskId": r[2], "at": r[3], "kind": r[4],
             "payload": json.loads(r[5])}
            for r in rows
        ]

    def last(self, run_id: str, kind: StepKind | None = None) -> dict[str, Any] | None:
        sql = "SELECT seq, at, kind, payload_json FROM run_steps WHERE run_id = ?"
        args: list[Any] = [run_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        with self.db.read() as conn:
            r = conn.execute(sql + " ORDER BY seq DESC LIMIT 1", args).fetchone()
        if r is None:
            return None
        return {"seq": r[0], "at": r[1], "kind": r[2], "payload": json.loads(r[3])}


class Scratchpads:
    def __init__(self, db: Database, notify: Notify | None = None) -> None:
        self.db = db
        self.notify = notify
        with db.tx() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS scratchpads (
                    task_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )"""
            )

    def get(self, task_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            r = conn.execute(
                "SELECT content, updated_at, updated_by FROM scratchpads WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if r is None:
            return {"taskId": task_id, "content": "", "updatedAt": None, "updatedBy": None}
        return {"taskId": task_id, "content": r[0], "updatedAt": r[1], "updatedBy": r[2]}

    def write(self, task_id: str, content: str, by: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            out = self._put(conn, task_id, content, by)
        return self._changed(out)

    def append(self, task_id: str, text: str, by: str) -> dict[str, Any]:
        with self.db.tx() as conn:  # read-modify-write in one transaction
            r = conn.execute(
                "SELECT content FROM scratchpads WHERE task_id = ?", (task_id,)
            ).fetchone()
            current = r[0] if r else ""
            sep = "" if not current or current.endswith("\n") else "\n"
            out = self._put(conn, task_id, current + sep + text, by)
        return self._changed(out)

    def _changed(self, pad: dict[str, Any]) -> dict[str, Any]:
        if self.notify is not None:
            self.notify("scratchpad", {k: pad[k] for k in ("taskId", "updatedAt", "updatedBy")})
        return pad

    @staticmethod
    def _put(conn, task_id: str, content: str, by: str) -> dict[str, Any]:
        if len(content) > MAX_SCRATCHPAD_CHARS:
            raise ValueError(f"scratchpad is limited to {MAX_SCRATCHPAD_CHARS} characters")
        at = _now()
        conn.execute(
            "INSERT INTO scratchpads (task_id, content, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET content = excluded.content, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (task_id, content, at, by),
        )
        return {"taskId": task_id, "content": content, "updatedAt": at, "updatedBy": by}
