"""Project repositories: where task agents change code and push to main.

A project may have a repository (`WorkspaceConfig`, stored on the projects
table, GET/PUT /api/projects/{id}/workspace):

    {"remote": "git@github-orchestrators:kyrill99/Orchestrators.git",
     "root": "/home/orchestrators/workspaces/orchestrators",   # the clone
     "branch": "main",
     "checks": [{"name": "backend-tests",
                 "command": "cd orchestrators-backend && ... -m unittest discover -s tests",
                 "timeoutSeconds": 900}],
     "keepRuns": 5}

Per run (`Workspace.start_run`): fetch in the clone, then `git worktree add` a
fresh worktree at `<root>/.runs/<run-id>` on branch `agent/<run-id>` from
`origin/<branch>` (an orphan branch while the remote is still empty). A run on
a task whose earlier attempt left unpushed commits gets them cherry-picked on
top, so a send-back continues from there; pushed work is already upstream.

The worker's code tools (services/agent_tools.code_tools) act on a
`RunWorkspace`: every path is resolved inside the worktree, `.git/` and
secrets (.env, keys) are refused, reads and writes are size-limited and output
is truncated. Checks are the named commands of the config, nothing else; they
run with a minimal environment (no API keys) and a timeout.

Landing (`RunWorkspace.land`, on finish_run(succeeded)): fetch, rebase onto
origin/<branch> (a conflict aborts the rebase and fails), run the checks, push
HEAD to <branch>; a rejected push is retried once (fetch, rebase, checks, push).
Commits are made as fibr <vectra14@gmail.com> (ORCH_GIT_NAME / ORCH_GIT_EMAIL)
with `Agent-Run:` and `Task:` trailers. Git hooks never run.

Shared-clone operations (fetch, worktree add/remove, landing) hold a per-clone
lock, so concurrent runs of one project land one after the other.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

GIT_NAME = os.environ.get("ORCH_GIT_NAME", "fibr")
GIT_EMAIL = os.environ.get("ORCH_GIT_EMAIL", "vectra14@gmail.com")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
RUNS_DIR = ".runs"

MAX_READ_BYTES = 400_000  # files larger than this are not read
MAX_READ_CHARS = 30_000  # characters returned by one read_file call
MAX_WRITE_BYTES = 400_000
MAX_OUTPUT = 12_000  # characters of command / diff output returned to the model
MAX_LIST = 400
MAX_MATCHES = 120


class WorkspaceError(Exception):
    """A refused or failed workspace operation (shown to the model)."""

    def __init__(self, message: str, code: str = "workspace"):
        super().__init__(message)
        self.code = code


class LandingError(WorkspaceError):
    """Landing failed. kind: dirty | conflict | checks | push."""

    def __init__(self, kind: str, message: str, output: str = ""):
        super().__init__(message, code=kind)
        self.kind = kind
        self.output = output


# ---- config ------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class Check(_Model):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    description: str = ""


class WorkspaceConfig(_Model):
    remote: str = Field(min_length=1)
    root: str = Field(min_length=1)
    branch: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]+$")
    web_url: str | None = None  # e.g. https://github.com/owner/repo; derived from remote if unset
    checks: list[Check] = Field(default_factory=list)
    keep_runs: int = Field(default=5, ge=0, le=50)

    @field_validator("root")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not os.path.isabs(v):
            raise ValueError("root must be an absolute path")
        return os.path.normpath(v)

    @field_validator("checks")
    @classmethod
    def _unique(cls, v: list[Check]) -> list[Check]:
        names = [c.name for c in v]
        if len(names) != len(set(names)):
            raise ValueError("check names must be unique")
        return v

    def check(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    def web(self) -> str | None:
        return (self.web_url or "").rstrip("/") or derive_web_url(self.remote)


def derive_web_url(remote: str) -> str | None:
    """https://github.com/<owner>/<repo> for GitHub remotes, including SSH host
    aliases such as `git@github-orchestrators:kyrill99/Orchestrators.git`."""
    m = (re.match(r"^(?:ssh://)?[^@/\s]+@([^:/\s]+)[:/](?:\d+/)?(.+?)(?:\.git)?/?$", remote)
         or re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", remote))
    if not m:
        return None
    host, path = m.group(1), m.group(2)
    if "github" not in host or path.count("/") != 1:
        return None
    return f"https://github.com/{path}"


# ---- git ---------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": GIT_NAME, "GIT_AUTHOR_EMAIL": GIT_EMAIL,
        "GIT_COMMITTER_NAME": GIT_NAME, "GIT_COMMITTER_EMAIL": GIT_EMAIL,
        "LC_ALL": "C",
    }


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 180,
        input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run git with hooks off and the agents' identity."""
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
           "-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}",
           "-c", "core.quotepath=off", *args]
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=_git_env(), input=input, errors="replace")
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"git {args[0]} timed out after {timeout:g} s") from None
    if check and p.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args[:3])} failed: {(p.stderr or p.stdout).strip()[-1500:]}")
    return p


