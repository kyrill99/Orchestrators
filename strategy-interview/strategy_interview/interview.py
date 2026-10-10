"""The objective interview: at most 6 questions, usually 3 to 5.

Gemini reads answers and phrases questions; plain code decides what to ask and
when to stop. The engine is stateless: every call takes a session and returns a
new one, so callers can persist it however they like.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .defaults import apply_profile, apply_type_defaults
from .llm import StructuredLLM
from .merge import Proposal, apply
from .prompts import PROMPT_VERSION
from .questions import Question, QuestionWriter, TranscriptEntry
from .schema import ItemKey, OwnerProfile, Source, StrategyPicture, utcnow
from .scoring import Readiness, ScoringConfig, pick_gap, readiness
from .templates import OPENING
from .updating import extract_and_merge

log = logging.getLogger("strategy_interview.interview")

StopReason = Literal["ready", "budget", "owner_stopped", "no_gaps", "forced"]


class InterviewFinishedError(RuntimeError):
    """The session is already done; use ask_strategy for later questions."""


class InterviewSession(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    prompt_version: str = PROMPT_VERSION
    owner_name: str
    picture: StrategyPicture
    profile: OwnerProfile | None = None
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    questions_asked: int = 0
    ask_counts: dict[ItemKey, int] = Field(default_factory=dict)
    pending_question: Question | None = None
    done: bool = False
    stop_reason: StopReason | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def turn(self) -> int:
        """Number of owner answers received so far."""
        return sum(1 for e in self.transcript if e.role == "owner")


class Turn(BaseModel):
    """What the UI gets after every call."""

    session: InterviewSession
    question: Question | None
    done: bool
    readiness: Readiness
    changed: list[ItemKey] = Field(default_factory=list)


class InterviewEngine:
    def __init__(
        self,
        llm: StructuredLLM,
        scoring: ScoringConfig | None = None,
        transcript_window: int = 6,
    ) -> None:
        self.llm = llm
        self.scoring = scoring or ScoringConfig()
        self.transcript_window = transcript_window
        self.writer = QuestionWriter(llm)

    # ------------------------------------------------------------------ API

    def start(
        self,
        project_id: str,
        owner_id: str,
        owner_name: str,
        profile: OwnerProfile | None = None,
    ) -> Turn:
        picture = StrategyPicture(project_id=project_id, owner_id=owner_id)
        changed = apply_profile(picture, profile, turn=0)
        session = InterviewSession(owner_name=owner_name, picture=picture, profile=profile)
        question = Question(
            text=OPENING.format(name=owner_name.strip() or "there"),
            target=ItemKey.GOAL,
            mode="opening",
            origin="fixed",
        )
        self._ask(session, question)
        return self._turn(session, changed)

    def answer(self, session: InterviewSession, text: str) -> Turn:
        if session.done:
            raise InterviewFinishedError("interview already finished")
        session = session.model_copy(deep=True)
        if not text or not text.strip():
            return self._turn(session, [])  # nothing to read; same question stands

        question = session.pending_question
        tail = session.transcript[-self.transcript_window :]
        session.transcript.append(
            TranscriptEntry(
                role="owner",
                text=text.strip(),
                turn=session.turn + 1,
                target=question.target if question else None,
            )
        )
        outcome = extract_and_merge(
            self.llm,
            session.picture,
            question,
            text,
            tail,
            turn=session.turn,
            profile=session.profile,
        )
        session.pending_question = None
        log.info(
            "turn %d: %d items changed%s",
            session.turn,
            len(outcome.changed),
            "" if outcome.model_ok else " (extraction unavailable)",
        )

        if outcome.wants_to_stop:
            return self._finish(session, "owner_stopped", outcome.changed)
        return self._advance(session, outcome.changed)

    def correct(self, session: InterviewSession, key: ItemKey, value: Any) -> Turn:
        """Direct correction from the UI: the value is stored as stated, confidence 1.0,
        without asking Gemini to read it. Gemini is only called if the pending question
        became moot and a new one has to be phrased. Raises ``InvalidValue``."""
        session = session.model_copy(deep=True)
        type_before = session.picture.project_type.value
        apply(
            session.picture,
            Proposal(key, value, Source.STATED, 1.0, "corrected by the owner"),
            turn=session.turn,
        )
        changed = [key]
        if session.picture.project_type.value != type_before:
            changed += apply_type_defaults(session.picture, session.profile, session.turn)
        if session.done:
            session.picture.open_gaps = readiness(session.picture, self.scoring).unknown
            return self._turn(session, changed)
        if readiness(session.picture, self.scoring).ready:
            return self._finish(session, "ready", changed)
        pending = session.pending_question
        if pending is not None and pending.target == key:
            return self._advance(session, changed, replace_pending=True)
        return self._turn(session, changed)

    def finish(self, session: InterviewSession) -> Turn:
        session = session.model_copy(deep=True)
        if session.done:
            return self._turn(session, [])
        return self._finish(session, "forced", [])

    # ------------------------------------------------------------- internals

    def _advance(
        self, session: InterviewSession, changed: list[ItemKey], replace_pending: bool = False
    ) -> Turn:
        state = readiness(session.picture, self.scoring)
        if state.ready:
            return self._finish(session, "ready", changed)
        if session.questions_asked >= self.scoring.max_questions and not replace_pending:
            return self._finish(session, "budget", changed)
        gap = pick_gap(session.picture, session.ask_counts, self.scoring)
        if gap is None:
            return self._finish(session, "no_gaps", changed)
        question = self.writer.compose(
            session.picture,
            gap,
            session.owner_name,
            session.transcript[-self.transcript_window :],
        )
        self._ask(session, question, count=not replace_pending)
        return self._turn(session, changed)

    def _ask(self, session: InterviewSession, question: Question, count: bool = True) -> None:
        session.pending_question = question
        if count:
            session.questions_asked += 1
            if question.target is not None:
                session.ask_counts[question.target] = (
                    session.ask_counts.get(question.target, 0) + 1
                )
        session.transcript.append(
            TranscriptEntry(
                role="interviewer",
                text=question.text,
                turn=session.turn,
                target=question.target,
            )
        )
        session.updated_at = utcnow()

    def _finish(
        self, session: InterviewSession, reason: StopReason, changed: list[ItemKey]
    ) -> Turn:
        session.done = True
        session.stop_reason = reason
        session.pending_question = None
        session.picture.open_gaps = readiness(session.picture, self.scoring).unknown
        session.updated_at = utcnow()
        log.info(
            "interview %s finished (%s) after %d questions",
            session.session_id,
            reason,
            session.questions_asked,
        )
        return self._turn(session, changed)

    def _turn(self, session: InterviewSession, changed: list[ItemKey]) -> Turn:
        return Turn(
            session=session,
            question=session.pending_question,
            done=session.done,
            readiness=readiness(session.picture, self.scoring),
            changed=changed,
        )
