"""Writing and vetting questions for the owner.

Gemini phrases; a second, cheaper Gemini call guards; a fixed template is the
fallback. The owner never sees a question that failed the guard.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Literal

from pydantic import BaseModel

from .llm import LLMError, StructuredLLM
from .llm_io import GuardVerdict, QuestionDraft
from .prompts import (
    COMPOSE_SYSTEM,
    GUARD_SYSTEM,
    build_compose_prompt,
    build_guard_prompt,
)
from .schema import ItemKey, StrategyPicture
from .scoring import Gap
from .templates import describe_value, template_question

log = logging.getLogger("strategy_interview.questions")

MAX_QUESTION_CHARS = 240
COMPOSE_TEMPERATURE = 0.4
GUARD_TEMPERATURE = 0.0


class Question(BaseModel):
    text: str
    target: ItemKey | None = None  # None only for a generic ask_strategy question
    mode: Literal["opening", "open", "confirm"]
    origin: Literal["fixed", "model", "template"]


class TranscriptEntry(BaseModel):
    role: Literal["interviewer", "owner"]
    text: str
    turn: int
    target: ItemKey | None = None


def _shape_ok(text: str) -> bool:
    """Cheap structural checks before spending a guard call."""
    stripped = text.strip()
    return (
        bool(stripped)
        and len(stripped) <= MAX_QUESTION_CHARS
        and stripped.endswith("?")
        and stripped.count("?") == 1
    )


class QuestionWriter:
    def __init__(self, llm: StructuredLLM, regenerations: int = 1) -> None:
        self.llm = llm
        self.regenerations = regenerations

    def passes_guard(self, text: str) -> bool:
        if not _shape_ok(text):
            return False
        try:
            verdict = self.llm.generate(
                role="guard",
                system=GUARD_SYSTEM,
                prompt=build_guard_prompt(text),
                schema=GuardVerdict,
                temperature=GUARD_TEMPERATURE,
            )
        except LLMError as exc:
            log.warning("guard unavailable, treating question as rejected: %s", exc)
            return False
        if not verdict.strategy_level:
            log.info("guard rejected question: %s", verdict.reason)
        return verdict.strategy_level

    def compose(
        self,
        picture: StrategyPicture,
        gap: Gap,
        owner_name: str,
        transcript_tail: Iterable[TranscriptEntry],
    ) -> Question:
        guess = picture.get(gap.key).value if gap.mode == "confirm" else None
        guess_words = describe_value(gap.key, guess) if guess is not None else None
        tail = list(transcript_tail)
        for attempt in range(1 + self.regenerations):
            try:
                draft = self.llm.generate(
                    role="main",
                    system=COMPOSE_SYSTEM,
                    prompt=build_compose_prompt(
                        picture, gap.key, gap.mode, guess_words, owner_name, tail
                    ),
                    schema=QuestionDraft,
                    temperature=COMPOSE_TEMPERATURE,
                )
            except LLMError as exc:
                log.warning("compose failed, using template: %s", exc)
                break
            text = draft.question.strip()
            if self.passes_guard(text):
                return Question(text=text, target=gap.key, mode=gap.mode, origin="model")
            log.info("question rejected (attempt %d): %r", attempt + 1, text)
        return self.template(gap.key, gap.mode, guess)

    def vet(
        self,
        text: str | None,
        target: ItemKey | None,
        guess: Any = None,
    ) -> Question:
        """Guard a question proposed elsewhere (ask_strategy); template if it fails."""
        if text and self.passes_guard(text.strip()):
            return Question(text=text.strip(), target=target, mode="open", origin="model")
        return self.template(target, "open", guess)

    @staticmethod
    def template(
        target: ItemKey | None, mode: Literal["open", "confirm"], guess: Any = None
    ) -> Question:
        actual_mode: Literal["open", "confirm"] = (
            "confirm" if mode == "confirm" and guess not in (None, []) else "open"
        )
        return Question(
            text=template_question(target, actual_mode, guess),
            target=target,
            mode=actual_mode,
            origin="template",
        )
