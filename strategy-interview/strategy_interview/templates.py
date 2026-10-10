"""Fixed questions. Strategy-level by construction; the guard's fallback."""

from __future__ import annotations

from typing import Any, Literal

from .schema import AXIS_POLES, ItemKey, Lifespan, ProjectType

OPENING = (
    "Hi {name}, let's get started defining the objective of our project. "
    "Can you tell me what your end goal is in one sentence?"
)

_TYPE_WORDS = {
    ProjectType.HOBBY: "a hobby project",
    ProjectType.PROTOTYPE: "a quick prototype or demo",
    ProjectType.RESEARCH: "a research project",
    ProjectType.PRODUCT: "a product people will rely on",
}

_LIFESPAN_WORDS = {
    Lifespan.THROWAWAY: "a throwaway that only needs to work for now",
    Lifespan.MONTHS: "something that lives for a few months",
    Lifespan.YEARS: "something maintained for years",
}

_OPEN: dict[ItemKey, str] = {
    ItemKey.GOAL: "What do you want to have achieved when this project is done, in one sentence?",
    ItemKey.PROJECT_TYPE: (
        "Is this a hobby project, a quick prototype, a research project, "
        "or a product people will rely on?"
    ),
    ItemKey.SUCCESS_CRITERIA: "How will you know it worked? What does a good result look like?",
    ItemKey.LIFESPAN: (
        "How long does this need to live: a throwaway, a few months, or years of maintenance?"
    ),
    ItemKey.AUDIENCE: "Who is the result for?",
    ItemKey.CONSTRAINTS: (
        "Are there hard limits I should respect, like a deadline, a budget, "
        "or tools you must use?"
    ),
    ItemKey.NON_GOALS: "Is there anything this project should explicitly not try to do?",
}


def lean_phrase(key: ItemKey, lean: int) -> str:
    """Human words for a trade-off lean, e.g. 'leaning towards getting it right'."""
    assert key.axis is not None
    left, right = AXIS_POLES[key.axis]
    if lean <= -2:
        return f"strongly favouring {left} over {right}"
    if lean == -1:
        return f"leaning towards {left} over {right}"
    if lean == 0:
        return f"balancing {left} and {right}"
    if lean == 1:
        return f"leaning towards {right} over {left}"
    return f"strongly favouring {right} over {left}"


def describe_value(key: ItemKey, value: Any) -> str:
    """Human words for a value, used in confirm questions and the agent brief."""
    if value is None:
        return "unknown"
    if key.axis is not None:
        return lean_phrase(key, int(value))
    if key is ItemKey.PROJECT_TYPE:
        return _TYPE_WORDS[ProjectType(value)]
    if key is ItemKey.LIFESPAN:
        return _LIFESPAN_WORDS[Lifespan(value)]
    if isinstance(value, list):
        return "none" if not value else "; ".join(value)
    return str(value)


def template_question(
    key: ItemKey | None, mode: Literal["open", "confirm"], guess: Any = None
) -> str:
    """A safe question for any gap. ``key=None`` gives a generic strategy question."""
    if key is None:
        return "What matters most for this project right now, so I can decide in your spirit?"
    if mode == "confirm" and guess is not None and guess != []:
        words = describe_value(key, guess)
        if key is ItemKey.PROJECT_TYPE:
            return f"Sounds like {words}. Is that right?"
        if key is ItemKey.LIFESPAN:
            return f"I'm assuming this is {words}. Does that fit?"
        if key.axis is not None:
            return f"I'd go with {words} here. Does that fit how you see it?"
        if key is ItemKey.SUCCESS_CRITERIA:
            return f"Would you call it a success if: {words}?"
        if key is ItemKey.CONSTRAINTS:
            return f"Should I treat these as hard limits: {words}?"
        if key is ItemKey.AUDIENCE:
            return f"This is mainly for {words}, right?"
        if key is ItemKey.GOAL:
            return f"So the goal is: {words}. Did I get that right?"
        if key is ItemKey.NON_GOALS:
            return f"Should I stay away from: {words}?"
    if key.axis is not None:
        left, right = AXIS_POLES[key.axis]
        return f"When the two pull apart, what matters more here: {left} or {right}?"
    return _OPEN[key]