def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    """Head and tail of long output (test failures are usually at the end)."""
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    return f"{text[:head]}\n… [{len(text) - limit} characters cut] …\n{text[-tail:]}"


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(root: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(os.path.realpath(root), threading.Lock())


# ---- the shared clone --------------------------------------------------------


class Workspace:
    """A project's clone (`config.root`) and its per-run worktrees."""

    def __init__(self, config: WorkspaceConfig, project_id: str = "default"):
        self.config = config
        self.project_id = project_id
        self.root = Path(config.root)
        self.lock = _lock(config.root)

    @property
    def upstream_ref(self) -> str:
        return f"refs/remotes/origin/{self.config.branch}"

    def ensure_clone(self) -> None:
        if (self.root / ".git").exists():
            return
        if self.root.exists() and any(self.root.iterdir()):
            raise WorkspaceError(f"{self.root} exists but is not a git clone")
        self.root.parent.mkdir(parents=True, exist_ok=True)
        git(self.root.parent, "clone", self.config.remote, str(self.root), timeout=900)

    def fetch(self) -> None:
        git(self.root, "fetch", "--prune", "origin", timeout=300)

    def upstream(self) -> str | None:
        p = git(self.root, "rev-parse", "--verify", "-q", f"{self.upstream_ref}^{{commit}}", check=False)
        return p.stdout.strip() or None

    def _branch_exists(self, branch: str) -> bool:
        return git(self.root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0

    def _exclude_runs_dir(self) -> None:
        info = self.root / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        text = exclude.read_text() if exclude.exists() else ""
        if f"/{RUNS_DIR}/" not in text.split("\n"):
            exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + f"/{RUNS_DIR}/\n")

    def start_run(self, run_id: str, task_id: str, previous_runs: list[str] | None = None) -> RunWorkspace:
        """A fresh worktree for `run_id` from the latest origin/<branch>, plus the
        unpushed commits of the newest earlier run of the task that has any."""
        if not re.match(r"^[A-Za-z0-9._-]+$", run_id):
            raise WorkspaceError(f"bad run id {run_id!r}")
        branch = f"agent/{run_id}"
        path = self.root / RUNS_DIR / run_id
        with self.lock:
            self.ensure_clone()
            self.fetch()
            self._exclude_runs_dir()
            base = self.upstream()
            if path.exists():  # leftover of an earlier process: start clean
                git(self.root, "worktree", "remove", "--force", str(path), check=False)
                shutil.rmtree(path, ignore_errors=True)
            git(self.root, "worktree", "prune")
            if self._branch_exists(branch):
                git(self.root, "branch", "-D", branch)
            path.parent.mkdir(parents=True, exist_ok=True)
            if base:
                git(self.root, "worktree", "add", "-b", branch, str(path), base)
            else:  # the remote has no commits yet
                git(self.root, "worktree", "add", "--orphan", "-b", branch, str(path))
            rw = RunWorkspace(self, run_id, task_id, path, base, branch)
            for prev in previous_runs or []:
                prev_branch = f"agent/{prev}"
                if prev == run_id or not self._branch_exists(prev_branch):
                    continue
                rw.carry_over(prev_branch)
                break
        return rw

    def runs_on_disk(self) -> list[Path]:
        d = self.root / RUNS_DIR
        if not d.is_dir():
            return []
        return sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)

    def cleanup(self, keep: int | None = None, active: set[str] | frozenset[str] = frozenset()) -> list[str]:
        """Remove run worktrees (and their branches) beyond the newest `keep`;
        worktrees of `active` runs stay. Returns the removed run ids."""
        keep = self.config.keep_runs if keep is None else keep
        if not (self.root / ".git").exists():
            return []
        removed = []
        with self.lock:
            kept = 0
            for p in self.runs_on_disk():
                if p.name in active:
                    continue
                if kept < keep:
                    kept += 1
                    continue
                git(self.root, "worktree", "remove", "--force", str(p), check=False)
                shutil.rmtree(p, ignore_errors=True)
                git(self.root, "branch", "-D", f"agent/{p.name}", check=False)
                removed.append(p.name)
            git(self.root, "worktree", "prune", check=False)
        return removed


