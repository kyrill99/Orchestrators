from __future__ import annotations

import os
import sys
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from pydantic import BaseModel, Field

from services.agent import chat as agent_chat
from services.agent import configure_tools
from services.agent_tools import AgentTools
from services.gradium import create_browser_token

from changelog import build_router as build_changelog_router
from conversations import CHANNEL as CONVERSATIONS_CHANNEL
from conversations import ConversationService, ConversationStore
from conversations import build_router as build_conversations_router
from db import Database
from identity import current_user
from events import Broker, PublishingEventStore
from events import build_router as build_events_router
from issues import IssueService
from issues import build_router as build_issues_router
from memory import MemoryService
from memory.api import build_router as build_memory_router
from memory.distill import GeminiDistiller
from selfupdate import build_router as build_selfupdate_router
from storage import open_memory_store, open_taskgraph_store
from sysstats import build_router as build_sysstats_router
from taskgraph import TaskGraphService
from taskgraph.api import build_router
from userstate import UserStateStore
from userstate import build_router as build_userstate_router

load_dotenv()

# Agent runs execute the Gemini worker (taskgraph/worker.py) unless configured
# otherwise; set TASKGRAPH_RUNNER=record to only track runs.
os.environ.setdefault("TASKGRAPH_RUNNER", "subprocess")
os.environ.setdefault("TASKGRAPH_RUNNER_CMD", f"{sys.executable} -m taskgraph.worker")

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

# Shared local SQLite database (data/orchestrators.db); see db.py.
# Task graph and memory backends are chosen in storage.py.
db = Database()

# In-process pub/sub for live updates (SSE); see events.py. One uvicorn worker.
broker = Broker()
app.include_router(build_events_router(broker, ["graph", CONVERSATIONS_CHANNEL]))

# Every task graph event appended by this process is also published live on
# the "graph" channel (GET /api/events?channel=graph).
taskgraph = TaskGraphService(
    PublishingEventStore(open_taskgraph_store(db), broker, "graph")
)
app.include_router(build_router(taskgraph))

memory = MemoryService(
    open_memory_store(db),
    GeminiDistiller(MODEL) if os.environ.get("GEMINI_API_KEY") else None,
)
app.include_router(build_memory_router(memory, taskgraph))

# Issue list; screenshots under data/issues (ISSUES_PATH).
issues = IssueService(db)
app.include_router(build_issues_router(issues))

# Interview chat uses the same memory/taskgraph/issue ops as MCP, in-process.
configure_tools(AgentTools(memory, taskgraph, issues=issues))

# Must match DATA in deploy/gcp/update.sh.
app.include_router(build_selfupdate_router("data"))
app.include_router(build_changelog_router("data"))
app.include_router(build_sysstats_router())
# GET /api/me plus per-user state; replaces identity.build_router().
app.include_router(build_userstate_router(UserStateStore(db)))

app.include_router(
    build_conversations_router(ConversationService(ConversationStore(db), broker))
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
def agent_chat_endpoint(body: ChatRequest, request: Request):
    """Strategy agent reply (Gemini). Pair with Gradium TTS on the client for voice."""
    return agent_chat(
        body.message,
        previous_interaction_id=body.previous_interaction_id,
        mode=body.mode,
        user=current_user(request).email,
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
