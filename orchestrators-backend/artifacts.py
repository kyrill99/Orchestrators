"""Task artifacts: files agents produce for a task (mostly HTML reports).

Mount with:

    store = ArtifactStore(db)
    app.include_router(build_router(store))

Files live under ARTIFACTS_PATH/<project>/<task id>/<artifact id>-<filename>
(default: artifacts/ next to the database, i.e. data/artifacts, which persists
across deploys); metadata in the
`artifacts` table of the shared database, one table for every project. Agents
save through `store.scoped(project)` (a `ProjectArtifacts`), which the
`save_artifact` / `list_artifacts` / `read_artifact` tools in
services/agent_tools.py use.

REST (everyone signed in sees everything, as elsewhere):

    GET /api/artifacts?task_id=<id>   a task's artifacts in the current project
    GET /api/artifacts                every artifact of the current project, with
                                      version / versions / latest per (task, filename)
    GET /api/artifacts/{id}           metadata
    GET /api/artifacts/{id}/view      the file inline (HTML rendered, sandboxed)
    GET /api/artifacts/{id}/download  the file as an attachment

Security: artifact HTML is written by a model and must not act with the app's
origin. Every file response carries `Content-Security-Policy: sandbox
allow-scripts ...` without allow-same-origin, so even opened directly in a tab
it runs in an opaque origin: no cookies, no localStorage, and requests to /api
are cross-origin without credentials. Scripts are allowed (inline, plus one CDN
for chart libraries) so reports can draw charts; `connect-src 'none'`, images
and fonts limited to data:/blob:, and no forms keep it from fetching or
sending anything. Links can open in a new tab (allow-popups). The UI also
shows it in an <iframe sandbox="allow-scripts allow-popups ...">.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from db import Database
from taskgraph.service import TaskGraphError

DEFAULT_FILES_PATH = "data/artifacts"
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024

ArtifactKind = Literal["html", "markdown", "json", "csv", "text", "svg"]

# kind -> (extension, stored content type)
KINDS: dict[str, tuple[str, str]] = {
    "html": ("html", "text/html; charset=utf-8"),
    "markdown": ("md", "text/markdown; charset=utf-8"),
    "json": ("json", "application/json"),
    "csv": ("csv", "text/csv; charset=utf-8"),
    "text": ("txt", "text/plain; charset=utf-8"),
    "svg": ("svg", "image/svg+xml"),
}
_EXT_ALIASES = {"htm": "html", "markdown": "md"}

# The one script host reports may load a chart library from.
SCRIPT_CDN = "https://cdn.jsdelivr.net"

CSP = "; ".join([
    # Opaque origin: no cookies or storage of the app, no same-origin /api calls.
    # Popups so links (sources) open in a new, normal tab.
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox",
    "default-src 'none'",
    f"script-src 'unsafe-inline' {SCRIPT_CDN}",
    "style-src 'unsafe-inline'",
    "img-src data: blob:",
    "font-src data:",
    "media-src data: blob:",
    "connect-src 'none'",  # no fetch / XHR / WebSocket / beacons
    "form-action 'none'",
    "base-uri 'none'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "private, max-age=300",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_task ON artifacts(project_id, task_id, created_at)
"""


class ArtifactError(TaskGraphError):
    """not_found / invalid / too_large. A TaskGraphError, so agent tools report it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def view_url(artifact_id: str) -> str:
    return f"/api/artifacts/{artifact_id}/view"


def download_url(artifact_id: str) -> str:
    return f"/api/artifacts/{artifact_id}/download"


def _component(text: str) -> str:
    """A safe single path component."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return s[:80] or "x"


def clean_filename(filename: str, kind: str) -> str:
    """Basename, safe characters, with the extension for `kind`."""
    ext = KINDS[kind][0]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename.replace("\\", "/")).name).strip(".-")
    stem, dot, suffix = name.rpartition(".")
    if dot and _EXT_ALIASES.get(suffix.lower(), suffix.lower()) == ext:
        name = stem
    elif not dot:
        name = suffix
    name = name.strip(".-")[:80] or "report"
    return f"{name}.{ext}"


