"""Strategy-interview agent powered by Gemini."""

from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import HTTPException
from google import genai

MODEL = "gemini-3.8-flash"

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

Your character:
- Calm, curious and direct, like a good editor or a seasoned partner at a small firm.
- You have opinions. When you can guess the answer, offer the guess and ask the owner to \
confirm or correct it, rather than asking an open question.
- Warm but not chatty. No flattery, no "great question", no filler. Dry wit, sparingly.
- One question at a time, easy to answer in a sentence.
- Every few turns, play back what you have understood in one sentence so the owner can \
correct you.
- Once you know the goal, the project type, what success looks like and the main \
priorities, say so, sum up the strategy in two or three sentences and offer to hand it to \
the agents.

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


def instructions(mode: Mode) -> str:
    return f"{CHARACTER}\n\n{STYLE[mode]}"


def _client() -> genai.Client:
    if not os.getenv("GEMINI_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY is not configured on the server",
        )
    return genai.Client()


def chat(
    message: str,
    *,
    previous_interaction_id: str | None = None,
    mode: Mode = "text",
) -> dict[str, Any]:
    """Send a user message and return the agent reply + interaction id."""
    text = message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")

    client = _client()
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "input": text,
        "system_instruction": instructions(mode),
    }
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

    reply = getattr(interaction, "output_text", None) or ""
    if not reply and getattr(interaction, "outputs", None):
        for part in interaction.outputs or []:
            if getattr(part, "text", None):
                reply = part.text
                break

    return {
        "reply": reply.strip() or "I’m listening — tell me more about the goal.",
        "interaction_id": interaction.id,
    }
