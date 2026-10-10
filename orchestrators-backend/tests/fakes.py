"""A scripted stand-in for Gemini, so every test runs offline."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from strategy_interview.llm_io import GuardVerdict, QuestionDraft


@dataclass
class Call:
    role: str
    schema: type
    system: str
    prompt: str
    temperature: float


def _default_compose(prompt: str) -> QuestionDraft:
    target = re.search(r"Target item: (\S+)", prompt)
    mode = re.search(r"Mode: (\w+)", prompt)
    name = target.group(1) if target else "it"
    if mode and mode.group(1) == "confirm":
        return QuestionDraft(question=f"Just to check on {name}: does my guess fit?")
    return QuestionDraft(question=f"Could you tell me about the {name}?")


class FakeLLM:
    """Responses are queued per output schema. A queued entry may be a model
    instance, a dict, an exception, or a callable taking the prompt."""

    def __init__(self) -> None:
        self.queues: dict[type, list[Any]] = defaultdict(list)
        self.defaults: dict[type, Any] = {
            GuardVerdict: GuardVerdict(strategy_level=True, reason="ok"),
            QuestionDraft: _default_compose,
        }
        self.calls: list[Call] = []

    def queue(self, schema: type, *responses: Any) -> "FakeLLM":
        self.queues[schema].extend(responses)
        return self

    def default(self, schema: type, response: Any) -> "FakeLLM":
        self.defaults[schema] = response
        return self

    def calls_for(self, schema: type) -> list[Call]:
        return [c for c in self.calls if c.schema is schema]

    def generate(self, *, role, system, prompt, schema, temperature):
        self.calls.append(Call(role, schema, system, prompt, temperature))
        if self.queues[schema]:
            response = self.queues[schema].pop(0)
        elif schema in self.defaults:
            response = self.defaults[schema]
        else:
            raise AssertionError(f"unexpected {schema.__name__} call")
        if isinstance(response, BaseException):
            raise response
        if callable(response) and not isinstance(response, BaseModel):
            response = response(prompt)
        if isinstance(response, dict):
            response = schema.model_validate(response)
        return response


def item(
    key: str,
    *values: str,
    source: str = "stated",
    confidence: float = 0.9,
    evidence: str = "owner said so",
) -> dict[str, Any]:
    return {
        "item": key,
        "values": list(values),
        "source": source,
        "confidence": confidence,
        "evidence": evidence,
    }


def extraction(*items: dict[str, Any], wants_to_stop: bool = False) -> dict[str, Any]:
    return {"items": list(items), "wants_to_stop": wants_to_stop}


Responder = Callable[[str], Any]
