"""Strategy picture schema: the one record every agent reads about intent.

Every item is a ``Tracked`` value that records where it came from (``source``)
and how sure we are (``confidence``). Those two fields drive both the interview
(what to ask next) and the agents (how much to rely on a value).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectType(str, Enum):
    HOBBY = "hobby"
    PROTOTYPE = "prototype"
    RESEARCH = "research"
    PRODUCT = "product"


class Lifespan(str, Enum):
    THROWAWAY = "throwaway"
    MONTHS = "months"
    YEARS = "years"


class Source(str, Enum):
    STATED = "stated"  # owner said it in their own words
    CONFIRMED = "confirmed"  # owner confirmed a guess we offered
    INFERRED = "inferred"  # Gemini inferred it from the conversation
    MEMORY = "memory"  # pre-filled from the owner's past projects
    DEFAULT = "default"  # derived from the project type


SOURCE_RANK: dict[Source, int] = {
    Source.STATED: 4,
    Source.CONFIRMED: 4,
    Source.INFERRED: 3,
    Source.MEMORY: 2,
    Source.DEFAULT: 1,
}

OWNER_SOURCES = frozenset({Source.STATED, Source.CONFIRMED})


class Axis(str, Enum):
    SPEED_VS_QUALITY = "speed_vs_quality"
    EXPLORE_VS_FOCUS = "explore_vs_focus"
    SIMPLICITY_VS_EXTENSIBILITY = "simplicity_vs_extensibility"
    CAUTION_VS_BOLDNESS = "caution_vs_boldness"


# (left pole, right pole). A lean of -2 strongly favours the left pole, +2 the right one.
AXIS_POLES: dict[Axis, tuple[str, str]] = {
    Axis.SPEED_VS_QUALITY: ("shipping fast", "getting it right"),
    Axis.EXPLORE_VS_FOCUS: ("trying several approaches", "committing to one"),
    Axis.SIMPLICITY_VS_EXTENSIBILITY: ("keeping it minimal", "building it to grow"),
    Axis.CAUTION_VS_BOLDNESS: ("avoiding risk", "taking bets"),
}

LEAN_MIN, LEAN_MAX = -2, 2


class ItemKey(str, Enum):
    """Every tracked item of the picture, addressable by one key."""

    GOAL = "goal"
    PROJECT_TYPE = "project_type"
    SUCCESS_CRITERIA = "success_criteria"
    LIFESPAN = "lifespan"
    AUDIENCE = "audience"
    CONSTRAINTS = "constraints"
    NON_GOALS = "non_goals"
    SPEED_VS_QUALITY = "tradeoffs.speed_vs_quality"
    EXPLORE_VS_FOCUS = "tradeoffs.explore_vs_focus"
    SIMPLICITY_VS_EXTENSIBILITY = "tradeoffs.simplicity_vs_extensibility"
    CAUTION_VS_BOLDNESS = "tradeoffs.caution_vs_boldness"

    @property
    def axis(self) -> Axis | None:
        if self.value.startswith("tradeoffs."):
            return Axis(self.value.split(".", 1)[1])
        return None

    @property
    def label(self) -> str:
        if self.axis is not None:
            left, right = AXIS_POLES[self.axis]
            return f"{left} vs {right}"
        return "non-goals" if self is ItemKey.NON_GOALS else self.value.replace("_", " ")


AXIS_ITEMS: dict[Axis, ItemKey] = {k.axis: k for k in ItemKey if k.axis is not None}
LIST_ITEMS = frozenset({ItemKey.SUCCESS_CRITERIA, ItemKey.CONSTRAINTS, ItemKey.NON_GOALS})
TEXT_ITEMS = frozenset({ItemKey.GOAL, ItemKey.AUDIENCE})
TYPE_DERIVED_ITEMS = frozenset({ItemKey.LIFESPAN, *AXIS_ITEMS.values()})

MAX_LIST_ENTRIES = 8
MAX_ENTRY_CHARS = 200
MAX_GOAL_CHARS = 300
MAX_EVIDENCE_CHARS = 200

T = TypeVar("T")


class Tracked(BaseModel, Generic[T]):
    """A value plus its provenance."""

    value: T | None = None
    source: Source | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str | None = None
    turn: int | None = None

    @property
    def is_set(self) -> bool:
        # An explicitly stated empty list ("no hard constraints") is set.
        return self.source is not None and self.value is not None


class Tradeoffs(BaseModel):
    speed_vs_quality: Tracked[int] = Field(default_factory=Tracked[int])
    explore_vs_focus: Tracked[int] = Field(default_factory=Tracked[int])
    simplicity_vs_extensibility: Tracked[int] = Field(default_factory=Tracked[int])
    caution_vs_boldness: Tracked[int] = Field(default_factory=Tracked[int])


class Policies(BaseModel):
    """Working rules derived from the project type. Computed, never asked."""

    testing: str
    documentation: str
    approaches_before_committing: int
    dependencies: str
    ask_owner_when: str


class StrategyPicture(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate computed fields on reload

    schema_version: Literal["1"] = "1"
    project_id: str
    owner_id: str
    revision: int = 0
    goal: Tracked[str] = Field(default_factory=Tracked[str])
    project_type: Tracked[ProjectType] = Field(default_factory=Tracked[ProjectType])
    success_criteria: Tracked[list[str]] = Field(default_factory=Tracked[list[str]])
    lifespan: Tracked[Lifespan] = Field(default_factory=Tracked[Lifespan])
    audience: Tracked[str] = Field(default_factory=Tracked[str])
    constraints: Tracked[list[str]] = Field(default_factory=Tracked[list[str]])
    non_goals: Tracked[list[str]] = Field(default_factory=Tracked[list[str]])
    tradeoffs: Tradeoffs = Field(default_factory=Tradeoffs)
    open_gaps: list[ItemKey] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)

    def get(self, key: ItemKey) -> Tracked[Any]:
        if key.axis is not None:
            return getattr(self.tradeoffs, key.axis.value)
        return getattr(self, key.value)

    def put(self, key: ItemKey, tracked: Tracked[Any]) -> None:
        if key.axis is not None:
            setattr(self.tradeoffs, key.axis.value, tracked)
        else:
            setattr(self, key.value, tracked)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def policies(self) -> Policies | None:
        from .defaults import POLICIES

        if self.project_type.value is None:
            return None
        return POLICIES[self.project_type.value]


class OwnerProfile(BaseModel):
    """What memory hands this module at the start of an interview (optional)."""

    owner_id: str
    standing_constraints: list[str] = Field(default_factory=list)
    learned_leans: dict[ProjectType, dict[Axis, int]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