class ArtifactStore:
    def __init__(self, db: Database, files_path: str | Path | None = None):
        self.db = db
        # Default: artifacts/ next to the database file (data/artifacts).
        beside_db = Path(db.path).parent / "artifacts" if db.path != ":memory:" else DEFAULT_FILES_PATH
        self.files = Path(files_path or os.environ.get("ARTIFACTS_PATH") or beside_db)
        with db.tx() as c:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    c.execute(statement)

    def scoped(self, project_id: str) -> ProjectArtifacts:
        return ProjectArtifacts(self, project_id)

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        return {
            "id": r["id"],
            "projectId": r["project_id"],
            "taskId": r["task_id"],
            "runId": r["run_id"],
            "filename": r["filename"],
            "kind": r["kind"],
            "contentType": r["content_type"],
            "size": r["size"],
            "title": r["title"],
            "createdBy": r["created_by"],
            "createdAt": r["created_at"],
            "updatedAt": r["updated_at"],
            "viewUrl": view_url(r["id"]),
            "downloadUrl": download_url(r["id"]),
        }

    def path(self, meta: dict[str, Any]) -> Path:
        return (self.files / _component(meta["projectId"]) / _component(meta["taskId"])
                / f"{meta['id']}-{meta['filename']}")

    def save(
        self,
        project_id: str,
        task_id: str,
        filename: str,
        content: str | bytes,
        *,
        title: str,
        kind: str = "html",
        run_id: str | None = None,
        created_by: str,
    ) -> tuple[dict[str, Any], bool]:
        """Store a file. Saving the same filename again in the same run replaces
        it. Returns (metadata, created)."""
        if kind not in KINDS:
            raise ArtifactError("invalid", f"kind must be one of {', '.join(KINDS)}")
        title = (title or "").strip()[:200]
        if not title:
            raise ArtifactError("invalid", "title is required")
        data = content.encode("utf-8") if isinstance(content, str) else content
        if not data.strip():
            raise ArtifactError("invalid", "content is empty")
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactError(
                "too_large",
                f"artifact is {len(data) // 1024} KB; the limit is {MAX_ARTIFACT_BYTES // (1024 * 1024)} MB",
            )
        name = clean_filename(filename, kind)
        now = _now()
        with self.db.tx() as c:
            existing = None
            if run_id:
                existing = c.execute(
                    "SELECT * FROM artifacts WHERE project_id = ? AND task_id = ? AND run_id = ?"
                    " AND filename = ?", (project_id, task_id, run_id, name),
                ).fetchone()
            if existing is not None:
                aid = existing["id"]
                c.execute(
                    "UPDATE artifacts SET size = ?, title = ?, kind = ?, content_type = ?,"
                    " updated_at = ? WHERE id = ?",
                    (len(data), title, kind, KINDS[kind][1], now, aid),
                )
            else:
                aid = uuid.uuid4().hex
                c.execute(
                    "INSERT INTO artifacts (id, project_id, task_id, run_id, filename, kind,"
                    " content_type, size, title, created_by, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, project_id, task_id, run_id, name, kind, KINDS[kind][1], len(data),
                     title, created_by, now, now),
                )
            meta = self._row(c.execute("SELECT * FROM artifacts WHERE id = ?", (aid,)).fetchone())
            # Write inside the transaction: a failed write rolls the row back.
            path = self.path(meta)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return meta, existing is None

    def list(self, project_id: str, task_id: str) -> list[dict[str, Any]]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT * FROM artifacts WHERE project_id = ? AND task_id = ? ORDER BY created_at, id",
                (project_id, task_id),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_project(self, project_id: str) -> list[dict[str, Any]]:
        """Every artifact of a project, oldest first. Rows with the same
        (task, filename) are versions of one artifact: each row gets `version`
        (1-based by created_at), `versions` (count) and `latest`."""
        with self.db.read() as c:
            rows = c.execute(
                "SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        out = [self._row(r) for r in rows]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for meta in out:
            groups.setdefault((meta["taskId"], meta["filename"]), []).append(meta)
        for metas in groups.values():
            for i, meta in enumerate(metas, start=1):
                meta["version"] = i
                meta["versions"] = len(metas)
                meta["latest"] = i == len(metas)
        return out

    def get(self, artifact_id: str) -> dict[str, Any]:
        with self.db.read() as c:
            r = c.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if r is None:
            raise ArtifactError("not_found", f"artifact '{artifact_id}' not found")
        return self._row(r)

    def file(self, artifact_id: str) -> tuple[dict[str, Any], Path]:
        meta = self.get(artifact_id)
        path = self.path(meta)
        if not path.exists():
            raise ArtifactError("not_found", f"the file of artifact '{artifact_id}' is missing")
        return meta, path

    def read_text(self, artifact_id: str) -> tuple[dict[str, Any], str]:
        meta, path = self.file(artifact_id)
        return meta, path.read_bytes().decode("utf-8", errors="replace")


class ProjectArtifacts:
    """The store bound to one project (what agent tools get)."""

    def __init__(self, store: ArtifactStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def save(self, task_id: str, filename: str, content: str | bytes, **kw: Any) -> tuple[dict[str, Any], bool]:
        return self.store.save(self.project_id, task_id, filename, content, **kw)

    def list(self, task_id: str) -> list[dict[str, Any]]:
        return self.store.list(self.project_id, task_id)

    def read_text(self, artifact_id: str) -> tuple[dict[str, Any], str]:
        meta, text = self.store.read_text(artifact_id)
        if meta["projectId"] != self.project_id:
            raise ArtifactError("not_found", f"artifact '{artifact_id}' not found in this project")
        return meta, text


def service_from_env(project_id: str = "default") -> ProjectArtifacts:
    """For processes other than the API (MCP server, subprocess worker)."""
    from storage import default_db

    return ArtifactStore(default_db()).scoped(project_id)


# ---------------------------------------------------------------- REST

STATUS_FOR_CODE = {"not_found": 404, "invalid": 422, "too_large": 413}


def build_router(store: ArtifactStore) -> APIRouter:
    from projects import current_project

    r = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

    def call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ArtifactError as e:
            raise HTTPException(STATUS_FOR_CODE.get(e.code, 400), {"code": e.code, "message": e.message}) from None

    @r.get("")
    def list_artifacts(task_id: str | None = Query(
        None, description="Task id in the current project; omit for every artifact of the project")):
        if task_id is None:
            return store.list_project(current_project.get())
        return store.list(current_project.get(), task_id)

    @r.get("/{artifact_id}")
    def get_artifact(artifact_id: str):
        return call(store.get, artifact_id)

    @r.get("/{artifact_id}/view")
    def view_artifact(artifact_id: str):
        meta, path = call(store.file, artifact_id)
        # HTML and SVG render; everything else shows as plain text.
        media = meta["contentType"] if meta["kind"] in ("html", "svg") else "text/plain; charset=utf-8"
        return FileResponse(path, media_type=media, filename=meta["filename"],
                            content_disposition_type="inline", headers=SECURITY_HEADERS)

    @r.get("/{artifact_id}/download")
    def download_artifact(artifact_id: str):
        meta, path = call(store.file, artifact_id)
        return FileResponse(path, media_type=meta["contentType"], filename=meta["filename"],
                            content_disposition_type="attachment", headers=SECURITY_HEADERS)

    return r
