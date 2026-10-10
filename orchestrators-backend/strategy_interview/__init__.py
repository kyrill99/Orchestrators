"""Objective interview and strategy picture.

Public API::

    from strategy_interview import (
        GeminiLLM, InterviewEngine, StrategyAdvisor, StrategyQuery, render_brief,
    )
"""

from .advisor import Advice, PendingQuestion, StrategyAdvisor, StrategyQuery
from .brief import render_brief
from .defaults import POLICIES
from .interview import InterviewEngine, InterviewFinishedError, InterviewSession, Turn
from .llm import GeminiLLM, LLMError, ModelConfig, StructuredLLM
from .merge import InvalidValue
from .prompts import PROMPT_VERSION
from .questions import Question
from .schema import (
    Axis,
    ItemKey,
    Lifespan,
    OwnerProfile,
    Policies,
    ProjectType,
    Source,
    StrategyPicture,
    Tracked,
)
from .scoring import Readiness, ScoringConfig

__all__ = [
    "Advice",
    "Axis",
    "GeminiLLM",
    "InterviewEngine",
    "InterviewFinishedError",
    "InterviewSession",
    "InvalidValue",
    "ItemKey",
    "LLMError",
    "Lifespan",
    "ModelConfig",
    "OwnerProfile",
    "POLICIES",
    "PROMPT_VERSION",
    "PendingQuestion",
    "Policies",
    "ProjectType",
    "Question",
    "Readiness",
    "ScoringConfig",
    "Source",
    "StrategyAdvisor",
    "StrategyPicture",
    "StrategyQuery",
    "StructuredLLM",
    "Tracked",
    "Turn",
    "render_brief",
]

__version__ = "0.1.0"
