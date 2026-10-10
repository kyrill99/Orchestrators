"""Strategy-interview agent powered by Gemini."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException
from google import genai

MODEL = "gemini-3.8-flash"

SYSTEM_INSTRUCTION = """You are the Orchestrators strategy interview agent.
You interview the user about goals, priorities, and what success looks like.
Never ask about implementation details, tech stacks, or how to build things.
Keep replies concise (2–4 short sentences) and conversational — suitable for
being spoken aloud. Ask one focused follow-up question when useful."""


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
) -> dict[str, Any]:
    """Send a user message and return the agent reply + interaction id."""
    text = message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")

    client = _client()
    if previous_interaction_id:
        prompt = text
    else:
        prompt = f"{SYSTEM_INSTRUCTION}\n\nUser: {text}"

    kwargs: dict[str, Any] = {
        "model": MODEL,
        "input": prompt,
    }
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
