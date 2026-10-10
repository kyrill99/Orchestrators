"""Turning one owner answer into picture updates. Shared by the interview and
by ask_strategy, so an owner answer lands the same way wherever it was asked."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from .defaults import apply_type_defaults
from .llm import LLMError, StructuredLLM
from .llm_io import ExtractionResult
from .merge import InvalidValue, Proposal, apply, tracked_class
from .prompts import EXTRACT_SYSTEM, build_extract_prompt
from .questions import Question, TranscriptEntry
from .schema import ItemKey, OwnerProfile, Source, StrategyPicture, utcnow

log = logging.getLogger("strategy_interview.updating")

EXTRACT_TEMPERATURE = 0.2
MAX_ANSWER_CHARS = 4000

_AFFIRMATIVE = re.compile(
    r"^\s*(yes|yeah|yep|yup|ja|jep|genau|right|correct|exactly|sure|indeed|absolutely|"
    r"definitely|that'?s right|sounds (good|right)|true|ok|okay)\b",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(r"^\s*(no|nope|nah|nein|not really|wrong|not quite)\b", re.IGNORECASE)


def is_affirmative(text: str) -> bool:
    return bool(_AFFIRMATIVE.match(text)) and not _NEGATIVE.match(text)


def is_negative(text: str) -> bool:
    return bool(_NEGATIVE.match(text))


@dataclass
class UpdateOutcome:
    changed: list[ItemKey] = field(default_factory=list)
    wants_to_stop: bool = False
    model_ok: bool = True


def _add(changed: list[ItemKey], keys: Iterable[ItemKey]) -> None:
    for k in keys:
        if k not in changed:
            changed.append(k)


def extract_and_merge(
    llm: StructuredLLM,
    picture: StrategyPicture,
    question: Question | None,
    answer: str,
    transcript_tail: Iterable[TranscriptEntry],
    turn: int | None,
    profile: OwnerProfile | None = None,
) -> UpdateOutcome:
    """Extract updates from ``answer`` and merge them into ``picture`` (mutated)."""
    outcome = UpdateOutcome()
    answer = answer.strip()[:MAX_ANSWER_CHARS]
    type_before = picture.project_type.value

    result: ExtractionResult | None
    try:
        result = llm.generate(
            role="main",
            system=EXTRACT_SYSTEM,
            prompt=build_extract_prompt(picture, question, answer, transcript_tail),
            schema=ExtractionResult,
            temperature=EXTRACT_TEMPERATURE,
        )
    except LLMError as exc:
        log.warning("extraction failed; no updates this turn: %s", exc)
        result = None
        outcome.model_ok = False

    if result is not None:
        outcome.wants_to_stop = result.wants_to_stop
        for item in result.items:
            source = Source(item.source)
            # "confirmed" only makes sense as an answer to a confirm question.
            if source is Source.CONFIRMED and not (
                question is not None and question.mode == "confirm"
            ):
                source = Source.STATED
            try:
                if apply(
                    picture,
                    Proposal(item.item, item.values, source, item.confidence, item.evidence),
                    turn,
                ):
                    _add(outcome.changed, [item.item])
            except InvalidValue as exc:
                log.info("dropped invalid value for %s: %s", item.item.value, exc)

    # Deterministic handling of a bare "yes" / "no" to a confirm question.
    if question is not None and question.mode == "confirm" and question.target is not None:
        target = question.target
        if target not in outcome.changed:
            current = picture.get(target)
            if is_affirmative(answer) and current.is_set:
                if apply(
                    picture,
                    Proposal(target, current.value, Source.CONFIRMED, 0.95, answer[:200]),
                    turn,
                ):
                    _add(outcome.changed, [target])
            elif is_negative(answer) and current.is_set and current.source not in (
                Source.STATED,
                Source.CONFIRMED,
            ):
                picture.put(target, tracked_class(target)())  # drop the rejected guess
                picture.revision += 1
                picture.updated_at = utcnow()
                _add(outcome.changed, [target])

    if picture.project_type.value != type_before:
        _add(outcome.changed, apply_type_defaults(picture, profile, turn))

    return outcome
