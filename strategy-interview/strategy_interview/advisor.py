"""ask_strategy: agents send an uncertainty; they get an answer from the strategy.

The owner is reached at most once per query, only with a strategy question, and
their answer improves the picture for every later agent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Iterable, Literal

from pydantic import BaseModel, Field

from .brief import render_brief
from .llm import LLMError, StructuredLLM
from .llm_io import AdviceDraft, gap_key
from .prompts import ADVISE_SYSTEM, build_advise_prompt
from .questions import Question, QuestionWriter
from .schema import ItemKey, OwnerProfile, StrategyPicture, utcnow
from .updating import extract_and_merge

log = logging.getLogger("strategy_interview.advisor")

ADVISE_TEMPERATURE = 0.2
MAX_FIELD_CHARS = 2000


class StrategyQuery(BaseModel):
    agent_id: str = Field(max_length=200)
    task: str = Field(max_length=MAX_FIELD_CHARS)
    uncertainty: str = Field(max_length=MAX_FIELD_CHARS)
    options: list[str] = Field(default_factory=list, max_length=10)


class PendingQuestion(BaseModel):
    """An owner question waiting for an answer. The caller stores it."""

    pending_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    query: StrategyQuery
    question: Question
    created_at: datetime = Field(default_factory=utcnow)


class Advice(BaseModel):
    status: Literal["answered", "needs_owner", "unavailable"]
    guidance: str | None = None
    chosen_option: str | None = None
    rationale: str | None = None
    cited: list[ItemKey] = Field(default_factory=list)
    confidence: Literal["high", "low"] = "high"
    owner_question: Question | None = None
    pending: PendingQuestion | None = None


def _match_option(chosen: str | None, options: list[str]) -> str | None:
    if not chosen or not options:
        return None
    wanted = chosen.strip().casefold()
    for option in options:
        if option.strip().casefold() == wanted:
            return option
    return None


class StrategyAdvisor:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm
        self.writer = QuestionWriter(llm)

    def ask(
        self,
        picture: StrategyPicture,
        query: StrategyQuery,
        profile: OwnerProfile | None = None,
        open_pending: Iterable[PendingQuestion] = (),
    ) -> Advice:
        draft = self._advise(picture, query, profile, allow_escalation=True)
        if draft is None:
            return Advice(status="unavailable")

        if draft.settled and not draft.escalate:
            return self._answered(draft, query, "high")

        target = gap_key(draft.gap_item)
        for pending in open_pending:
            if pending.project_id == picture.project_id and (
                pending.question.target == target or target is None
            ):
                log.info("reusing open owner question %s", pending.pending_id)
                return self._needs_owner(draft, query, pending)

        guess = picture.get(target).value if target is not None else None
        question = self.writer.vet(draft.owner_question, target, guess)
        pending = PendingQuestion(project_id=picture.project_id, query=query, question=question)
        return self._needs_owner(draft, query, pending)

    def resolve(
        self,
        picture: StrategyPicture,
        pending: PendingQuestion,
        owner_answer: str,
        profile: OwnerProfile | None = None,
    ) -> tuple[StrategyPicture, Advice]:
        """Merge the owner's answer, then answer the original query. Never escalates."""
        picture = picture.model_copy(deep=True)
        extract_and_merge(
            self.llm,
            picture,
            pending.question,
            owner_answer,
            transcript_tail=(),
            turn=None,
            profile=profile,
        )
        draft = self._advise(picture, pending.query, profile, allow_escalation=False)
        if draft is None:
            return picture, Advice(status="unavailable")
        return picture, self._answered(draft, pending.query, "high" if draft.settled else "low")

    # ------------------------------------------------------------- internals

    def _advise(
        self,
        picture: StrategyPicture,
        query: StrategyQuery,
        profile: OwnerProfile | None,
        allow_escalation: bool,
    ) -> AdviceDraft | None:
        try:
            return self.llm.generate(
                role="main",
                system=ADVISE_SYSTEM,
                prompt=build_advise_prompt(
                    render_brief(picture),
                    query,
                    profile.notes if profile else [],
                    allow_escalation,
                ),
                schema=AdviceDraft,
                temperature=ADVISE_TEMPERATURE,
            )
        except LLMError as exc:
            log.warning("advise unavailable: %s", exc)
            return None

    @staticmethod
    def _answered(draft: AdviceDraft, query: StrategyQuery, confidence: str) -> Advice:
        return Advice(
            status="answered",
            guidance=draft.guidance,
            chosen_option=_match_option(draft.chosen_option, query.options),
            rationale=draft.rationale,
            cited=draft.cited,
            confidence=confidence,  # type: ignore[arg-type]
        )

    @staticmethod
    def _needs_owner(
        draft: AdviceDraft, query: StrategyQuery, pending: PendingQuestion
    ) -> Advice:
        # Provisional guidance travels along so the agent can keep working on
        # something reversible while the owner answers.
        return Advice(
            status="needs_owner",
            guidance=draft.guidance,
            chosen_option=_match_option(draft.chosen_option, query.options),
            rationale=draft.rationale,
            cited=draft.cited,
            confidence="low",
            owner_question=pending.question,
            pending=pending,
        )
