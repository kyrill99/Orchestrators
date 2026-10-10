"""Deterministic merge rules: validate a proposed value, then apply source ranks.

Gemini proposes; this module decides. A lower-ranked source never overwrites a
higher-ranked one; at equal rank the newer value wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import (
    LEAN_MAX,
    LEAN_MIN,
    LIST_ITEMS,
    MAX_ENTRY_CHARS,
    MAX_EVIDENCE_CHARS,
    MAX_GOAL_CHARS,
    MAX_LIST_ENTRIES,
    SOURCE_RANK,
    ItemKey,
    Lifespan,
    ProjectType,
    Source,
    StrategyPicture,
    Tracked,
    utcnow,
)

# Bounds on stored confidence per source (see spec: Core concepts).
_CONFIDENCE_FLOOR = {Source.STATED: 0.9, Source.CONFIRMED: 0.9}
_CONFIDENCE_CEILING = {Source.INFERRED: 0.85, Source.MEMORY: 0.8, Source.DEFAULT: 0.5}


class InvalidValue(ValueError):
    """A proposed value does not fit the item's type."""


@dataclass(frozen=True)
class Proposal:
    key: ItemKey
    value: Any
    source: Source
    confidence: float
    evidence: str | None = None


def clamp_confidence(source: Source, confidence: float) -> float:
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        c = 0.0
    c = max(0.0, min(1.0, c))
    c = max(c, _CONFIDENCE_FLOOR.get(source, 0.0))
    c = min(c, _CONFIDENCE_CEILING.get(source, 1.0))
    return round(c, 3)


def _clean_text(raw: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(raw)).strip()
    return text[:limit].rstrip()


def _enum_value(enum_cls: type, raw: Any) -> Any:
    if isinstance(raw, enum_cls):
        return raw
    text = _clean_text(raw, 40).lower()
    for member in enum_cls:
        if text == member.value:
            return member
    raise InvalidValue(f"{text!r} is not one of {[m.value for m in enum_cls]}")


def normalize_value(key: ItemKey, raw: Any) -> Any:
    """Coerce a raw value (from Gemini or the UI) into the item's type."""
    if key in LIST_ITEMS:
        if raw is None:
            raw = []
        if isinstance(raw, str):
            raw = [raw]
        seen: set[str] = set()
        out: list[str] = []
        for entry in raw:
            text = _clean_text(entry, MAX_ENTRY_CHARS)
            if text and text.lower() not in seen:
                seen.add(text.lower())
                out.append(text)
            if len(out) == MAX_LIST_ENTRIES:
                break
        return out

    if isinstance(raw, list):
        if not raw:
            raise InvalidValue(f"{key.value} needs a value")
        raw = raw[0]
    if raw is None:
        raise InvalidValue(f"{key.value} needs a value")

    if key is ItemKey.PROJECT_TYPE:
        return _enum_value(ProjectType, raw)
    if key is ItemKey.LIFESPAN:
        return _enum_value(Lifespan, raw)
    if key.axis is not None:
        try:
            lean = int(round(float(str(raw).strip())))
        except ValueError as exc:
            raise InvalidValue(f"lean for {key.value} must be a number") from exc
        return max(LEAN_MIN, min(LEAN_MAX, lean))

    limit = MAX_GOAL_CHARS if key is ItemKey.GOAL else MAX_ENTRY_CHARS
    text = _clean_text(raw, limit)
    if not text:
        raise InvalidValue(f"{key.value} needs a value")
    return text


def apply(picture: StrategyPicture, proposal: Proposal, turn: int | None) -> bool:
    """Apply one proposal to ``picture`` (mutates it). Returns True if it changed.

    Raises ``InvalidValue`` if the value does not fit the item.
    """
    value = normalize_value(proposal.key, proposal.value)
    current = picture.get(proposal.key)
    new_rank = SOURCE_RANK[proposal.source]

    if current.is_set and SOURCE_RANK[current.source] > new_rank:
        return False  # lower-ranked sources never overwrite higher-ranked ones

    confidence = clamp_confidence(proposal.source, proposal.confidence)
    evidence = (
        _clean_text(proposal.evidence, MAX_EVIDENCE_CHARS) if proposal.evidence else None
    )

    if (
        current.is_set
        and current.value == value
        and current.source == proposal.source
        and current.confidence >= confidence
    ):
        return False  # nothing new

    picture.put(
        proposal.key,
        tracked_class(proposal.key)(
            value=value,
            source=proposal.source,
            confidence=confidence,
            evidence=evidence,
            turn=turn,
        ),
    )
    picture.revision += 1
    picture.updated_at = utcnow()
    return True


def tracked_class(key: ItemKey) -> type[Tracked[Any]]:
    """The concrete ``Tracked[...]`` class for an item, e.g. ``Tracked[ProjectType]``."""
    if key.axis is not None:
        from .schema import Tradeoffs

        return Tradeoffs.model_fields[key.axis.value].annotation  # type: ignore[return-value]
    return StrategyPicture.model_fields[key.value].annotation  # type: ignore[return-value]
