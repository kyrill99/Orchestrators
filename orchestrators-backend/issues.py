"""Issue list: bug reports and feature requests from people and agents.

Mount with:

    issues = IssueService(db)
    app.include_router(build_router(issues))

Everyone signed in sees and edits every issue; there are no permissions.
Authors are recorded as an email + name: people come from oauth2-proxy
(identity.current_user), agents act as `agent:<id>` (see agent_author).

Lifecycle: draft -> open <-> in_progress -> closed (reopen -> open).
Drafts have no number yet; they get the next sequential number when they
are published. Only drafts can be deleted ("discard"); published issues are
closed instead, so history is never lost.

Screenshots are stored as files under ISSUES_PATH/<issue id>/ (default
data/issues, which persists across deploys) with metadata in SQLite.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from db import Database
from identity import current_user

DEFAULT_FILES_PATH = "data/issues"
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
STATUSES = ("draft", "open", "in_progress", "closed")
PUBLISHED = ("open", "in_progress", "closed")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

IssueStatus = Literal["draft", "open", "in_progress", "closed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    number INTEGER UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    author_email TEXT NOT NULL,
    author_name TEXT NOT NULL,
    assignee TEXT,
    labels TEXT NOT NULL DEFAULT '[]',
    context TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by_email TEXT,
    closed_by_name TEXT,
    close_reason TEXT
);
CREATE INDEX IF NOT EXISTS issues_status ON issues(status, created_at);
CREATE TABLE IF NOT EXISTS issue_attachments (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    author_email TEXT NOT NULL,
    author_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS issue_attachments_issue ON issue_attachments(issue_id);
CREATE TABLE IF NOT EXISTS issue_comments (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    author_email TEXT NOT NULL,
    author_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS issue_comments_issue ON issue_comments(issue_id, created_at);
"""


class IssueError(Exception):
    """code: not_found | conflict | invalid | too_large"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Author:
    email: str
    name: str

    def dump(self) -> dict[str, str]:
        return {"email": self.email, "name": self.name}


def agent_author(agent_id: str) -> Author:
    return Author(email=f"agent:{agent_id}", name=agent_id)


def sniff_image(data: bytes) -> str | None:
    """Content type from the file's magic bytes (png, jpeg, webp), else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clean_labels(labels: list[str] | None) -> list[str]:
    out: list[str] = []
    for label in labels or []:
        label = str(label).strip()
        if label and label not in out:
            out.append(label)
    return out


def attachment_url(issue_id: str, attachment_id: str) -> str:
    return f"/api/issues/{issue_id}/attachments/{attachment_id}"


