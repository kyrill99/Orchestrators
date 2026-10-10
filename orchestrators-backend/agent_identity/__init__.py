"""Who Orchestrators is and what each agent's role is, as prompt text.

Every agent starts from the same identity (identity.md), then its role card. The text
lives in the Markdown files next to this module so it can be read and edited as prose;
it is read on each call, so edits apply without a restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

Role = Literal["strategy_partner", "planner", "worker"]

_DIR = Path(__file__).resolve().parent


def _read(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def system_prompt(role: Role) -> str:
    """The shared identity followed by the role card for `role`."""
    return f"{_read('identity')}\n\n{_read(role)}"
