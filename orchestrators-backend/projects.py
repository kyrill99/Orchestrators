"""Projects: one task graph + brief per project. See docs/architecture/projects.md.

The active project comes from the `orch_project` cookie. `ProjectMiddleware` puts it
in the `current_project` context variable; `ScopedProxy` objects resolve the task
graph service / run manager of that project on every attribute access, so routers
and agent tools built for a single graph work per project unchanged.
"""


import contextvars
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import Database

COOKIE = "orch_project"
DEFAULT = "default"
ORCHESTRATORS = "orchestrators"

current_project: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_project", default=DEFAULT
)

SEED = [
    (DEFAULT, "Default project", "The team's first project."),
    (
        ORCHESTRATORS,
        "Orchestrators",
        "The Orchestrators app itself: a strategy-first orchestration layer for agentic "
        "projects (Next.js frontend, FastAPI backend, task graph, memory, agents). Bug "
        "reports from the app land here as tasks. Fix bugs with small, safe changes; keep "
        "the demo working.",
    ),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at TEXT NOT NULL
);
"""


class ProjectError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40].strip("-") or "project"


class ProjectRegistry:
    def __init__(self, db: Database):
        self.db = db
        self._ids: set[str] = set()
        with self.db.tx() as c:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    c.execute(statement)
            for pid, name, desc in SEED:
                c.execute(
                    "INSERT OR IGNORE INTO projects (id, name, description, created_by, created_at)"
                    " VALUES (?, ?, ?, NULL, ?)",
                    (pid, name, desc, _now()),
                )
            self._ids = {r["id"] for r in c.execute("SELECT id FROM projects").fetchall()}

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        return {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "createdBy": r["created_by"],
            "createdAt": r["created_at"],
        }

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM projects ORDER BY created_at, id").fetchall()
        return [self._row(r) for r in rows]

    def get(self, pid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        return self._row(r) if r else None

    def exists(self, pid: str) -> bool:
        """In-memory check (no DB lock): safe to call on the event loop."""
        return pid in self._ids

    def create(self, name: str, description: str = "", created_by: str | None = None) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ProjectError("invalid", "name is required")
        base, n = _slug(name), 2
        pid = base
        while self.exists(pid):
            pid, n = f"{base}-{n}", n + 1
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO projects (id, name, description, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (pid, name, (description or "").strip(), created_by, _now()),
            )
        self._ids.add(pid)
        return self.get(pid)  # type: ignore[return-value]

    def update(self, pid: str, name: str | None = None, description: str | None = None) -> dict[str, Any]:
        if not self.exists(pid):
            raise ProjectError("not_found", f"project '{pid}' not found")
        sets, args = [], []
        if name is not None:
            if not name.strip():
                raise ProjectError("invalid", "name cannot be empty")
            sets.append("name = ?")
            args.append(name.strip())
        if description is not None:
            sets.append("description = ?")
            args.append(description.strip())
        if sets:
            with self.db.tx() as c:
                c.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", (*args, pid))
        return self.get(pid)  # type: ignore[return-value]

    def brief(self, pid: str | None = None) -> str:
        """Markdown section for agent prompts."""
        p = self.get(pid or current_project.get())
        if not p:
            return ""
        text = f"## Project: {p['name']}"
        if p["description"]:
            text += f"\n\n{p['description']}"
        return text


def project_db_path(data_dir: str | Path, pid: str) -> Path:
    return Path(data_dir) / "projects" / f"{pid}.db"


class ProjectGraphs:
    """Lazily built per-project (service, run manager) pairs.

    `factory(pid)` returns (TaskGraphService, RunManager) for a project; the
    default project's pair is passed in so the existing graph is reused.
    """

    def __init__(self, registry: ProjectRegistry, factory: Callable[[str], tuple[Any, Any]],
                 default: tuple[Any, Any]):
        self.registry = registry
        self.factory = factory
        self._pairs: dict[str, tuple[Any, Any]] = {DEFAULT: default}
        self._lock = threading.Lock()

    def pair(self, pid: str | None = None) -> tuple[Any, Any]:
        pid = pid or current_project.get()
        if pid not in self._pairs and not self.registry.exists(pid):
            pid = DEFAULT
        with self._lock:
            if pid not in self._pairs:
                self._pairs[pid] = self.factory(pid)
            return self._pairs[pid]

    def all(self) -> list[tuple[Any, ...]]:
        with self._lock:
            return list(self._pairs.values())

    def service(self, pid: str | None = None) -> Any:
        return self.pair(pid)[0]

    def manager(self, pid: str | None = None) -> Any:
        return self.pair(pid)[1]


class ScopedProxy:
    """Stand-in object: every attribute resolves on the current project's instance."""

    def __init__(self, resolve: Callable[[], Any]):
        object.__setattr__(self, "_resolve", resolve)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._resolve(), name, value)


def project_from_cookie(header: str | None, registry: ProjectRegistry | None = None) -> str:
    """Tolerant parse (like Starlette's cookie_parser): one odd cookie must not hide ours."""
    if not header:
        return DEFAULT
    from urllib.parse import unquote

    pid = ""
    for part in header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip() == COOKIE:
            pid = unquote(value.strip().strip('"')).strip()
    if not pid or (registry is not None and not registry.exists(pid)):
        return DEFAULT
    return pid


class ProjectMiddleware:
    """Pure ASGI middleware: sets `current_project` from the cookie for the request."""

    def __init__(self, app: Any, registry: ProjectRegistry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        header = None
        for k, v in scope.get("headers") or []:
            if k == b"cookie":
                header = v.decode("latin-1")
                break
        token = current_project.set(project_from_cookie(header, self.registry))
        try:
            await self.app(scope, receive, send)
        finally:
            current_project.reset(token)


def build_router(registry: ProjectRegistry, viewers: Callable[[], list[dict[str, Any]]]):
    from fastapi import APIRouter, HTTPException, Request
    from pydantic import BaseModel

    from identity import current_user

    class CreateBody(BaseModel):
        name: str
        description: str = ""

    class UpdateBody(BaseModel):
        name: str | None = None
        description: str | None = None

    r = APIRouter(prefix="/api/projects", tags=["projects"])

    def call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ProjectError as e:
            raise HTTPException(404 if e.code == "not_found" else 422,
                                {"code": e.code, "message": e.message}) from None

    def with_viewers(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        entries = viewers()
        for p in projects:
            p["viewers"] = sorted({e["email"] for e in entries if e.get("project", DEFAULT) == p["id"]})
        return projects

    @r.get("")
    def list_projects():
        return with_viewers(registry.list())

    @r.get("/current")
    def current():
        p = registry.get(current_project.get()) or registry.get(DEFAULT)
        return with_viewers([p])[0]

    @r.post("", status_code=201)
    def create(body: CreateBody, request: Request):
        p = call(registry.create, body.name, body.description, current_user(request).email)
        return with_viewers([p])[0]

    @r.patch("/{pid}")
    def update(pid: str, body: UpdateBody):
        return with_viewers([call(registry.update, pid, body.name, body.description)])[0]

    return r
