"""Host CPU / memory usage for the sidebar indicators. Mount with:

    app.include_router(build_router())

Reads /proc directly (Linux only, no extra dependency). CPU is the busy
share since the previous request, so the first call after start returns
the average since boot.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException


def read_cpu(proc: Path) -> tuple[int, int]:
    """(busy, total) jiffies from the aggregate `cpu` line of /proc/stat."""
    fields = [int(v) for v in (proc / "stat").read_text().split("\n", 1)[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
    total = sum(fields[:8])  # guest time is already counted in user/nice
    return total - idle, total


def read_memory(proc: Path) -> dict[str, int]:
    """Bytes from /proc/meminfo."""
    info = {}
    for line in (proc / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0]) * 1024
    return {
        "total": info["MemTotal"],
        "used": info["MemTotal"] - info.get("MemAvailable", info["MemFree"]),
        "swap_total": info.get("SwapTotal", 0),
        "swap_used": info.get("SwapTotal", 0) - info.get("SwapFree", 0),
    }


def build_router(proc: str | Path = "/proc") -> APIRouter:
    proc = Path(proc)
    r = APIRouter(prefix="/api/admin/system", tags=["admin"])
    lock = threading.Lock()
    last = [0, 0]  # busy, total at the previous request

    @r.get("")
    def get_system():
        try:
            busy, total = read_cpu(proc)
            memory = read_memory(proc)
        except (OSError, ValueError, KeyError, IndexError) as e:
            raise HTTPException(503, f"stats unavailable: {e}") from e
        with lock:
            d_busy, d_total = busy - last[0], total - last[1]
            last[:] = [busy, total]
        return {
            "cpu": {
                "percent": round(100 * d_busy / d_total, 1) if d_total > 0 else 0.0,
                "cores": os.cpu_count(),
                "load": [round(x, 2) for x in os.getloadavg()],
            },
            "memory": memory,
        }

    return r
