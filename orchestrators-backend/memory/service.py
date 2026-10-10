"""MemoryService: what the agents learn and keep across sessions.

Three scopes, narrowest wins on conflict:

- project: strategy, success criteria, decisions and findings of one project.
- user:    owner preferences that hold across projects.
- global:  lessons about how the orchestrator itself should behave.

The loop that makes it better over time:

1. `context()` gives an agent the memories that apply before it starts.
2. `remember()` (explicit), `reflect()` (distilled from a conversation) and
   `sync_from_taskgraph()` (decision log, rejected changes) write new ones.
3. `feedback()` raises or lowers a memory's confidence. Retrieval ranks by
   confidence, and a memory that keeps being wrong is archived.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from taskgraph.models import Actor
from taskgraph.service import TaskGraphService, _slug, _utcnow

from .models import (
    ARCHIVE_BELOW,
    CORE_KINDS,
    HELPFUL_STEP,
    ID_PART,
    SCOPE_ORDER,
    UNHELPFUL_STEP,
    ChatMessage,
    Memory,
    MemoryKind,
    MemorySource,
    MemoryStatus,
    ReflectOp,
    Scope,
)
from .store import MarkdownMemoryStore

DEFAULT_ID = "default"
SYNC_ACTOR = Actor(type="agent", id="memory-sync")

SCOPE_HEADINGS = {
    "project": "This project",
    "user": "Owner preferences (all projects)",
    "global": "Orchestrator lessons",
}

_STOPWORDS = frozenset(
    "the and for with that this are was not but you our from have has what which "
    "should about into than then when will can more".split()
)


class MemoryServiceError(Exception):
    """Validation failure. `code` is machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class Distiller(Protocol):
    def distill(
        self, messages: Sequence[ChatMessage], existing: Sequence[Memory]
    ) -> list[ReflectOp]: ...


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) > 2 and t not in _STOPWORDS
    }


def _score(memory: Memory, query: set[str]) -> float:
    """Share of query words found (title and tags count double), weighted by
    confidence so memories that proved useful rank first."""
    if not query:
        return 0.0
    strong = _tokens(memory.title + " " + " ".join(memory.tags))
    weak = _tokens(memory.body)
    hits = sum(2 if t in strong else 1 if t in weak else 0 for t in query)
    return hits / (2 * len(query)) * (0.5 + memory.confidence)