class IssueService:
    # Called with the issue dict whenever an issue becomes open (created open or
    # a draft published); main.py uses it to file a task in the Orchestrators project.
    on_open: Callable[[dict[str, Any]], None] | None = None

    def __init__(self, db: Database, files_path: str | Path | None = None):
        self.db = db
        self.files = Path(files_path or os.environ.get("ISSUES_PATH", DEFAULT_FILES_PATH))
        with db.tx() as c:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    c.execute(statement)

    # ---- reads ----
    def resolve(self, ref: str | int) -> str:
        """Issue id from an id, a number (12) or '#12'."""
        text = str(ref).strip()
        with self.db.read() as c:
            if re.fullmatch(r"#?\d+", text):
                row = c.execute("SELECT id FROM issues WHERE number = ?", (int(text.lstrip("#")),)).fetchone()
            else:
                row = c.execute("SELECT id FROM issues WHERE id = ?", (text,)).fetchone()
        if row is None:
            raise IssueError("not_found", f"issue '{text}' not found")
        return row["id"]

    def list_issues(
        self,
        status: list[str] | str | None = None,
        search: str | None = None,
        author: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """status: one or more statuses, 'all', or None for every published issue."""
        if status is None:
            statuses = list(PUBLISHED)
        elif isinstance(status, str):
            statuses = list(STATUSES) if status == "all" else [s for s in status.split(",") if s]
        else:
            statuses = list(status)
        bad = [s for s in statuses if s not in STATUSES]
        if bad:
            raise IssueError("invalid", f"unknown status {bad[0]!r}")
        where = [f"status IN ({','.join('?' * len(statuses))})"]
        params: list[Any] = list(statuses)
        if search and search.strip():
            q = search.strip()
            number = int(q.lstrip("#")) if re.fullmatch(r"#?\d+", q) else -1
            where.append("(number = ? OR title LIKE ? OR description LIKE ?)")
            params += [number, f"%{q}%", f"%{q}%"]
        if author:
            where.append("author_email = ?")
            params.append(author.strip().lower())
        if assignee:
            where.append("assignee = ?")
            params.append(assignee.strip())
        if label:
            where.append("EXISTS (SELECT 1 FROM json_each(labels) WHERE value = ?)")
            params.append(label.strip())
        params.append(max(1, min(int(limit), 1000)))
        with self.db.read() as c:
            rows = c.execute(
                f"SELECT * FROM issues WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                params,
            ).fetchall()
            ids = [r["id"] for r in rows]
            atts = self._attachments(c, ids)
            counts = self._comment_counts(c, ids)
        return [
            self._issue(r, atts.get(r["id"], []), comment_count=counts.get(r["id"], 0))
            for r in rows
        ]

    def get(self, ref: str | int) -> dict[str, Any]:
        issue_id = self.resolve(ref)
        with self.db.read() as c:
            row = c.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
            atts = self._attachments(c, [issue_id]).get(issue_id, [])
            comments = [
                self._comment(r)
                for r in c.execute(
                    "SELECT * FROM issue_comments WHERE issue_id = ? ORDER BY created_at, rowid",
                    (issue_id,),
                ).fetchall()
            ]
        out = self._issue(row, atts, comment_count=len(comments))
        out["comments"] = comments
        return out

    def attachment(self, ref: str | int, attachment_id: str) -> tuple[dict[str, Any], Path]:
        issue_id = self.resolve(ref)
        with self.db.read() as c:
            row = c.execute(
                "SELECT * FROM issue_attachments WHERE id = ? AND issue_id = ?",
                (attachment_id, issue_id),
            ).fetchone()
        if row is None:
            raise IssueError("not_found", f"attachment '{attachment_id}' not found")
        meta = self._attachment(row)
        return meta, self._file(issue_id, row["id"], row["content_type"])

    def attachment_path(self, meta: dict[str, Any]) -> Path:
        """Where an attachment (as returned in an issue) is stored on disk."""
        return self._file(meta["issue_id"], meta["id"], meta["content_type"])

    # ---- writes ----
    def create(
        self,
        author: Author,
        title: str = "",
        description: str = "",
        status: Literal["draft", "open"] = "open",
        labels: list[str] | None = None,
        assignee: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in ("draft", "open"):
            raise IssueError("invalid", "new issues are 'draft' or 'open'")
        title = (title or "").strip()
        if status == "open" and not title:
            raise IssueError("invalid", "title is required")
        issue_id = _new_id("iss")
        now = _now()
        with self.db.tx() as c:
            number = self._next_number(c) if status != "draft" else None
            c.execute(
                "INSERT INTO issues (id, number, title, description, status, author_email,"
                " author_name, assignee, labels, context, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id, number, title, description or "", status, author.email,
                    author.name, (assignee or "").strip() or None,
                    json.dumps(_clean_labels(labels)), json.dumps(context or {}), now, now,
                ),
            )
        issue = self.get(issue_id)
        if status == "open":
            self._opened(issue)
        return issue

    def _opened(self, issue: dict[str, Any]) -> None:
        if self.on_open is None:
            return
        try:
            self.on_open(issue)
        except Exception:  # noqa: BLE001 - filing the task is best effort
            import logging

            logging.getLogger(__name__).warning("on_open hook failed", exc_info=True)

    def update(
        self,
        author: Author,
        ref: str | int,
        *,
        title: str | None = None,
        description: str | None = None,
        status: IssueStatus | None = None,
        labels: list[str] | None = None,
        assignee: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Change fields. status moves between open and in_progress; use
        publish / close / reopen for the other transitions. assignee '' unassigns."""
        issue_id = self.resolve(ref)
        current = self._row(issue_id)
        sets: dict[str, Any] = {}
        if title is not None:
            if not title.strip() and current["status"] != "draft":
                raise IssueError("invalid", "title is required")
            sets["title"] = title.strip()
        if description is not None:
            sets["description"] = description
        if labels is not None:
            sets["labels"] = json.dumps(_clean_labels(labels))
        if assignee is not None:
            sets["assignee"] = assignee.strip() or None
        if context is not None:
            sets["context"] = json.dumps(context)
        if status is not None and status != current["status"]:
            if current["status"] not in ("open", "in_progress") or status not in ("open", "in_progress"):
                hint = {"open": "publish or reopen", "closed": "close", "draft": "-"}.get(status, "-")
                raise IssueError(
                    "conflict",
                    f"cannot move a {current['status']} issue to {status} with update"
                    + (f"; use {hint}" if hint != "-" else ""),
                )
            sets["status"] = status
        return self._set(issue_id, sets)

    def publish(self, author: Author, ref: str | int) -> dict[str, Any]:
        issue_id = self.resolve(ref)
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
            if row["status"] != "draft":
                raise IssueError("conflict", f"issue is {row['status']}, not a draft")
            if not row["title"].strip():
                raise IssueError("invalid", "title is required")
            c.execute(
                "UPDATE issues SET status = 'open', number = ?, created_at = ?, updated_at = ?"
                " WHERE id = ?",
                (self._next_number(c), _now(), _now(), issue_id),
            )
        issue = self.get(issue_id)
        self._opened(issue)
        return issue

    def close(self, author: Author, ref: str | int, reason: str | None = None) -> dict[str, Any]:
        issue_id = self.resolve(ref)
        status = self._row(issue_id)["status"]
        if status == "draft":
            raise IssueError("conflict", "drafts are discarded, not closed")
        if status == "closed":
            raise IssueError("conflict", "issue is already closed")
        now = _now()
        return self._set(issue_id, {
            "status": "closed", "closed_at": now, "closed_by_email": author.email,
            "closed_by_name": author.name, "close_reason": (reason or "").strip() or None,
        })

    def reopen(self, author: Author, ref: str | int) -> dict[str, Any]:
        issue_id = self.resolve(ref)
        if self._row(issue_id)["status"] != "closed":
            raise IssueError("conflict", "only closed issues can be reopened")
        return self._set(issue_id, {
            "status": "open", "closed_at": None, "closed_by_email": None,
            "closed_by_name": None, "close_reason": None,
        })

    def delete(self, author: Author, ref: str | int) -> None:
        """Discard a draft with its screenshots. Published issues are closed instead."""
        issue_id = self.resolve(ref)
        with self.db.tx() as c:
            row = c.execute("SELECT status FROM issues WHERE id = ?", (issue_id,)).fetchone()
            if row["status"] != "draft":
                raise IssueError("conflict", "only drafts can be deleted; close the issue instead")
            c.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
        shutil.rmtree(self.files / issue_id, ignore_errors=True)

    def add_comment(self, author: Author, ref: str | int, body: str) -> dict[str, Any]:
        issue_id = self.resolve(ref)
        if not (body or "").strip():
            raise IssueError("invalid", "comment body is required")
        comment_id = _new_id("cmt")
        now = _now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO issue_comments (id, issue_id, author_email, author_name, body, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (comment_id, issue_id, author.email, author.name, body.strip(), now),
            )
            c.execute("UPDATE issues SET updated_at = ? WHERE id = ?", (now, issue_id))
            row = c.execute("SELECT * FROM issue_comments WHERE id = ?", (comment_id,)).fetchone()
        return self._comment(row)

    def add_attachment(
        self, author: Author, ref: str | int, filename: str, data: bytes
    ) -> dict[str, Any]:
        """Store a png/jpeg/webp screenshot (type checked from the bytes, max 5 MB)."""
        issue_id = self.resolve(ref)
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise IssueError("too_large", f"attachments are limited to {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
        if not data:
            raise IssueError("invalid", "empty file")
        content_type = sniff_image(data)
        if content_type is None:
            raise IssueError("invalid", "only png, jpeg and webp images are accepted")
        attachment_id = _new_id("att")
        path = self._file(issue_id, attachment_id, content_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        name = Path(filename or "").name.strip()[:200] or f"screenshot.{IMAGE_TYPES[content_type]}"
        now = _now()
        try:
            with self.db.tx() as c:
                c.execute(
                    "INSERT INTO issue_attachments (id, issue_id, filename, content_type, size,"
                    " author_email, author_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (attachment_id, issue_id, name, content_type, len(data), author.email, author.name, now),
                )
                c.execute("UPDATE issues SET updated_at = ? WHERE id = ?", (now, issue_id))
                row = c.execute("SELECT * FROM issue_attachments WHERE id = ?", (attachment_id,)).fetchone()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return self._attachment(row)

    def delete_attachment(self, author: Author, ref: str | int, attachment_id: str) -> None:
        meta, path = self.attachment(ref, attachment_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM issue_attachments WHERE id = ?", (attachment_id,))
            c.execute("UPDATE issues SET updated_at = ? WHERE id = ?", (_now(), meta["issue_id"]))
        path.unlink(missing_ok=True)

    # ---- helpers ----
    def _next_number(self, c) -> int:
        return c.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM issues").fetchone()[0]

    def _row(self, issue_id: str):
        with self.db.read() as c:
            return c.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()

    def _set(self, issue_id: str, sets: dict[str, Any]) -> dict[str, Any]:
        if sets:
            sets["updated_at"] = _now()
            cols = ", ".join(f"{k} = ?" for k in sets)
            with self.db.tx() as c:
                c.execute(f"UPDATE issues SET {cols} WHERE id = ?", [*sets.values(), issue_id])
        return self.get(issue_id)

    def _file(self, issue_id: str, attachment_id: str, content_type: str) -> Path:
        return self.files / issue_id / f"{attachment_id}.{IMAGE_TYPES[content_type]}"

    def _attachments(self, c, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not ids:
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        rows = c.execute(
            f"SELECT * FROM issue_attachments WHERE issue_id IN ({','.join('?' * len(ids))})"
            " ORDER BY created_at, rowid",
            ids,
        ).fetchall()
        for r in rows:
            out.setdefault(r["issue_id"], []).append(self._attachment(r))
        return out

    def _comment_counts(self, c, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        rows = c.execute(
            f"SELECT issue_id, count(*) AS n FROM issue_comments WHERE issue_id IN ({','.join('?' * len(ids))})"
            " GROUP BY issue_id",
            ids,
        ).fetchall()
        return {r["issue_id"]: r["n"] for r in rows}

    def _attachment(self, r) -> dict[str, Any]:
        return {
            "id": r["id"],
            "issue_id": r["issue_id"],
            "filename": r["filename"],
            "content_type": r["content_type"],
            "size": r["size"],
            "author": {"email": r["author_email"], "name": r["author_name"]},
            "created_at": r["created_at"],
            "url": attachment_url(r["issue_id"], r["id"]),
        }

    @staticmethod
    def _comment(r) -> dict[str, Any]:
        return {
            "id": r["id"],
            "issue_id": r["issue_id"],
            "author": {"email": r["author_email"], "name": r["author_name"]},
            "body": r["body"],
            "created_at": r["created_at"],
        }

    @staticmethod
    def _issue(r, attachments: list[dict[str, Any]], comment_count: int = 0) -> dict[str, Any]:
        return {
            "id": r["id"],
            "number": r["number"],
            "title": r["title"],
            "description": r["description"],
            "status": r["status"],
            "author": {"email": r["author_email"], "name": r["author_name"]},
            "assignee": r["assignee"],
            "labels": json.loads(r["labels"] or "[]"),
            "context": json.loads(r["context"] or "{}"),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "closed_at": r["closed_at"],
            "closed_by": (
                {"email": r["closed_by_email"], "name": r["closed_by_name"]}
                if r["closed_by_email"] else None
            ),
            "close_reason": r["close_reason"],
            "attachments": attachments,
            "comment_count": comment_count,
        }


def service_from_env() -> IssueService:
    """For processes other than the API (the MCP server): same DB and files."""
    from storage import default_db

    return IssueService(default_db())


# ---------------------------------------------------------------- REST

STATUS_FOR_CODE = {"not_found": 404, "conflict": 409, "invalid": 422, "too_large": 413}


class CreateBody(BaseModel):
    title: str = ""
    description: str = ""
    status: Literal["draft", "open"] = "open"
    labels: list[str] = Field(default_factory=list)
    assignee: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class UpdateBody(BaseModel):
    title: str | None = None
    description: str | None = None
    status: IssueStatus | None = None
    labels: list[str] | None = None
    assignee: str | None = None
    context: dict[str, Any] | None = None


class CloseBody(BaseModel):
    reason: str | None = None


class CommentBody(BaseModel):
    body: str = Field(..., min_length=1)


def build_router(service: IssueService) -> APIRouter:
    r = APIRouter(prefix="/api/issues", tags=["issues"])

    def me(request: Request) -> Author:
        user = current_user(request)
        return Author(email=user.email, name=user.name)

    def call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except IssueError as e:
            raise HTTPException(STATUS_FOR_CODE.get(e.code, 400), {"code": e.code, "message": str(e)}) from None

    @r.get("")
    def list_issues(
        request: Request,
        status: str | None = Query(None, description="Comma-separated statuses, or 'all'. Default: every published issue."),
        q: str | None = Query(None, description="Search title, description or #number"),
        author: str | None = Query(None, description="Author email, or 'me'"),
        assignee: str | None = None,
        label: str | None = None,
        limit: int = 200,
    ):
        if author == "me":
            author = me(request).email
        return call(service.list_issues, status=status, search=q, author=author, assignee=assignee, label=label, limit=limit)

    @r.post("", status_code=201)
    def create_issue(body: CreateBody, request: Request):
        return call(service.create, me(request), **body.model_dump())

    @r.get("/{ref}")
    def get_issue(ref: str):
        return call(service.get, ref)

    @r.patch("/{ref}")
    def update_issue(ref: str, body: UpdateBody, request: Request):
        return call(service.update, me(request), ref, **body.model_dump(exclude_unset=True))

    @r.delete("/{ref}", status_code=204)
    def delete_issue(ref: str, request: Request):
        call(service.delete, me(request), ref)

    @r.post("/{ref}/publish")
    def publish_issue(ref: str, request: Request):
        return call(service.publish, me(request), ref)

    @r.post("/{ref}/close")
    def close_issue(ref: str, request: Request, body: CloseBody | None = None):
        return call(service.close, me(request), ref, body.reason if body else None)

    @r.post("/{ref}/reopen")
    def reopen_issue(ref: str, request: Request):
        return call(service.reopen, me(request), ref)

    @r.get("/{ref}/comments")
    def list_comments(ref: str):
        return call(service.get, ref)["comments"]

    @r.post("/{ref}/comments", status_code=201)
    def add_comment(ref: str, body: CommentBody, request: Request):
        return call(service.add_comment, me(request), ref, body.body)

    @r.post("/{ref}/attachments", status_code=201)
    async def upload_attachment(ref: str, request: Request, file: UploadFile = File(...)):
        data = await file.read(MAX_ATTACHMENT_BYTES + 1)
        if file.content_type and file.content_type not in IMAGE_TYPES:
            raise HTTPException(422, {"code": "invalid", "message": "only png, jpeg and webp images are accepted"})
        return call(service.add_attachment, me(request), ref, file.filename or "", data)

    @r.get("/{ref}/attachments/{attachment_id}")
    def download_attachment(ref: str, attachment_id: str):
        meta, path = call(service.attachment, ref, attachment_id)
        if not path.exists():
            raise HTTPException(404, {"code": "not_found", "message": "file is missing"})
        return FileResponse(
            path,
            media_type=meta["content_type"],
            filename=meta["filename"],
            content_disposition_type="inline",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )

    @r.delete("/{ref}/attachments/{attachment_id}", status_code=204)
    def delete_attachment(ref: str, attachment_id: str, request: Request):
        call(service.delete_attachment, me(request), ref, attachment_id)

    return r


# ---------------------------------------------------------------- MCP

MCP_INSTRUCTIONS = """Issues: bug reports and requests filed by people (often with a
screenshot) and by agents. list_issues / get_issue to read (refer to an issue by
id or by number like '#12'); update_issue to change title, description, labels,
assignee or move it between open and in_progress; comment_on_issue to report
progress or findings; close_issue with a reason when it is fixed or won't be;
reopen_issue if it comes back. get_issue_screenshot returns an attachment image."""


def register_mcp_tools(server, service: IssueService, agent_id: str = "planner") -> None:
    """Issue tools on an MCPServer. Names are issue-specific (list_issues,
    close_issue, ...) so they do not clash with the task graph's tools."""
    me = agent_author(agent_id)

    def run(fn, *args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except IssueError as e:
            raise ToolError(f"{e.code}: {e}") from None

    def with_paths(issue: dict[str, Any]) -> dict[str, Any]:
        for a in issue.get("attachments", []):
            a["path"] = str(service.attachment_path(a).resolve())
        return issue

    @server.tool()
    def list_issues(
        status: str | None = None,
        search: str | None = None,
        author: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Issues, newest first. status: comma-separated draft/open/in_progress/closed
        or 'all' (default: everything published). search matches title, description, #number."""
        return run(service.list_issues, status=status, search=search, author=author,
                   assignee=assignee, label=label, limit=limit)

    @server.tool()
    def get_issue(issue: str) -> dict:
        """One issue (id or '#12') with description, comments and screenshot
        metadata (url, and path on this machine)."""
        return with_paths(run(service.get, issue))

    @server.tool()
    def get_issue_screenshot(issue: str, attachment_id: str) -> Image:
        """The image of one issue attachment."""
        meta, path = run(service.attachment, issue, attachment_id)
        return Image(data=path.read_bytes(), format=IMAGE_TYPES[meta["content_type"]].replace("jpg", "jpeg"))

    @server.tool()
    def create_issue(title: str, description: str = "", labels: list[str] | None = None,
                     assignee: str | None = None) -> dict:
        """File a new (open) issue, e.g. a bug you found while working."""
        return run(service.create, me, title=title, description=description,
                   labels=labels, assignee=assignee)

    @server.tool()
    def update_issue(
        issue: str,
        title: str | None = None,
        description: str | None = None,
        status: Literal["open", "in_progress"] | None = None,
        labels: list[str] | None = None,
        assignee: str | None = None,
    ) -> dict:
        """Change fields. status moves between open and in_progress. assignee
        '' unassigns; use 'agent:<id>' for an agent or an email for a person."""
        return run(service.update, me, issue, title=title, description=description,
                   status=status, labels=labels, assignee=assignee)

    @server.tool()
    def comment_on_issue(issue: str, body: str) -> dict:
        """Add a comment (markdown): progress notes, findings, questions."""
        return run(service.add_comment, me, issue, body)

    @server.tool()
    def close_issue(issue: str, reason: str) -> dict:
        """Close an issue, saying why (fixed in <commit>, duplicate of #3, won't fix...)."""
        return run(service.close, me, issue, reason)

    @server.tool()
    def reopen_issue(issue: str) -> dict:
        """Reopen a closed issue."""
        return run(service.reopen, me, issue)
