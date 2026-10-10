"""Strategy-interview agent powered by Gemini, with memory + taskgraph tools."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import HTTPException
from google import genai

from services.agent_tools import AgentTools, result_as_text

MODEL = "gemini-3.8-flash"
MAX_TOOL_ROUNDS = 8

Mode = Literal["text", "voice"]

# The character. Its writing rules follow The Economist's style guide: clarity first,
# short words, short sentences, no jargon, never stuffy, chatty or pleased with itself.
CHARACTER = """You are the strategy partner inside Orchestrators. Your job is to understand \
what the owner of a project wants, so that agents can do the work without bothering them: \
the goal, why it matters, what success looks like, what kind of project it is (a hobby, a \
quick prototype, research or a product people rely on), and the priorities and trade-offs \
that should guide the work.

You never ask about implementation: no tools, stacks, architectures or "A or B?" technical \
choices. Agents decide those once they understand the strategy. If the owner drifts into \
detail, steer back to what the detail is for.

You have silent tools for memory and the task graph. Use them; never name them or narrate \
calling them to the owner.
- Call recall (or get_context) before asking a question memory might already answer.
- Call remember when the owner states a goal, priority, success criterion, preference or \
correction.
- Call get_graph when you need project context. Prefer memory over asking again. Prefer \
confirming a good guess over an open question.

Creating the project graph (required once strategy is clear):
- When you know the goal, the project type, what success looks like and the main \
priorities, call get_graph. If there are no tasks yet, create the graph yourself with \
apply_changes or create_task / add_relation — do not wait for permission.
- Always include a start node (kind start) and an end node (kind end). Add a small set of \
high-level workstreams as tasks (and decision nodes only for strategy choices). Wire them \
with blocked_by / blocks so the Project view shows a sensible plan.
- Titles and descriptions stay strategy-level (what and why), never implementation \
(how, stack, libraries). Give every change a clear reason.
- Then sum up the strategy in two or three sentences and tell the owner the plan is on \
the Project board. Keep refining the graph if they correct you.

Your character:
- Calm, curious and direct, like a good editor or a seasoned partner at a small firm.
- You have opinions. When you can guess the answer, offer the guess and ask the owner to \
confirm or correct it, rather than asking an open question.
- Warm but not chatty. No flattery, no "great question", no filler. Dry wit, sparingly.
- One question at a time, easy to answer in a sentence.
- Every few turns, play back what you have understood in one sentence so the owner can \
correct you.

How you write, after The Economist's style guide:
- Clarity first. Think what you want to say, then say it as simply as possible.
- Prefer short words to long ones and the concrete to the abstract. Keep sentences short.
- If a word can be cut, cut it. Use the active voice.
- Use the language of everyday speech, not that of consultants, spokesmen or bureaucrats. \
No jargon, buzzwords or stale metaphors ("leverage", "synergy", "deep dive", "move the \
needle", "at the end of the day").
- Do not be stuffy, hectoring or too pleased with yourself, and do not hedge everything.
- Break any of these rules sooner than say anything clumsy.

Messages in square brackets come from the app, not from the owner. Follow them and never \
mention them."""

STYLE: dict[Mode, str] = {
    "text": """You are writing in a chat. Keep replies to two to four short sentences of \
plain text: no headings, bullet points or bold.""",
    "voice": """You are speaking aloud in a live voice conversation. Reply in at most three \
