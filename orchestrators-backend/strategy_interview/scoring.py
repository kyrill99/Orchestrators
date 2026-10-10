"""Readiness, gap priority and the stop rule. Plain code, no model calls.

    readiness  = sum(w_i * c_i) / sum(w_i)
    priority_i = k_i * w_i * (1 - c_i)     k_i = 2 for an unknown required item, else 1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from .schema import OWNER_SOURCES, ItemKey, StrategyPicture, Tracked

WEIGHTS: dict[ItemKey, float] = {
    ItemKey.GOAL: 3.0,
    ItemKey.PROJECT_TYPE: 3.0,
    ItemKey.SUCCESS_CRITERIA: 2.0,
    ItemKey.LIFESPAN: 2.0,
    ItemKey.SPEED_VS_QUALITY: 2.0,
    ItemKey.AUDIENCE: 1.0,
    ItemKey.CONSTRAINTS: 1.0,
    ItemKey.EXPLORE_VS_FOCUS: 1.0,
    ItemKey.SIMPLICITY_VS_EXTENSIBILITY: 1.0,
    ItemKey.CAUTION_VS_BOLDNESS: 1.0,
    ItemKey.NON_GOALS: 0.5,
}

REQUIRED = frozenset({ItemKey.GOAL, ItemKey.PROJECT_TYPE, ItemKey.SUCCESS_CRITERIA})

# Items that only count as known when the owner stated or confirmed them,
# because they set every default below them.
OWNER_ONLY = frozenset({ItemKey.PROJECT_TYPE})


@dataclass(frozen=True)
class ScoringConfig:
    known_threshold: float = 0.7
    ready_threshold: float = 0.75
    max_questions: int = 6  # including the opening question
    max_asks_per_item: int = 2
    confirm_threshold: float = 0.4  # a guess at least this sure is confirmed, not asked open


class Readiness(BaseModel):
    score: float
    ready: bool
    known: list[ItemKey]
    unknown: list[ItemKey]
    required_missing: list[ItemKey]


class Gap(BaseModel):
    key: ItemKey
    mode: Literal["open", "confirm"]
    priority: float


def counted_confidence(tracked: Tracked) -> float:
    return tracked.confidence if tracked.is_set else 0.0


def is_known(key: ItemKey, tracked: Tracked, cfg: ScoringConfig) -> bool:
    if not tracked.is_set:
        return False
    if key in OWNER_ONLY and tracked.source not in OWNER_SOURCES:
        return False
    return counted_confidence(tracked) >= cfg.known_threshold


def readiness(picture: StrategyPicture, cfg: ScoringConfig | None = None) -> Readiness:
    cfg = cfg or ScoringConfig()
    total = sum(WEIGHTS.values())
    score = 0.0
    known: list[ItemKey] = []
    unknown: list[ItemKey] = []
    for key, weight in WEIGHTS.items():
        tracked = picture.get(key)
        score += weight * counted_confidence(tracked)
        (known if is_known(key, tracked, cfg) else unknown).append(key)
    score = round(score / total, 3)
    required_missing = [k for k in unknown if k in REQUIRED]
    return Readiness(
        score=score,
        ready=not required_missing and score >= cfg.ready_threshold,
        known=known,
        unknown=unknown,
        required_missing=required_missing,
    )


def priority(key: ItemKey, tracked: Tracked, cfg: ScoringConfig) -> float:
    c = counted_confidence(tracked)
    if key in OWNER_ONLY and tracked.source not in OWNER_SOURCES:
        c = min(c, cfg.known_threshold - 0.01)  # unconfirmed type is still a gap
    k = 2.0 if key in REQUIRED and not is_known(key, tracked, cfg) else 1.0
    return round(k * WEIGHTS[key] * (1.0 - c), 4)


def pick_gap(
    picture: StrategyPicture,
    ask_counts: dict[ItemKey, int] | None = None,
    cfg: ScoringConfig | None = None,
) -> Gap | None:
    """The highest-priority unknown item that has not been asked too often."""
    cfg = cfg or ScoringConfig()
    ask_counts = ask_counts or {}
    best: Gap | None = None
    for key in WEIGHTS:  # dict order breaks ties: heavier, earlier items first
        tracked = picture.get(key)
        if is_known(key, tracked, cfg):
            continue
        if ask_counts.get(key, 0) >= cfg.max_asks_per_item:
            continue
        p = priority(key, tracked, cfg)
        if p <= 0:
            continue
        mode: Literal["open", "confirm"] = (
            "confirm"
            if tracked.is_set and counted_confidence(tracked) >= cfg.confirm_threshold
            else "open"
        )
        if best is None or p > best.priority:
            best = Gap(key=key, mode=mode, priority=p)
    return best
