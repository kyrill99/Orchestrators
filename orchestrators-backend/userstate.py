"""Per-user state: who has signed in, and small JSON values per user.

    users       email, name, first_seen, last_seen (upserted on every request
                to the endpoints below)
    user_state  (email, key) -> JSON value, e.g. UI preferences or the last
                opened project

Endpoints (the user is identity.current_user, i.e. oauth2-proxy's header):

    GET    /api/me                 {email, name}
    GET    /api/me/state           {key: value, ...}
    GET    /api/me/state/{key}     the value, 404 if unset
    PUT    /api/me/state/{key}     body: any JSON value (max 64 KB); returns it
    DELETE /api/me/state/{key}     204, 404 if unset

Mount with `app.include_router(build_router(UserStateStore(db)))`; this
router also serves GET /api/me, so it replaces identity.build_router().
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from db import Database
from identity import User, current_user
from taskgraph.sqlite_store import create_schema, join_tx

MAX_VALUE_BYTES = 64 * 1024
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email      TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_state (
    email      TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,   -- JSON
    updated_at TEXT NOT NULL,
    PRIMARY KEY (email, key)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class UserStateStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        create_schema(db, SCHEMA)

    def touch(self, user: User) -> None:
        """Insert the user or refresh name and last_seen."""
        now = _now()
        with join_tx(self.db) as conn:
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?) ON CONFLICT(email) DO UPDATE "
                "SET name = excluded.name, last_seen = excluded.last_seen",
                (user.email, user.name, now, now),
            )

    def user(self, email: str) -> dict[str, Any] | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def all(self, email: str) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_state WHERE email = ? ORDER BY key", (email,)
            ).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def get(self, email: str, key: str) -> tuple[bool, Any]:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT value FROM user_state WHERE email = ? AND key = ?", (email, key)
            ).fetchone()
        return (True, json.loads(row["value"])) if row else (False, None)

    def put(self, email: str, key: str, value: Any) -> None:
        with join_tx(self.db) as conn:
            conn.execute(
                "INSERT INTO user_state VALUES (?, ?, ?, ?) ON CONFLICT(email, key) DO UPDATE "
                "SET value = excluded.value, updated_at = excluded.updated_at",
                (email, key, json.dumps(value, ensure_ascii=False), _now()),
            )

    def delete(self, email: str, key: str) -> bool:
        with join_tx(self.db) as conn:
            cur = conn.execute("DELETE FROM user_state WHERE email = ? AND key = ?", (email, key))
        return cur.rowcount > 0


def build_router(store: UserStateStore) -> APIRouter:
    r = APIRouter(prefix="/api/me", tags=["identity"])

    def seen(request: Request) -> User:
        user = current_user(request)
        store.touch(user)
        return user

    def check_key(key: str) -> None:
        if not KEY_RE.match(key):
            raise HTTPException(400, "key must be 1-128 characters of A-Z a-z 0-9 _ . : -")

    @r.get("", response_model=User)
    def me(request: Request):
        return seen(request)

    @r.get("/state")
    def all_state(request: Request) -> dict[str, Any]:
        return store.all(seen(request).email)

    @r.get("/state/{key}")
    def get_state(key: str, request: Request) -> Any:
        check_key(key)
        found, value = store.get(seen(request).email, key)
        if not found:
            raise HTTPException(404, f"no state for '{key}'")
        return value

    @r.put("/state/{key}")
    async def put_state(key: str, request: Request) -> Any:
        check_key(key)
        raw = await request.body()
        if len(raw) > MAX_VALUE_BYTES:
            raise HTTPException(413, f"value is larger than {MAX_VALUE_BYTES} bytes")
        try:
            value = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "body must be JSON") from None
        user = await run_in_threadpool(seen, request)
        await run_in_threadpool(store.put, user.email, key, value)
        return value

    @r.delete("/state/{key}", status_code=204)
    def delete_state(key: str, request: Request) -> Response:
        check_key(key)
        if not store.delete(seen(request).email, key):
            raise HTTPException(404, f"no state for '{key}'")
        return Response(status_code=204)

    return r