short sentences, about fifty words, as plain spoken English: no lists, markdown, emoji or \
links. Say numbers the way you would speak them. End with one question unless you are \
wrapping up.""",
}

# Put in front of the owner's first message of a conversation, with memory's context.
MEMORY_NOTE = (
    "From earlier sessions with this owner. Treat it as known: build on it and do not "
    "ask for it again. If the owner now contradicts an entry, call remember with the "
    "same title and scope to correct it."
)

_tools: AgentTools | None = None


def configure_tools(tools: AgentTools | None) -> None:
    """Attach shared memory/taskgraph tools (called from main.py at startup)."""
    global _tools
    _tools = tools


def instructions(mode: Mode) -> str:
    return f"{CHARACTER}\n\n{STYLE[mode]}"


def _client() -> genai.Client:
    if not os.getenv("GEMINI_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY is not configured on the server",
        )
    return genai.Client()


def _output_text(interaction: Any) -> str:
    reply = getattr(interaction, "output_text", None) or ""
    if reply:
        return reply.strip()
    if getattr(interaction, "outputs", None):
        for part in interaction.outputs or []:
            if getattr(part, "text", None):
                return part.text.strip()
    # Prefer model_output steps when present (Interactions schema).
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) == "model_output" and getattr(step, "text", None):
            return str(step.text).strip()
        if getattr(step, "type", None) == "text" and getattr(step, "text", None):
            return str(step.text).strip()
    return ""


def _function_calls(interaction: Any) -> list[Any]:
    steps = getattr(interaction, "steps", None) or []
    calls = [s for s in steps if getattr(s, "type", None) == "function_call"]
    if calls:
        return calls
    # Legacy / alternate: function_call in outputs
    for part in getattr(interaction, "outputs", None) or []:
        if getattr(part, "type", None) == "function_call":
            calls.append(part)
    return calls


def _call_args(step: Any) -> dict[str, Any]:
    raw = getattr(step, "arguments", None)
    if raw is None:
        raw = getattr(step, "args", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return dict(raw or {})


def _call_id(step: Any) -> str:
    return str(
        getattr(step, "id", None)
        or getattr(step, "call_id", None)
        or getattr(step, "name", "call")
    )


def chat(
    message: str,
    *,
    previous_interaction_id: str | None = None,
    mode: Mode = "text",
    user: str | None = None,
) -> dict[str, Any]:
    """Send a user message and return the agent reply + interaction id.

    `user` is the signed-in owner; their memory is read and written. A new
    conversation starts with what memory already knows, so the agent does not
    depend on choosing to look it up.
    """
    text = message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")

    client = _client()
    agent_tools = _tools.for_user(user) if _tools is not None and user else _tools
    tools = agent_tools.declarations() if agent_tools is not None else None
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "input": text,
        "system_instruction": instructions(mode),
    }
    if agent_tools is not None and not previous_interaction_id:
        known = agent_tools.memory_context()
        if known:
            kwargs["input"] = f"[{MEMORY_NOTE}\n\n{known}]\n\n{text}"
    if tools:
        kwargs["tools"] = tools
    if mode == "voice":
        # Low thinking answers in ~3 s instead of ~5 s with no loss we could hear.
        kwargs["generation_config"] = {"thinking_level": "low"}
    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id

    try:
        interaction = client.interactions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — surface upstream failures cleanly
        raise HTTPException(
            status_code=502,
            detail=f"Gemini request failed: {exc}",
        ) from exc

    rounds = 0
    while agent_tools is not None and tools and rounds < MAX_TOOL_ROUNDS:
        calls = _function_calls(interaction)
        if not calls:
            break
        rounds += 1
        results: list[dict[str, Any]] = []
        for step in calls:
            name = getattr(step, "name", None) or ""
            payload = agent_tools.dispatch(name, _call_args(step))
            results.append(
                {
                    "type": "function_result",
                    "name": name,
                    "call_id": _call_id(step),
                    "result": [{"type": "text", "text": result_as_text(payload)}],
                }
            )
        try:
            interaction = client.interactions.create(
                model=MODEL,
                previous_interaction_id=interaction.id,
                input=results,
                tools=tools,
                system_instruction=instructions(mode),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Gemini tool follow-up failed: {exc}",
            ) from exc

    reply = _output_text(interaction)
    return {
        "reply": reply or "I’m listening — tell me more about the goal.",
        "interaction_id": interaction.id,
    }