# ---- one run's worktree -------------------------------------------------------

_SECRET_NAMES = re.compile(
    r"^(\.env(\..*)?|\.envrc|\.netrc|\.npmrc|\.pypirc|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
    r".*\.(pem|key|p12|pfx|jks|keystore|asc|gpg)|credentials(\..*)?|secrets?(\..*)?|"
    r"service[-_]account.*\.json)$",
    re.IGNORECASE,
)
_SECRET_OK = re.compile(r"^\.env\.(example|sample|template|dist)$", re.IGNORECASE)


def is_secret(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in (".ssh", ".gnupg") for p in parts):
        return True
    name = parts[-1] if parts else ""
    return bool(_SECRET_NAMES.match(name)) and not _SECRET_OK.match(name)


def _glob_match(path: str, pattern: str) -> bool:
    """`*` and `**` match across directories; a pattern without '/' also matches the file name."""
    pattern = pattern.strip().lstrip("./") or "*"
    if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", "")):
        return True
    return "/" not in pattern and fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)


def _minimal_env(home: str) -> dict[str, str]:
    """Environment for checks: no API keys, tokens or service configuration."""
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME")
           if k in os.environ}
    env.update({"HOME": home, "CI": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "NEXT_TELEMETRY_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"})
    return env


@dataclass
class RunWorkspace:
    ws: Workspace
    run_id: str
    task_id: str
    path: Path
    base: str | None  # origin/<branch> when the run started (None: empty remote)
    branch: str
    carried: list[str] = field(default_factory=list)  # commits taken over from an earlier run
    notes: list[str] = field(default_factory=list)
    landed: dict[str, Any] | None = None
    check_failures: int = 0

    # ---- paths ----

    def resolve(self, rel: str, *, write: bool = False) -> Path:
        rel = (rel or "").strip()
        if not rel:
            raise WorkspaceError("path is empty")
        root = self.path.resolve()
        p = Path(rel)
        target = (p if p.is_absolute() else root / p).resolve()
        try:
            inside = target.relative_to(root)
        except ValueError:
            raise WorkspaceError(f"{rel}: outside the repository") from None
        parts = inside.parts
        if not parts and write:
            raise WorkspaceError("cannot write the repository root")
        if ".git" in parts:
            raise WorkspaceError(f"{rel}: .git is off limits")
        if is_secret(str(inside)):
            raise WorkspaceError(f"{rel}: secrets and keys are off limits")
        return target

    def rel(self, p: Path) -> str:
        return str(p.resolve().relative_to(self.path.resolve()))

    def g(self, *args: str, check: bool = True, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        return git(self.path, *args, check=check, timeout=timeout)

    # ---- reading ----

    def files(self) -> list[str]:
        out = self.g("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
        seen, files = set(), []
        for f in out.split("\0"):
            if f and f not in seen and (self.path / f).is_file():
                seen.add(f)
                files.append(f)
        return sorted(files)

    def list_files(self, glob: str = "**") -> dict[str, Any]:
        files = [f for f in self.files() if _glob_match(f, glob) and not is_secret(f)]
        out: dict[str, Any] = {"count": len(files), "files": files[:MAX_LIST]}
        if len(files) > MAX_LIST:
            out["note"] = f"showing {MAX_LIST} of {len(files)}; narrow the glob"
        return out

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        p = self.resolve(path)
        if not p.is_file():
            raise WorkspaceError(f"{path}: no such file")
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            raise WorkspaceError(f"{path}: {size} bytes is too large to read (limit {MAX_READ_BYTES}); "
                                 "use search_code")
        data = p.read_bytes()
        if b"\0" in data[:8000]:
            raise WorkspaceError(f"{path}: binary file")
        lines = data.decode("utf-8", errors="replace").splitlines()
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        body, chars, last = [], 0, start - 1
        width = len(str(end))
        for n in range(start, end + 1):
            line = f"{n:>{width}}  {lines[n - 1]}"
            if chars + len(line) > MAX_READ_CHARS and body:
                break
            body.append(line)
            chars += len(line) + 1
            last = n
        out: dict[str, Any] = {"path": self.rel(p), "totalLines": len(lines),
                               "lines": f"{start}-{last}", "content": "\n".join(body)}
        if last < end:
            out["note"] = f"truncated at line {last}; read on with start_line={last + 1}"
        return out

    def search_code(self, pattern: str, glob: str | None = None, ignore_case: bool = False) -> dict[str, Any]:
        if not pattern:
            raise WorkspaceError("pattern is empty")
        args = ["grep", "-n", "-I", "-E", "--untracked", "--no-color", "-e", pattern]
        if ignore_case:
            args.insert(1, "-i")
        p = self.g(*args, check=False, timeout=60)
        if p.returncode not in (0, 1):
            raise WorkspaceError(f"search failed: {p.stderr.strip()[:500]}")
        matches = []
        for line in p.stdout.splitlines():
            path = line.split(":", 1)[0]
            if is_secret(path) or (glob and not _glob_match(path, glob)):
                continue
            matches.append(line if len(line) <= 300 else line[:300] + "…")
        out: dict[str, Any] = {"count": len(matches), "matches": matches[:MAX_MATCHES]}
        if len(matches) > MAX_MATCHES:
            out["note"] = f"showing {MAX_MATCHES} of {len(matches)}; narrow the pattern or glob"
        return out

    # ---- writing ----

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        p = self.resolve(path, write=True)
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise WorkspaceError(f"content is {len(data)} bytes; the limit is {MAX_WRITE_BYTES}")
        if p.is_dir():
            raise WorkspaceError(f"{path} is a directory")
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": self.rel(p), "bytes": len(data), "created": not existed}

    def edit_file(self, path: str, old: str, new: str) -> dict[str, Any]:
        p = self.resolve(path, write=True)
        if not p.is_file():
            raise WorkspaceError(f"{path}: no such file")
        if not old:
            raise WorkspaceError("old is empty")
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise WorkspaceError(f"{path}: old text found {n} times; it must match exactly once "
                                 "(include more surrounding lines)" if n else
                                 f"{path}: old text not found (it must match exactly, whitespace included)")
        updated = text.replace(old, new, 1)
        if len(updated.encode("utf-8")) > MAX_WRITE_BYTES:
            raise WorkspaceError(f"result exceeds {MAX_WRITE_BYTES} bytes")
        p.write_text(updated, encoding="utf-8")
        line = text[: text.index(old)].count("\n") + 1
        return {"path": self.rel(p), "line": line, "removedLines": old.count("\n") + 1,
                "addedLines": new.count("\n") + 1}

    def delete_file(self, path: str) -> dict[str, Any]:
        p = self.resolve(path, write=True)
        if not p.is_file():
            raise WorkspaceError(f"{path}: no such file")
        p.unlink()
        return {"path": path, "deleted": True}

    # ---- git ----

    def _base_rev(self) -> str:
        return self.base or EMPTY_TREE

    def ahead(self, since: str | None = None) -> list[dict[str, str]]:
        """Commits on HEAD that are not in `since` (default: the run's base)."""
        if self.g("rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
            return []  # orphan branch without commits
        since = since if since is not None else self.base
        rng = [f"{since}..HEAD"] if since else ["HEAD"]
        out = self.g("log", "--reverse", "--format=%H%x1f%s", *rng).stdout
        web = self.ws.config.web()
        commits = []
        for line in out.splitlines():
            sha, _, subject = line.partition("\x1f")
            c = {"sha": sha, "short": sha[:8], "subject": subject}
            if web:
                c["url"] = f"{web}/commit/{sha}"
            commits.append(c)
        return commits

    def dirty(self) -> list[str]:
        out = self.g("status", "--porcelain=v1", "-uall").stdout
        return [line for line in out.splitlines() if line.strip()]

    def status(self) -> dict[str, Any]:
        return {"branch": self.branch, "base": (self.base or "")[:8] or "(empty repository)",
                "commitsAhead": [f"{c['short']} {c['subject']}" for c in self.ahead()],
                "uncommitted": self.dirty()[:200]}

    def diff(self, stat_only: bool = False, path: str | None = None) -> dict[str, Any]:
        """Everything changed since the run's base: commits plus uncommitted edits
        (new files included)."""
        self.g("add", "-A", "-N")  # untracked files show up in the diff
        spec = ["--", path] if path else []
        if path:
            self.resolve(path)
        stat = self.g("diff", "--stat=120", self._base_rev(), *spec).stdout
        out: dict[str, Any] = {"stat": clip(stat, 4000)}
        if not stat_only:
            out["diff"] = clip(self.g("diff", self._base_rev(), *spec).stdout)
        return out

    def changed_files(self, since: str | None) -> list[dict[str, Any]]:
        out = self.g("diff", "--numstat", since or EMPTY_TREE, "HEAD").stdout
        files = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                a, d, f = parts
                files.append({"path": f, "added": int(a) if a.isdigit() else None,
                              "removed": int(d) if d.isdigit() else None})
        return files

    def commit(self, message: str) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            raise WorkspaceError("commit message is empty")
        self.g("add", "-A")
        staged = [f for f in self.g("diff", "--cached", "--name-only", "-z").stdout.split("\0") if f]
        bad = [f for f in staged if is_secret(f) or f.split("/")[0] == ".git"]
        if bad:
            self.g("reset", "-q", "--", *bad, check=False)
            raise WorkspaceError(f"refusing to commit secrets: {', '.join(bad)}")
        if not staged:
            raise WorkspaceError("nothing to commit")
        self.g("commit", "-q", "-m", message,
               "--trailer", f"Agent-Run: {self.run_id}",
               "--trailer", f"Task: {self.ws.project_id}/{self.task_id}")
        sha = self.g("rev-parse", "HEAD").stdout.strip()
        return {"commit": sha[:8], "files": staged[:100], "message": message.splitlines()[0]}

    def carry_over(self, prev_branch: str) -> None:
        """Cherry-pick an earlier run's commits that are not upstream yet."""
        if self.base:
            rng = ["--cherry-pick", "--right-only", "--no-merges", f"{self.base}...{prev_branch}"]
        else:
            rng = [prev_branch]
        shas = self.g("rev-list", "--reverse", *rng, check=False).stdout.split()
        if not shas:
            return
        if self.base is None:
            self.g("reset", "-q", "--hard", prev_branch)
            self.carried = shas
        else:
            p = self.g("cherry-pick", *shas, check=False)
            if p.returncode != 0:
                self.g("cherry-pick", "--abort", check=False)
                self.notes.append(f"The previous attempt's unpushed commits on {prev_branch} do not "
                                  "apply to the latest main; start from main.")
                return
            self.carried = shas
        self.notes.append(f"Carried over {len(shas)} unpushed commit(s) from the previous attempt "
                          f"({prev_branch}).")

    # ---- checks ----

    def run_check(self, name: str) -> dict[str, Any]:
        check = self.ws.config.check(name)
        if check is None:
            names = ", ".join(c.name for c in self.ws.config.checks) or "none configured"
            raise WorkspaceError(f"unknown check '{name}' (available: {names})")
        home = os.environ.get("HOME") or str(self.path)
        started = time.monotonic()
        proc = subprocess.Popen(["bash", "-c", check.command], cwd=str(self.path), env=_minimal_env(home),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, text=True, errors="replace")
        timed_out = False
        try:
            output, _ = proc.communicate(timeout=check.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            output, _ = proc.communicate()
        return {"check": name, "passed": proc.returncode == 0 and not timed_out,
                "exitCode": None if timed_out else proc.returncode, "timedOut": timed_out,
                "seconds": round(time.monotonic() - started, 1), "output": clip(output or "")}

    def run_checks(self) -> list[dict[str, Any]]:
        results = []
        for c in self.ws.config.checks:
            r = self.run_check(c.name)
            results.append(r)
            if not r["passed"]:
                break
        return results

    # ---- landing ----

    def land(self) -> dict[str, Any]:
        """Rebase onto origin/<branch>, run the checks, push. Returns what was
        pushed ({"pushed": False} when there is nothing to push). Raises
        LandingError."""
        dirty = self.dirty()
        if dirty:
            raise LandingError("dirty", "uncommitted changes: commit them (commit) or undo them "
                                        "before finishing:\n" + "\n".join(dirty[:30]))
        if not self.ahead():
            return {"pushed": False, "commits": []}
        cfg = self.ws.config
        checks: list[dict[str, Any]] = []
        with self.ws.lock:
            checked_at = None
            for attempt in (1, 2):
                self.ws.fetch()
                upstream = self.ws.upstream()
                if upstream and self.g("merge-base", "--is-ancestor", upstream, "HEAD",
                                       check=False).returncode != 0:
                    p = self.g("rebase", upstream, check=False, timeout=300)
                    if p.returncode != 0:
                        conflicts = self.g("diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
                        self.g("rebase", "--abort", check=False)
                        raise LandingError(
                            "conflict",
                            f"rebase onto origin/{cfg.branch} conflicts"
                            + (f" in {', '.join(conflicts[:20])}" if conflicts else "")
                            + "; nothing was pushed. Restart the task to redo the change on the latest main.",
                            clip(p.stdout + p.stderr, 3000))
                head = self.g("rev-parse", "HEAD").stdout.strip()
                if cfg.checks and head != checked_at:
                    checks = self.run_checks()
                    failed = next((c for c in checks if not c["passed"]), None)
                    if failed:
                        why = "timed out" if failed["timedOut"] else f"exit {failed['exitCode']}"
                        raise LandingError("checks", f"check '{failed['check']}' failed ({why}); "
                                                     "nothing was pushed", failed["output"])
                    checked_at = head
                p = self.g("push", "origin", f"HEAD:refs/heads/{cfg.branch}", check=False, timeout=300)
                if p.returncode == 0:
                    break
                if attempt == 2:
                    raise LandingError("push", f"push to {cfg.branch} was rejected twice",
                                       clip(p.stderr, 3000))
            commits = self.ahead(upstream or "")
            self.ws.fetch()
        web = cfg.web()
        after = commits[-1]["sha"] if commits else None
        if web and upstream and after:
            compare = f"{web}/compare/{upstream[:12]}...{after[:12]}"
        elif web and after:
            compare = f"{web}/commits/{after}"
        else:
            compare = None
        files = self.changed_files(upstream)
        self.landed = {
            "pushed": True, "branch": cfg.branch, "before": upstream, "after": after,
            "commits": commits, "files": files[:200], "compareUrl": compare,
            "added": sum(f["added"] or 0 for f in files), "removed": sum(f["removed"] or 0 for f in files),
            "checks": [{"check": c["check"], "passed": c["passed"], "seconds": c["seconds"]} for c in checks],
        }
        return self.landed
