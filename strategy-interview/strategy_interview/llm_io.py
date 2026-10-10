"""Structured outputs Gemini must return. Kept flat so they map onto Gemini's
JSON-schema subset (no unions beyond nullable, no recursion)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .schema import ItemKey

# A flat enum (no nullable union), which maps cleanly onto Gemini's schema subset.
GapItem = Literal[
    "none",
    "goal",
    "project_type",
    "success_criteria",
    "lifespan",
    "audience",
    "constraints",
    "non_goals",
    "tradeoffs.speed_vs_quality",
    "tradeoffs.explore_vs_focus",
    "tradeoffs.simplicity_vs_extensibility",
    "tradeoffs.caution_vs_boldness",
]


def gap_key(value: str) -> ItemKey | None:
    return None if value == "none" else ItemKey(value)


class ExtractedItem(BaseModel):
    item: ItemKey = Field(description="Which picture item this answer speaks to.")
    values: list[str] = Field(
        description=(
            "The value. Text items: one entry. List items: one entry per point; an empty "
            "list means the owner explicitly said 'none'. project_type: one of hobby, "
            "prototype, research, product. lifespan: one of throwaway, months, years. "
            "Trade-off items: one integer from -2 to 2 as a string."
        )
    )
    source: Literal["stated", "confirmed", "inferred"] = Field(
        description=(
            "stated: the owner said it. confirmed: the owner agreed to a guess in the "
            "question. inferred: you read it between the lines."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0, description="How sure you are, 0 to 1.")
    evidence: str = Field(description="The owner's words this is based on, at most 25 words.")


class ExtractionResult(BaseModel):
    items: list[ExtractedItem] = Field(default_factory=list)
    wants_to_stop: bool = Field(
        default=False,
        description="True only if the owner asks to stop the questions and get started.",
    )


class QuestionDraft(BaseModel):
    question: str = Field(description="Exactly one question for the owner, under 240 characters.")


class GuardVerdict(BaseModel):
    strategy_level: bool = Field(
        description=(
            "True if this is a single question about intent (goals, priorities, audience, "
            "success, lifespan, limits) that a non-technical owner can answer in one sentence."
        )
    )
    reason: str = Field(description="One short sentence.")


class AdviceDraft(BaseModel):
    settled: bool = Field(description="True if the strategy picture and policies settle this.")
    guidance: str = Field(description="What the agent should do, one or two sentences.")
    chosen_option: str | None = Field(
        default=None, description="The chosen option, copied exactly, if options were given."
    )
    rationale: str = Field(description="Why, referring to the picture items used.")
    cited: list[ItemKey] = Field(default_factory=list)
    escalate: bool = Field(
        description="True only if the owner must weigh in under the escalation rules."
    )
    gap_item: GapItem = Field(
        default="none",
        description="The picture item the owner's answer would fill, or none.",
    )
    owner_question: str | None = Field(
        default=None,
        description="If escalating: one strategy question for the owner, never 'A or B?'.",
    )


class OwnerReply(BaseModel):
    """Used only by the persona evaluation, where Gemini plays the owner."""

    answer: str