class MemoryService:
    def __init__(
        self,
        store: MarkdownMemoryStore,
        distiller: Distiller | None = None,
        *,
        clock: Callable[[], str] = _utcnow,
    ) -> None:
        self.store = store
        self.distiller = distiller
        self.clock = clock

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def get(self, memory_id: str) -> Memory:
        return self._get(self.store.load(), memory_id)

    def list_memories(
        self,
        *,
        scope: Scope | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = "active",
        q: str | None = None,
    ) -> list[Memory]:
        """Filter memories; with `q`, keep only matches, best first."""
        out = [
            m for m in self.store.load().values()
            if (scope is None or m.scope == scope)
            and (project_id is None or m.project_id == project_id)
            and (user_id is None or m.user_id == user_id)
            and (kind is None or m.kind == kind)
            and (status is None or m.status == status)
        ]
        if q:
            return self._rank(out, q)
        return sorted(out, key=lambda m: m.id)

    def search(
        self,
        query: str,
        *,
        project_id: str = DEFAULT_ID,
        user_id: str = DEFAULT_ID,
        kind: MemoryKind | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        """Best matches among the memories that apply to this project and owner."""
        visible = self._visible(self.store.load(), project_id, user_id)
        return self._rank([m for m in visible if kind is None or m.kind == kind], query)[:limit]

    def tree(self) -> list[dict[str, Any]]:
        """Folder/file view for the Memory page (frontend `MemoryFolder[]`)."""
        folders: dict[str, dict[str, Any]] = {}
        for m in self.list_memories():
            folder, _, name = m.path.rpartition("/")
            entry = folders.setdefault(folder, {"id": folder, "name": folder, "files": []})
            entry["files"].append(
                {
                    "id": m.id,
                    "name": name,
                    "path": m.path,
                    "content": f"# {m.title}\n\n{m.body}\n",
                    "title": m.title,
                    "scope": m.scope,
                    "kind": m.kind,
                    "confidence": m.confidence,
                    "uses": m.uses,
                    "updatedAt": m.updated_at,
                }
            )
        return [folders[k] for k in sorted(folders)]

    def stats(self) -> dict[str, Any]:
        memories = list(self.store.load().values())
        active = [m for m in memories if m.status == "active"]
        helpful = sum(m.helpful for m in memories)
        rated = helpful + sum(m.unhelpful for m in memories)
        return {
            "active": len(active),
            "archived": sum(m.status == "archived" for m in memories),
            "superseded": sum(m.status == "superseded" for m in memories),
            "byScope": {s: sum(m.scope == s for m in active) for s in SCOPE_ORDER},
            "uses": sum(m.uses for m in memories),
            "helpfulRate": helpful / rated if rated else None,
        }

    def log(self, limit: int = 100) -> list[dict[str, Any]]:
        """What memory learned and when (created, updated, used, feedback...)."""
        return self.store.read_log(limit)

    # ------------------------------------------------------------------
    # the loop: context -> remember / reflect / sync -> feedback
    # ------------------------------------------------------------------

    def context(
        self,
        *,
        project_id: str = DEFAULT_ID,
        user_id: str = DEFAULT_ID,
        query: str | None = None,
        limit: int = 5,
        budget_chars: int = 6000,
    ) -> dict[str, Any]:
        """Markdown block for an agent's prompt, plus the ids it contains.

        Always includes strategy and preferences; adds the `limit` best
        learnings / decisions / notes for `query` (or the most trusted ones).
        Narrower scopes are picked first, so they survive a tight budget.
        """
        with self.store.locked():
            visible = self._visible(self.store.load(), project_id, user_id)
            by_precedence = lambda m: (SCOPE_ORDER.index(m.scope), -m.confidence)  # noqa: E731
            core = sorted((m for m in visible if m.kind in CORE_KINDS), key=by_precedence)
            rest = [m for m in visible if m.kind not in CORE_KINDS]
            if query:
                extra = self._rank(rest, query)[:limit]
            else:
                extra = sorted(rest, key=lambda m: (-m.confidence, m.updated_at or ""))[:limit]

            picked: list[Memory] = []
            used = 0
            for m in [*core, *extra]:
                size = len(m.title) + len(m.body)
                if picked and used + size > budget_chars:
                    break
                picked.append(m)
                used += size

            now = self.clock()
            for m in picked:
                m.uses += 1
                self.store.write(m)
            if picked:
                self.store.log({"ts": now, "event": "used", "ids": [m.id for m in picked],
                                "projectId": project_id, "userId": user_id, "query": query})

        parts = []
        if picked:
            parts.append(
                "## Memory\n\nWhat is already known. Do not ask the owner about "
                "anything answered here. If entries conflict, this project beats "
                "owner preferences, which beat orchestrator lessons."
            )
        for scope in SCOPE_ORDER:
            items = [m for m in picked if m.scope == scope]
            if items:
                parts.append(f"### {SCOPE_HEADINGS[scope]}")
                parts.extend(f"**{m.title}** ({m.kind}, id `{m.id}`)\n{m.body}" for m in items)
        return {"text": "\n\n".join(parts), "memoryIds": [m.id for m in picked]}

    def remember(
        self,
        actor: Actor,
        *,
        title: str,
        body: str,
        kind: MemoryKind,
        scope: Scope = "project",
        project_id: str | None = None,
        user_id: str | None = None,
        tags: Sequence[str] = (),
        source: MemorySource | None = None,
        slug: str | None = None,
    ) -> Memory:
        """Store a memory. A memory with the same scope, kind and title is
        updated instead of duplicated."""
        with self.store.locked():
            return self._put(
                self.store.load(), actor, title=title, body=body, kind=kind, scope=scope,
                project_id=project_id, user_id=user_id, tags=tags, source=source, slug=slug,
            )

    def update(
        self,
        actor: Actor,
        memory_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        tags: Sequence[str] | None = None,
        confidence: float | None = None,
        status: MemoryStatus | None = None,
    ) -> Memory:
        with self.store.locked():
            m = self._get(self.store.load(), memory_id)
            changes = {
                k: v for k, v in
                {"title": title, "body": body, "confidence": confidence, "status": status,
                 "tags": list(tags) if tags is not None else None}.items()
                if v is not None
            }
            if not changes:
                raise MemoryServiceError("invalid", "nothing to change")
            # Re-validate so a bad confidence or status is rejected, not written.
            m = Memory.model_validate({**m.model_dump(), **changes})
            return self._save(m, actor, "updated")

    def archive(self, actor: Actor, memory_id: str) -> Memory:
        return self.update(actor, memory_id, status="archived")

    def feedback(
        self, actor: Actor, memory_id: str, helpful: bool, note: str | None = None
    ) -> Memory:
        """Tell memory whether an entry helped. Unhelpful entries lose
        confidence faster than helpful ones gain it, and are archived once
        confidence drops below the threshold."""
        with self.store.locked():
            return self._feedback(self.store.load(), actor, memory_id, helpful, note)

    def reflect(
        self,
        actor: Actor,
        messages: Sequence[ChatMessage],
        *,
        project_id: str = DEFAULT_ID,
        user_id: str = DEFAULT_ID,
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Distill a finished conversation into memory changes and apply them."""
        if self.distiller is None:
            raise MemoryServiceError("unavailable", "no distiller configured (set GEMINI_API_KEY)")
        if not messages:
            raise MemoryServiceError("invalid", "no messages given")
        existing = self._visible(self.store.load(), project_id, user_id)
        ops = self.distiller.distill(messages, existing)  # slow; outside the lock

        source = MemorySource(type="conversation", ref=ref)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        with self.store.locked():
            memories = self.store.load()
            for op in ops:
                try:
                    m = self._apply_reflect_op(memories, actor, op, project_id, user_id, source)
                except MemoryServiceError as e:
                    skipped.append({"op": op.dump(), "error": e.message})
                    continue
                memories[m.id] = m
                applied.append({"op": op.op, "id": m.id, "title": m.title, "reason": op.reason})
        return {"applied": applied, "skipped": skipped}

    def sync_from_taskgraph(
        self, taskgraph: TaskGraphService, *, project_id: str = DEFAULT_ID
    ) -> list[Memory]:
        """Copy what the task graph already knows into project memory:
        resolved decisions, and agent changes the owner rejected with a note.
        Safe to call repeatedly; each decision / changeset is stored once."""
        decisions = taskgraph.decisions("resolved")
        rejected = [c for c in taskgraph.changesets("rejected") if c.get("reviewNote")]
        created: list[Memory] = []
        with self.store.locked():
            memories = self.store.load()
            seen = {(m.source.type, m.source.ref) for m in memories.values()
                    if m.project_id == project_id}
            for d in decisions:
                if ("decision", d["taskId"]) in seen:
                    continue
                who = Actor.model_validate(d["resolvedBy"]) if d.get("resolvedBy") else SYNC_ACTOR
                body = f"Question: {d['question']}\n\nChosen: {d['chosen']}\n\nRationale: {d['rationale']}"
                created.append(self._put(
                    memories, who, title=d["title"], body=body, kind="decision",
                    project_id=project_id, slug=_slug(d["taskId"]),
                    source=MemorySource(type="decision", ref=d["taskId"]),
                ))
            for c in rejected:
                if ("changeset", c["id"]) in seen:
                    continue
                proposer = Actor.model_validate(c["actor"]).label()
                ops = ", ".join(op["op"] for op in c["ops"])
                body = (
                    f"{proposer} proposed: {c.get('reason') or ops} ({ops}).\n\n"
                    f"The owner rejected it: {c['reviewNote']}"
                )
                created.append(self._put(
                    memories, SYNC_ACTOR, title=f"Rejected: {c.get('reason') or c['id']}",
                    body=body, kind="learning", project_id=project_id, slug=_slug(c["id"]),
                    source=MemorySource(type="changeset", ref=c["id"]),
                ))
        return created

    # ------------------------------------------------------------------
    # internals (callers hold the store lock for anything that writes)
    # ------------------------------------------------------------------

    @staticmethod
    def _get(memories: dict[str, Memory], memory_id: str) -> Memory:
        m = memories.get(memory_id)
        if m is None:
            raise MemoryServiceError("not_found", f"memory '{memory_id}' not found")
        return m

    @staticmethod
    def _visible(memories: dict[str, Memory], project_id: str, user_id: str) -> list[Memory]:
        """Active memories that apply to this project and owner."""
        return [
            m for m in memories.values()
            if m.status == "active"
            and (
                m.scope == "global"
                or (m.scope == "user" and m.user_id == user_id)
                or (m.scope == "project" and m.project_id == project_id)
            )
        ]

    @staticmethod
    def _rank(memories: Sequence[Memory], query: str) -> list[Memory]:
        q = _tokens(query)
        scored = [(s, m) for m in memories if (s := _score(m, q)) > 0]
        return [m for _, m in sorted(scored, key=lambda sm: -sm[0])]

    def _save(self, m: Memory, actor: Actor, event: str, **extra: Any) -> Memory:
        m.updated_at = self.clock()
        self.store.write(m)
        self.store.log({"ts": m.updated_at, "event": event, "id": m.id,
                        "actor": actor.dump(), **extra})
        return m

    def _put(
        self,
        memories: dict[str, Memory],
        actor: Actor,
        *,
        title: str,
        body: str,
        kind: MemoryKind,
        scope: Scope = "project",
        project_id: str | None = None,
        user_id: str | None = None,
        tags: Sequence[str] = (),
        source: MemorySource | None = None,
        slug: str | None = None,
    ) -> Memory:
        if not title.strip():
            raise MemoryServiceError("invalid", "title is required")
        if scope == "project":
            project_id, user_id = project_id or DEFAULT_ID, None
            owner, prefix = project_id, f"projects.{project_id}"
        elif scope == "user":
            project_id, user_id = None, user_id or DEFAULT_ID
            owner, prefix = user_id, f"users.{user_id}"
        else:
            project_id, user_id = None, None
            owner, prefix = "global", "global"
        if not re.match(ID_PART, owner):
            raise MemoryServiceError("invalid", f"'{owner}' may only contain letters, digits, _ and -")
        memory_id = f"{prefix}.{kind}.{slug or _slug(title)}"

        existing = memories.get(memory_id)
        if existing is not None:
            existing.title, existing.body = title, body
            existing.tags = list(dict.fromkeys([*existing.tags, *tags]))
            existing.status, existing.superseded_by = "active", None
            return self._save(existing, actor, "updated")

        m = Memory(
            id=memory_id, scope=scope, project_id=project_id, user_id=user_id, kind=kind,
            title=title, body=body, tags=list(tags), source=source or MemorySource(),
            created_by=actor, created_at=self.clock(),
        )
        memories[m.id] = m
        return self._save(m, actor, "created")

    def _feedback(
        self, memories: dict[str, Memory], actor: Actor, memory_id: str,
        helpful: bool, note: str | None,
    ) -> Memory:
        m = self._get(memories, memory_id)
        if helpful:
            m.helpful += 1
            m.confidence = round(min(1.0, m.confidence + HELPFUL_STEP), 2)
        else:
            m.unhelpful += 1
            m.confidence = round(max(0.0, m.confidence - UNHELPFUL_STEP), 2)
            if m.confidence < ARCHIVE_BELOW and m.status == "active":
                m.status = "archived"
        return self._save(m, actor, "feedback", helpful=helpful, note=note,
                          confidence=m.confidence, status=m.status)

    def _apply_reflect_op(
        self, memories: dict[str, Memory], actor: Actor, op: ReflectOp,
        project_id: str, user_id: str, source: MemorySource,
    ) -> Memory:
        if op.op == "reinforce":
            return self._feedback(memories, actor, op.target_id or "", True, op.reason)

        if op.op == "update":
            m = self._get(memories, op.target_id or "")
            m.title, m.body = op.title or m.title, op.body or m.body
            m.tags = list(dict.fromkeys([*m.tags, *op.tags]))
            return self._save(m, actor, "updated", reason=op.reason)

        old = self._get(memories, op.target_id or "") if op.op == "supersede" else None
        if not (op.title and op.body):
            raise MemoryServiceError("invalid", f"{op.op} needs a title and a body")
        new = self._put(
            memories, actor, title=op.title, body=op.body,
            kind=op.kind or (old.kind if old else "learning"),
            scope=op.scope or (old.scope if old else "project"),
            project_id=project_id, user_id=user_id, tags=op.tags, source=source,
        )
        if old is not None and old.id != new.id:
            old.status, old.superseded_by = "superseded", new.id
            self._save(old, actor, "superseded", by=new.id, reason=op.reason)
        return new
