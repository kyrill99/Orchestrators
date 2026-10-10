"""Self-update trigger. Mount with:

    app.include_router(build_router("data"))

POST only drops `update.request` into the data dir; on the GCP deploy a
systemd path unit picks it up and runs the root-owned update script
(deploy/gcp/update.sh), which writes `update-status.json` back here.
The backend itself never gets privileges. Access control is oauth2-proxy.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import APIRouter

REQUEST = "update.request"
STATUS = "update-status.json"


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

    def status() -> dict:
        try:
            last = json.loads((data / STATUS).read_text())
        except (OSError, ValueError):
            last = None
        return {
            "commit": _commit(),
            "requested": (data / REQUEST).exists(),
            "last": last,
        }

    @r.get("")
    def get_status():
        return status()

    @r.post("", status_code=202)
    def request_update():
        data.mkdir(parents=True, exist_ok=True)
        (data / REQUEST).touch()
        return status()

    return r
