"""Shared agent conversations, stored in SQLite and broadcast live.

Mount with:

    app.include_router(build_router(ConversationStore(db), broker))

Everyone signed in sees every conversation (there is no permission system).
Posting a message stores it, broadcasts it, then calls the strategy agent
(services.agent.chat) with the conversation's latest Gemini interaction id.
Agent calls are serialized per conversation, so two people typing at once
still give one linear interaction chain.

Live updates go out on the "conversations" channel of the shared broker
(events.py) and are streamed by GET /api/conversations/stream. Event types:

    conversation  a conversation summary (created, new activity, thinking on/off)
    message       {"conversation_id", "message"}: a new visible message; role is
                  user, agent, error, or tool (a memory/graph tool the agent used)
    thinking      {"conversation_id", "thinking", "by"}: agent started/stopped
    resync        the client missed events and should re-fetch
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import services.agent as agent_service
from db import Database
from projects import current_project
from events import Broker, sse_response
from identity import User, current_user

CHANNEL = "conversations"
Mode = Literal["text", "voice"]
Role = Literal["user", "agent", "error", "tool"]

TITLE_LEN = 80
PREVIEW_LEN = 140
TOOL_LEN = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL,
    created_by_name TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    interaction_id  TEXT,
    mode            TEXT NOT NULL DEFAULT 'text'
);
CREATE INDEX IF NOT EXISTS conversations_updated ON conversations(updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    author_email    TEXT,
    author_name     TEXT,
    content         TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'text',
    hidden          INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS message_reactions (
    message_id  INTEGER NOT NULL,
    email       TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    value       INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (message_id, email)
);
"""
REACTION_PREVIEW_LEN = 80



class Participant(BaseModel):
    email: str
    name: str


class Voter(BaseModel):
    email: str
    name: str
    value: int


class Reactions(BaseModel):
    up: int = 0
    down: int = 0
    voters: list[Voter] = Field(default_factory=list)


class Message(BaseModel):
    id: int
    conversation_id: str
    role: Role
    author_email: str | None = None
    author_name: str | None = None
    content: str
    mode: Mode = "text"
    created_at: float
    reactions: Reactions = Field(default_factory=Reactions)


class Summary(BaseModel):
    id: str
    title: str
    created_by: str
    created_by_name: str
    created_at: float
    updated_at: float
    mode: Mode
    message_count: int
    participants: list[Participant]
    last_message: Message | None = None
    thinking: bool = False
    project: str = "default"


class Detail(Summary):
    messages: list[Message]


class CreateBody(BaseModel):
    title: str | None = None
    mode: Mode = "text"


class PostBody(BaseModel):
    content: str = Field(..., min_length=1)
    mode: Mode = "text"
    # Stage directions for the agent (e.g. voice openers): stored, sent to the
    # agent, but not shown or broadcast.
    hidden: bool = False


class ReactBody(BaseModel):
    value: Literal[1, -1, 0]


class PostResult(BaseModel):
    message: Message | None
    reply: Message


def _title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LEN else line[: TITLE_LEN - 1].rstrip() + "…"


def _preview(text: str) -> str:
    line = " ".join(text.split())
    if len(line) <= REACTION_PREVIEW_LEN:
        return line
    return line[: REACTION_PREVIEW_LEN - 1].rstrip() + "\u2026"


def reactions_text(rows) -> str:
    """Rows carry name, email, value, role, author_name, content. Empty -> ""."""
    parts = []
    for r in rows:
        who = r["name"] or r["email"]
        verb = "upvoted" if r["value"] > 0 else "downvoted"
        if r["role"] == "agent":
            whose = "your message"
        else:
            whose = f"{r['author_name'] or 'someone'}'s message"
        parts.append(f'{who} {verb} {whose}: "{_preview(r["content"])}"')
    if not parts:
        return ""
    return "[Reactions since your last reply: " + "; ".join(parts) + "]\n\n"


def _message(row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        author_email=row["author_email"],
        author_name=row["author_name"],
        content=row["content"],
        mode=row["mode"],
        created_at=row["created_at"],
    )


def _add_vote(reactions: Reactions, v) -> None:
    if v["value"] > 0:
        reactions.up += 1
    elif v["value"] < 0:
        reactions.down += 1
    reactions.voters.append(Voter(email=v["email"], name=v["name"] or v["email"], value=v["value"]))


