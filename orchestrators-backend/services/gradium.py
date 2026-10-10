"""Gradium API helpers — mint short-lived browser tokens; never expose the API key."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException

GRADIUM_API_BASE = "https://api.gradium.ai/api"
GRADIUM_TOKEN_URL = f"{GRADIUM_API_BASE}/api-keys/token"

# Demo voice from Gradium docs (English flagship).
DEFAULT_VOICE_ID = "YTpq7expH9539ERJ"


def _api_key() -> str:
    key = os.getenv("GRADIUM_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="GRADIUM_API_KEY is not configured on the server",
        )
    return key


async def create_browser_token() -> dict[str, Any]:
    """Exchange the server API key for a single-use Gradium WebSocket token."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            GRADIUM_TOKEN_URL,
            headers={"x-api-key": _api_key()},
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="Could not create Gradium token",
        )

    payload = response.json()
    if "token" not in payload:
        raise HTTPException(
            status_code=502,
            detail="Gradium token response missing token",
        )
    return {
        "token": payload["token"],
        "expires_at": payload.get("expires_at"),
        "voice_id": DEFAULT_VOICE_ID,
        "asr_url": "wss://api.gradium.ai/api/speech/asr",
        "tts_url": "wss://api.gradium.ai/api/speech/tts",
    }
