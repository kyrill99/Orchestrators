"""Recent commits of the deployed checkout. Mount with:

    app.include_router(build_router("data"))

GET /api/admin/changelog?limit=30 reads `git log` from the repo the backend
runs in. It only looks at refs that are already there (the update script
fetches); it never touches the network. Commits added by the last
self-update are flagged `new`, and commits on the upstream branch that
are not deployed yet come back as `pending`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from selfupdate import STATUS

MAX_LIMIT = 200
UPSTREAM_FALLBACK = "origin/main"

# Fields are split by US (0x1f); `-z` ends each record with NUL. Neither can
# appear in a commit message, so subjects and bodies come through verbatim.
_FIELDS = ("hash", "short", "author", "date", "subject", "body")
_FORMAT = "%x1f".join(("%H", "%h", "%an", "%aI", "%s", "%b"))


class GitError(Exception):
    pass


def _git(repo: Path | None, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=repo, capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError(str(exc)) from exc
    return out.stdout


def _ref(repo: Path | None, ref: str) -> str | None:
    """Full hash of `ref`, or None if it does not resolve locally."""
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip() or None
    except GitError:
        return None


def _log(repo: Path | None, rev: str, limit: int) -> list[dict]:
    out = _git(repo, "log", "-z", f"--max-count={limit}", f"--format={_FORMAT}", rev, "--")
    commits = []
    for record in out.split("\0"):
        if not record.strip("\n"):
            continue
        parts = record.lstrip("\n").split("\x1f")
        if len(parts) != len(_FIELDS):
            continue
        commit = dict(zip(_FIELDS, parts))
        commit["body"] = commit["body"].strip() or None
        commits.append(commit)
    return commits


def changelog(repo: str | Path | None, data_dir: str | Path, limit: int) -> dict:
    repo = Path(repo) if repo is not None else None
    head = _ref(repo, "HEAD")
    if head is None:
        raise GitError("not a git checkout")
    commits = _log(repo, head, limit)

    # Commits the last successful self-update brought in: last.from..HEAD.
    try:
        last = json.loads((Path(data_dir) / STATUS).read_text())
    except (OSError, ValueError):
        last = None
    new: set[str] = set()
    since = last.get("from") if isinstance(last, dict) and last.get("state") == "ok" else None
    base = _ref(repo, since) if since else None
    if base and base != head:
        try:
            new = set(_git(repo, "rev-list", f"{base}..{head}", "--").split())
        except GitError:
            pass
    for commit in commits:
        commit["new"] = commit["hash"] in new

    # Already-fetched upstream commits that are not deployed yet.
    upstream = _upstream(repo)
    target = _ref(repo, upstream) if upstream else None
    pending = _log(repo, f"{head}..{target}", limit) if target else []
    for commit in pending:
        commit["new"] = False

    return {
        "commit": commits[0]["short"] if commits else head[:7],
        "upstream": upstream,
        "since": since if new else None,
        "commits": commits,
        "pending": pending,
    }


def _upstream(repo: Path | None) -> str | None:
    """Tracking branch of HEAD (e.g. origin/main), else origin/main if present."""
    try:
        name = _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").strip()
        if name:
            return name
    except GitError:
        pass
    return UPSTREAM_FALLBACK if _ref(repo, UPSTREAM_FALLBACK) else None


def build_router(data_dir: str | Path, repo: str | Path | None = None) -> APIRouter:
    """`repo` defaults to the working directory, like selfupdate._commit."""
    r = APIRouter(prefix="/api/admin/changelog", tags=["admin"])

    @r.get("")
    def get_changelog(limit: int = 30):
        try:
            return changelog(repo, data_dir, max(1, min(limit, MAX_LIMIT)))
        except GitError as exc:
            raise HTTPException(503, f"git unavailable: {exc}") from exc

    return r
