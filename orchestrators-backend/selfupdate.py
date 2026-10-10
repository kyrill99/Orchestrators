"""Self-update trigger. Mount with:

    app.include_router(build_router("data"))

POST only drops `update.request` into the data dir; on the GCP deploy a
systemd path unit picks it up and runs the root-owned update script
(deploy/gcp/update.sh), which writes `update-status.json` (per-step
progress) and `update.log` (command output) back here. GET returns both;
`commit` is the version this process is running, so the GUI can tell when
a restart brought in a new one. The backend itself never gets privileges.
Access control is oauth2-proxy.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, Query

REQUEST = "update.request"
STATUS = "update-status.json"
LOG = "update.log"


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_router(data_dir: str | Path) -> APIRouter:
    data = Path(data_dir)
    r = APIRouter(prefix="/api/admin/update", tags=["admin"])
    running = _commit()

    def log_tail(lines: int) -> list[str] | None:
        if not lines:
            return None
        try:
            return (data / LOG).read_text(errors="replace").splitlines()[-lines:]
        except OSError:
            return []

    def status(log: int = 0) -> dict:
        try:
            last = json.loads((data / STATUS).read_text())
        except (OSError, ValueError):
            last = None
        return {
            "commit": running,
            "requested": (data / REQUEST).exists(),
            "last": last,
            "log": log_tail(log),
        }

    @r.get("")
    def get_status(log: int = Query(0, ge=0, le=500)):
        return status(log)

    @r.post("", status_code=202)
    def request_update():
        data.mkdir(parents=True, exist_ok=True)
        (data / REQUEST).touch()
        return status()

    return r