class ConversationStore:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as conn:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            # Projects (docs/architecture/projects.md): conversations belong to one.
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "project" not in cols:
                conn.execute(
                    "ALTER TABLE conversations ADD COLUMN project TEXT NOT NULL DEFAULT 'default'"
                )

    def create(
        self, user: User, title: str | None = None, mode: Mode = "text", project: str | None = None
    ) -> str:
        cid = uuid.uuid4().hex[:12]
        now = time.time()
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, created_by, created_by_name,"
                " created_at, updated_at, mode, project) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, _title(title or ""), user.email, user.name, now, now, mode,
                 project or current_project.get()),
            )
        return cid

    def project_of(self, cid: str) -> str:
        with self.db.read() as conn:
            row = conn.execute("SELECT project FROM conversations WHERE id = ?", (cid,)).fetchone()
        return row["project"] if row and row["project"] else "default"

    def exists(self, cid: str) -> bool:
        with self.db.read() as conn:
            return conn.execute("SELECT 1 FROM conversations WHERE id = ?", (cid,)).fetchone() is not None

    def add_message(
        self,
        cid: str,
        role: Role,
        content: str,
        *,
        user: User | None = None,
        mode: Mode = "text",
        hidden: bool = False,
        interaction_id: str | None = None,
    ) -> Message:
        now = time.time()
        with self.db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, author_email, author_name,"
                " content, mode, hidden, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, role, user.email if user else None, user.name if user else None,
                 content, mode, int(hidden), now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ?, mode = ? WHERE id = ?",
                (now, mode, cid),
            )
            if role == "user" and not hidden:
                conn.execute(
                    "UPDATE conversations SET title = ? WHERE id = ? AND title = ''",
                    (_title(content), cid),
                )
            if interaction_id:
                conn.execute(
                    "UPDATE conversations SET interaction_id = ? WHERE id = ?",
                    (interaction_id, cid),
                )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _message(row)

    def interaction_id(self, cid: str) -> str | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT interaction_id FROM conversations WHERE id = ?", (cid,)).fetchone()
        return row["interaction_id"] if row else None

    def messages(self, cid: str) -> list[Message]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND hidden = 0 ORDER BY id",
                (cid,),
            ).fetchall()
            votes = conn.execute(
                "SELECT r.message_id, r.email, r.name, r.value FROM message_reactions r"
                " JOIN messages m ON m.id = r.message_id WHERE m.conversation_id = ?"
                " ORDER BY r.created_at",
                (cid,),
            ).fetchall()
        by_msg: dict[int, Reactions] = {}
        for v in votes:
            _add_vote(by_msg.setdefault(v["message_id"], Reactions()), v)
        out = []
        for r in rows:
            m = _message(r)
            if m.id in by_msg:
                m.reactions = by_msg[m.id]
            out.append(m)
        return out

    def message(self, cid: str, mid: int) -> Message | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ? AND conversation_id = ?", (mid, cid)
            ).fetchone()
            if row is None:
                return None
            votes = conn.execute(
                "SELECT message_id, email, name, value FROM message_reactions"
                " WHERE message_id = ? ORDER BY created_at",
                (mid,),
            ).fetchall()
        m = _message(row)
        for v in votes:
            _add_vote(m.reactions, v)
        return m

    def react(self, cid: str, mid: int, user: User, value: int) -> Message | None:
        """Upsert (value 1/-1) or remove (value 0) a user's reaction."""
        with self.db.tx() as conn:
            ok = conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ? AND hidden = 0",
                (mid, cid),
            ).fetchone()
            if ok is None:
                return None
            if value == 0:
                conn.execute(
                    "DELETE FROM message_reactions WHERE message_id = ? AND email = ?",
                    (mid, user.email),
                )
            else:
                conn.execute(
                    "INSERT INTO message_reactions (message_id, email, name, value, created_at,"
                    " delivered) VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(message_id, email)"
                    " DO UPDATE SET value = excluded.value, name = excluded.name,"
                    " created_at = excluded.created_at, delivered = 0",
                    (mid, user.email, user.name, value, time.time()),
                )
        return self.message(cid, mid)

    def take_reactions(self, cid: str) -> str:
        """Undelivered reactions in this conversation as an agent-facing prefix; marks them delivered."""
        with self.db.tx() as conn:
            rows = conn.execute(
                "SELECT r.message_id, r.email, r.name, r.value, m.role, m.author_name, m.content"
                " FROM message_reactions r JOIN messages m ON m.id = r.message_id"
                " WHERE m.conversation_id = ? AND r.delivered = 0 ORDER BY r.created_at",
                (cid,),
            ).fetchall()
            if rows:
                conn.execute(
                    "UPDATE message_reactions SET delivered = 1 WHERE delivered = 0 AND"
                    " message_id IN (SELECT id FROM messages WHERE conversation_id = ?)",
                    (cid,),
                )
        return reactions_text(rows)

    def summaries(
        self, cid: str | None = None, limit: int = 200, project: str | None = None
    ) -> list[Summary]:
        if cid:
            where, args = "WHERE c.id = ?", (cid,)
        elif project:
            where, args = "WHERE c.project = ?", (project,)
        else:
            where, args = "", ()
        with self.db.read() as conn:
            convs = conn.execute(
                f"SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id"
                f" AND m.hidden = 0) AS n FROM conversations c {where}"
                f" ORDER BY c.updated_at DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
            ids = [c["id"] for c in convs]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            people = conn.execute(
                f"SELECT conversation_id, author_email, author_name, min(id) AS first"
                f" FROM messages WHERE conversation_id IN ({marks}) AND role = 'user'"
                f" AND hidden = 0 AND author_email IS NOT NULL"
                f" GROUP BY conversation_id, author_email ORDER BY first",
                ids,
            ).fetchall()
            lasts = conn.execute(
                f"SELECT m.* FROM messages m JOIN (SELECT conversation_id, max(id) AS id"
                f" FROM messages WHERE conversation_id IN ({marks}) AND hidden = 0"
                f" AND role != 'tool' GROUP BY conversation_id) l ON m.id = l.id",
                ids,
            ).fetchall()
        by_conv: dict[str, list[Participant]] = {}
        for p in people:
            by_conv.setdefault(p["conversation_id"], []).append(
                Participant(email=p["author_email"], name=p["author_name"] or p["author_email"])
            )
        last_by = {r["conversation_id"]: _message(r) for r in lasts}
        out = []
        for c in convs:
            parts = by_conv.get(c["id"], [])
            if not any(p.email == c["created_by"] for p in parts):
                parts.insert(0, Participant(email=c["created_by"], name=c["created_by_name"]))
            last = last_by.get(c["id"])
            if last and len(last.content) > PREVIEW_LEN:
                last = last.model_copy(update={"content": last.content[: PREVIEW_LEN - 1] + "…"})
            out.append(
                Summary(
                    id=c["id"],
                    title=c["title"],
                    created_by=c["created_by"],
                    created_by_name=c["created_by_name"],
                    created_at=c["created_at"],
                    updated_at=c["updated_at"],
                    mode=c["mode"],
                    message_count=c["n"],
                    participants=parts,
                    last_message=last,
                    project=c["project"] or "default",
                )
            )
        return out

    def summary(self, cid: str) -> Summary | None:
        found = self.summaries(cid)
        return found[0] if found else None


class ConversationService:
    """Storage + broadcasting + per-conversation agent locks."""

    def __init__(self, store: ConversationStore, broker: Broker):
        self.store = store
        self.broker = broker
        self._locks: dict[str, asyncio.Lock] = {}
        self._thinking: dict[str, str] = {}  # conversation id -> who asked

    def lock(self, cid: str) -> asyncio.Lock:
        lock = self._locks.get(cid)
        if lock is None:
            lock = self._locks[cid] = asyncio.Lock()
        return lock

    def _with_live(self, summary: Summary) -> Summary:
        summary.thinking = summary.id in self._thinking
        return summary

    async def list(self) -> list[Summary]:
        project = current_project.get()
        return [
            self._with_live(s)
            for s in await run_in_threadpool(lambda: self.store.summaries(project=project))
        ]

    async def get(self, cid: str) -> Detail:
        summary = await run_in_threadpool(self.store.summary, cid)
        if summary is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        messages = await run_in_threadpool(self.store.messages, cid)
        return Detail(**self._with_live(summary).model_dump(), messages=messages)

    async def _announce(self, cid: str) -> None:
        summary = await run_in_threadpool(self.store.summary, cid)
        if summary:
            self.broker.publish(CHANNEL, "conversation", self._with_live(summary).model_dump())

    def _publish_message(self, message: Message) -> None:
        self.broker.publish(
            CHANNEL,
            "message",
            {"conversation_id": message.conversation_id, "message": message.model_dump()},
        )

    def _set_thinking(self, cid: str, by: str | None) -> None:
        if by is None:
            self._thinking.pop(cid, None)
        else:
            self._thinking[cid] = by
        self.broker.publish(
            CHANNEL, "thinking", {"conversation_id": cid, "thinking": by is not None, "by": by}
        )

    def _record_tool(self, cid: str, mode: Mode, name: str, args: dict, result) -> None:
        """Called from the agent's worker thread for each tool call."""
        call = f"{name}({json.dumps(args, ensure_ascii=False, default=str)})"
        if isinstance(result, dict) and result.get("error"):
            call += f" \u2192 {result['error']}"
        if len(call) > TOOL_LEN:
            call = call[: TOOL_LEN - 1] + "\u2026"
        message = self.store.add_message(cid, "tool", call, mode=mode)
        self._publish_message(message)  # Broker.publish is thread-safe

    async def create(self, user: User, title: str | None, mode: Mode) -> Summary:
        cid = await run_in_threadpool(self.store.create, user, title, mode)
        await self._announce(cid)
        return await run_in_threadpool(self.store.summary, cid)

    async def post(self, cid: str, user: User, body: PostBody) -> PostResult:
        text = body.content.strip()
        if not text:
            raise HTTPException(status_code=400, detail="message is required")
        if not await run_in_threadpool(self.store.exists, cid):
            raise HTTPException(status_code=404, detail="conversation not found")

        mine = await run_in_threadpool(
            lambda: self.store.add_message(
                cid, "user", text, user=user, mode=body.mode, hidden=body.hidden
            )
        )
        if not body.hidden:
            self._publish_message(mine)
            await self._announce(cid)

        # The planner works in the conversation's project, whatever the poster's cookie says.
        project_token = current_project.set(await run_in_threadpool(self.store.project_of, cid))
        try:
            return await self._reply(cid, user, body, text, mine)
        finally:
            current_project.reset(project_token)

    async def react(self, cid: str, mid: int, user: User, value: int) -> Message:
        message = await run_in_threadpool(lambda: self.store.react(cid, mid, user, value))
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        self._publish_message(message)
        return message

    async def _reply(
        self, cid: str, user: User, body: PostBody, text: str, mine: Message
    ) -> PostResult:
        async with self.lock(cid):
            self._set_thinking(cid, user.name)
            try:
                previous = await run_in_threadpool(self.store.interaction_id, cid)
                # Reactions are a stage direction for the agent, not part of the stored message.
                prefix = await run_in_threadpool(self.store.take_reactions, cid)
                result = await run_in_threadpool(
                    lambda: agent_service.chat(
                        prefix + text,
                        previous_interaction_id=previous,
                        mode=body.mode,
                        on_tool=lambda name, args, result: self._record_tool(
                            cid, body.mode, name, args, result
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — stored and shown to everyone
                status = exc.status_code if isinstance(exc, HTTPException) else 500
                detail = exc.detail if isinstance(exc, HTTPException) else f"Agent failed: {exc}"
                error = await run_in_threadpool(
                    lambda: self.store.add_message(cid, "error", str(detail), mode=body.mode)
                )
                self._set_thinking(cid, None)
                self._publish_message(error)
                await self._announce(cid)
                raise HTTPException(status_code=status, detail=detail) from exc

            reply = await run_in_threadpool(
                lambda: self.store.add_message(
                    cid,
                    "agent",
                    result["reply"],
                    mode=body.mode,
                    interaction_id=result.get("interaction_id"),
                )
            )
            self._set_thinking(cid, None)
            self._publish_message(reply)
            await self._announce(cid)
        return PostResult(message=None if body.hidden else mine, reply=reply)


def build_router(service: ConversationService) -> APIRouter:
    r = APIRouter(prefix="/api/conversations", tags=["conversations"])

    @r.get("", response_model=list[Summary])
    async def list_conversations():
        return await service.list()

    @r.post("", response_model=Summary, status_code=201)
    async def create_conversation(body: CreateBody, request: Request):
        return await service.create(current_user(request), body.title, body.mode)

    # Registered before /{conversation_id} so "stream" is not taken as an id.
    @r.get("/stream")
    async def stream(request: Request):
        return sse_response(service.broker, request, [CHANNEL])

    @r.get("/{conversation_id}", response_model=Detail)
    async def get_conversation(conversation_id: str):
        return await service.get(conversation_id)

    @r.post("/{conversation_id}/messages", response_model=PostResult)
    async def post_message(conversation_id: str, body: PostBody, request: Request):
        return await service.post(conversation_id, current_user(request), body)

    @r.post("/{conversation_id}/messages/{message_id}/reactions", response_model=Message)
    async def react_to_message(
        conversation_id: str, message_id: int, body: ReactBody, request: Request
    ):
        return await service.react(conversation_id, message_id, current_user(request), body.value)

    return r
