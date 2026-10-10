"""Turn a finished conversation into memory changes with Gemini."""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import TypeAdapter

from .models import ChatMessage, Memory, ReflectOp

OPS_ADAPTER: TypeAdapter[list[ReflectOp]] = TypeAdapter(list[ReflectOp])

SYSTEM = """You maintain the long-term memory of Orchestrators, a tool where a project
owner sets strategy and agents do the work. You read one finished conversation between
the owner (user) and an agent, plus the memories that already exist, and decide what
should change in memory so the next session needs fewer questions.

Keep only what will still matter in a later session:
- goals, priorities, success criteria, non-goals, constraints
- owner preferences about how agents should work or communicate
- decisions with their reasoning
- corrections: the owner disagreed with, or was annoyed by, something an agent did
Leave out small talk, implementation trivia an agent can decide alone, and anything
already stored unchanged.

Scopes:
- "project": true for this project only (its goal, success criteria, findings, decisions).
- "user": a preference of this owner that would also hold in their other projects.
- "global": a lesson about how the orchestrator itself should behave for any owner,
  e.g. a kind of question that should not have been asked. Use sparingly and phrase it
  without naming the owner or the project.

Kinds: "strategy", "preference", "decision", "learning", "note".

Answer with a JSON array of operations (an empty array if nothing is worth keeping):
- {"op": "create", "scope": ..., "kind": ..., "title": ..., "body": ..., "tags": [...], "reason": ...}
- {"op": "update", "targetId": ..., "title": ..., "body": ..., "reason": ...}
    an existing memory is still right but incomplete; body is the full new text
- {"op": "supersede", "targetId": ..., "title": ..., "body": ..., "reason": ...}
    the conversation contradicts an existing memory; the new one replaces it
- {"op": "reinforce", "targetId": ..., "reason": ...}
    the conversation confirmed an existing memory, or the agent used it and it was right

Titles are short noun phrases. Bodies are one to four plain sentences that an agent can
act on without having seen the conversation. "reason" says which part of the
conversation the operation comes from."""


def build_prompt(messages: Sequence[ChatMessage], existing: Sequence[Memory]) -> str:
    known = [
        {"id": m.id, "scope": m.scope, "kind": m.kind, "title": m.title, "body": m.body}
        for m in existing
    ]
    transcript = "\n".join(f"{m.role}: {m.content}" for m in messages)
    return (
        "<existing_memories>\n"
        + json.dumps(known, ensure_ascii=False, indent=1)
        + "\n</existing_memories>\n\n<conversation>\n"
        + transcript
        + "\n</conversation>"
    )


class GeminiDistiller:
    def __init__(self, model: str, client=None) -> None:
        self.model = model
        self._client = client

    def distill(
        self, messages: Sequence[ChatMessage], existing: Sequence[Memory]
    ) -> list[ReflectOp]:
        from google import genai
        from google.genai import types

        if self._client is None:
            self._client = genai.Client()
        response = self._client.models.generate_content(
            model=self.model,
            contents=build_prompt(messages, existing),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
            ),
        )
        return OPS_ADAPTER.validate_json(response.text)
