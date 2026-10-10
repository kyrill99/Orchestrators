"""Shared tool contract: teammates register tools, the workflow generator calls them.

Register a tool in your own module::

    from pydantic import BaseModel
    from orchestration import tool

    class SavePicture(BaseModel):
        project_id: str
        picture: dict

    @tool("save_picture", "Store the strategy picture of a project.", SavePicture)
    def save_picture(args: SavePicture) -> dict:
        ...
        return {"ok": True}

then add the module to ``TOOL_MODULES``. The caller hands ``toolbox.declarations()``
to Gemini (Interactions API ``tools=``) and answers every ``function_call`` step
with ``toolbox.call(step)``, sent back as ``input`` with ``previous_interaction_id``.
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypeVar

from pydantic import BaseModel

from strategy_interview.llm import gemini_schema

log = logging.getLogger("orchestration.tools")

A = TypeVar("A", bound=BaseModel)
Handler = Callable[[A], Any]

# Modules that register tools when imported, e.g. "memory_store.tools".
TOOL_MODULES: list[str] = []


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[Any], Any]

    def declaration(self) -> dict[str, Any]:
        """Function tool for ``client.interactions.create(tools=[...])``."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": gemini_schema(self.args),
        }


def _encode(result: Any) -> str:
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    return json.dumps(
        result,
        default=lambda v: v.model_dump(mode="json") if isinstance(v, BaseModel) else str(v),
    )


class Toolbox:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, args: type[A]) -> Callable[[Handler[A]], Handler[A]]:
        """Decorator form of ``register``; the function gets the validated args model."""

        def decorator(handler: Handler[A]) -> Handler[A]:
            self.register(Tool(name, description, args, handler))
            return handler

        return decorator

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def declarations(self) -> list[dict[str, Any]]:
        return [t.declaration() for t in self._tools.values()]

    def call(self, step: Any) -> dict[str, Any]:
        """Run one ``function_call`` step (SDK object or dict) and return its
        ``function_result`` step. Failures go back to the model as ``is_error``."""
        get = step.get if isinstance(step, Mapping) else lambda key: getattr(step, key, None)
        call_id, name = get("id"), get("name")
        tool = self._tools.get(name)
        try:
            if tool is None:
                raise LookupError(f"unknown tool {name!r}; available: {', '.join(self.names)}")
            result = _encode(tool.handler(tool.args.model_validate(get("arguments") or {})))
            is_error = False
        except Exception as exc:  # report to the model instead of crashing the workflow
            log.warning("tool %s failed: %s", name, exc)
            result, is_error = f"{type(exc).__name__}: {exc}", True
        return {
            "type": "function_result",
            "call_id": call_id,
            "name": name,
            "result": result,
            "is_error": is_error,
        }


toolbox = Toolbox()
tool = toolbox.tool


def load_tools(modules: list[str] | None = None) -> Toolbox:
    """Import the tool modules so their tools register, then return the shared toolbox."""
    for module in TOOL_MODULES if modules is None else modules:
        importlib.import_module(module)
    return toolbox
