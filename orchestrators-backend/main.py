from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from pydantic import BaseModel, Field

from services.agent import chat as agent_chat
from services.agent import configure_tools
from services.agent_tools import AgentTools
from services.gradium import create_browser_token

from changelog import build_router as build_changelog_router
from db import Database
from identity import build_router as build_identity_router
from memory import MarkdownMemoryStore, MemoryService
from memory.api import build_router as build_memory_router
from memory.distill import GeminiDistiller
from selfupdate import build_router as build_selfupdate_router
from sysstats import build_router as build_sysstats_router
from taskgraph import JsonlEventStore, TaskGraphService
from taskgraph.api import build_router

load_dotenv()

MODEL = "gemini-3.8-flash"

app = FastAPI(
    title="Orchestrators API",
    description="Backend for agentic project orchestration",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


taskgraph = TaskGraphService(
    JsonlEventStore(os.environ.get("TASKGRAPH_PATH", "data/taskgraph.jsonl"))
)
app.include_router(build_router(taskgraph))

memory = MemoryService(
    MarkdownMemoryStore(os.environ.get("MEMORY_PATH", "data/memory")),
    GeminiDistiller(MODEL) if os.environ.get("GEMINI_API_KEY") else None,
)
app.include_router(build_memory_router(memory, taskgraph))

# Interview chat uses the same memory/taskgraph ops as MCP, in-process.
configure_tools(AgentTools(memory, taskgraph))

# Must match DATA in deploy/gcp/update.sh.
app.include_router(build_selfupdate_router("data"))
app.include_router(build_changelog_router("data"))
app.include_router(build_sysstats_router())
app.include_router(build_identity_router())

# Shared local SQLite database (data/orchestrators.db); see db.py.
db = Database()


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
