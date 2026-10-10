from __future__ import annotations

from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from pydantic import BaseModel, Field

from services.agent import chat as agent_chat
from services.gradium import create_browser_token

load_dotenv()

MODEL = "gemini-3.8-flash"

app = FastAPI(
    title="Orchestrators API",
    description="Backend for agentic project orchestration",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    previous_interaction_id: str | None = None
    mode: Literal["text", "voice"] = "text"


class ChatResponse(BaseModel):
    reply: str
    interaction_id: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/voice/token")
async def voice_token():
    """Mint a short-lived Gradium token for browser STT/TTS WebSockets."""
    return await create_browser_token()


@app.post("/api/agent/chat", response_model=ChatResponse)
def agent_chat_endpoint(body: ChatRequest):
    """Strategy agent reply (Gemini). Pair with Gradium TTS on the client for voice."""
    return agent_chat(
        body.message,
        previous_interaction_id=body.previous_interaction_id,
        mode=body.mode,
    )


def _smoke_gemini() -> None:
    """Local smoke script: python -c 'from main import _smoke_gemini; _smoke_gemini()'"""
    client = genai.Client()
    first = client.interactions.create(
        model=MODEL,
        input="Give me one sentence on what an orchestrator agent does.",
    )
    print(first.output_text)
    second = client.interactions.create(
        model=MODEL,
        input="Now give a concrete example.",
        previous_interaction_id=first.id,
    )
    print(second.output_text)


if __name__ == "__main__":
    _smoke_gemini()
