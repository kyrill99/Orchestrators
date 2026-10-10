"""Model access: a small protocol plus the Gemini implementation.

The engine only depends on ``StructuredLLM``; tests use a scripted fake.
``GeminiLLM`` uses the Gemini Interactions API with structured output.
"""

from __future__ import annotations

import copy
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("strategy_interview.llm")

M = TypeVar("M", bound=BaseModel)
Role = Literal["main", "guard"]


class LLMError(RuntimeError):
    """A model call failed after retries (or failed in a way retries cannot fix)."""


class StructuredLLM(Protocol):
    def generate(
        self,
        *,
        role: Role,
        system: str,
        prompt: str,
        schema: type[M],
        temperature: float,
    ) -> M: ...


@dataclass(frozen=True)
class ModelConfig:
    main_model: str = "gemini-3.8-flash"
    guard_model: str = "gemini-3.5-flash-lite"
    max_attempts: int = 3
    backoff_seconds: float = 1.0  # 1 s, 2 s, 4 s ...
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            main_model=os.environ.get("STRATEGY_MAIN_MODEL", cls.main_model),
            guard_model=os.environ.get("STRATEGY_GUARD_MODEL", cls.guard_model),
        )

    def model_for(self, role: Role) -> str:
        return self.guard_model if role == "guard" else self.main_model


# Fields of JSON Schema that Gemini's structured output does not need or accept.
_DROP_KEYS = {"title", "default", "examples", "$schema"}


def gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema reduced to Gemini's subset: refs inlined, nullable
    unions turned into type arrays, titles and defaults dropped."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged)
        if "anyOf" in node:
            options = [resolve(o) for o in node["anyOf"]]
            non_null = [o for o in options if o.get("type") != "null"]
            if len(non_null) == 1 and len(options) == 2:
                inner = dict(non_null[0])
                t = inner.get("type")
                if isinstance(t, str):
                    inner["type"] = [t, "null"]
                rest = {k: v for k, v in node.items() if k not in ("anyOf",)}
                return resolve({**inner, **rest})
            node = {**node, "anyOf": options}
        return {k: resolve(v) for k, v in node.items() if k not in _DROP_KEYS}

    return resolve(raw)


_NON_RETRYABLE_CODES = {400, 401, 403, 404}


def _status_code(exc: BaseException) -> int | None:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


class GeminiLLM:
    """``StructuredLLM`` backed by the Gemini API (``google-genai`` SDK).

    Reads ``GEMINI_API_KEY`` from the environment unless a client is passed in.
    The CLI and the eval load it from ``orchestrators-backend/.env``, like ``main.py``.
    """

    def __init__(
        self,
        config: ModelConfig | None = None,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or ModelConfig.from_env()
        self._client = client
        self._sleep = sleep

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - depends on install
                raise LLMError(
                    "google-genai is not installed; run `pip install google-genai`"
                ) from exc
            timeout_ms = int(self.config.timeout_seconds * 1000)
            try:
                self._client = genai.Client(http_options={"timeout": timeout_ms})
            except TypeError:  # older SDKs without http_options
                self._client = genai.Client()
            except ValueError as exc:  # no API key; retrying cannot fix it
                raise LLMError(
                    f"cannot create the Gemini client; is GEMINI_API_KEY set in orchestrators-backend/.env? ({exc})"
                ) from exc
        return self._client

    def generate(
        self,
        *,
        role: Role,
        system: str,
        prompt: str,
        schema: type[M],
        temperature: float,
    ) -> M:
        model = self.config.model_for(role)
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": gemini_schema(schema),
        }
        last_error: BaseException | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                interaction = self.client.interactions.create(
                    model=model,
                    input=prompt,
                    system_instruction=system,
                    generation_config={"temperature": temperature},
                    response_format=response_format,
                    store=False,
                )
                text = getattr(interaction, "output_text", None)
                if not text:
                    raise ValueError("empty model output")
                return schema.model_validate_json(text)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                log.warning(
                    "gemini %s returned unusable output (attempt %d/%d): %s",
                    schema.__name__, attempt, self.config.max_attempts, exc,
                )
            except LLMError:
                raise
            except Exception as exc:  # SDK / transport errors
                last_error = exc
                code = _status_code(exc)
                if code in _NON_RETRYABLE_CODES:
                    raise LLMError(f"Gemini rejected the request ({code}): {exc}") from exc
                log.warning(
                    "gemini call for %s failed (attempt %d/%d, code=%s): %s",
                    schema.__name__, attempt, self.config.max_attempts, code, exc,
                )
            if attempt < self.config.max_attempts:
                self._sleep(self.config.backoff_seconds * (2 ** (attempt - 1)))
        raise LLMError(f"Gemini call for {schema.__name__} failed: {last_error}") from last_error
