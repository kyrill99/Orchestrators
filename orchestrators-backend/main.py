from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from pydantic import BaseModel, Field

from services.agent import chat as agent_chat
from services.agent import configure_brief, configure_tools
from services.agent_tools import AgentTools
from services.gemini_worker import GeminiRunner
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
from presence import CHANNEL as PRESENCE_CHANNEL
from presence import PresenceStore
from presence import build_router as build_presence_router
from multiplayer import build_router as build_multiplayer_router
from selfupdate import build_router as build_selfupdate_router
from storage import open_memory_store, open_taskgraph_store
from sysstats import build_router as build_sysstats_router
from taskgraph import TaskGraphService
from taskgraph.api import build_engine_router, build_router
from taskgraph.engine import Engine
from taskgraph.runlog import RunLog, Scratchpads
from taskgraph.runs import RunManager, runner_from_env
from taskgraph.sqlite_store import SqliteEventStore
from projects import DEFAULT as DEFAULT_PROJECT
from projects import ORCHESTRATORS as ORCHESTRATORS_PROJECT
from projects import ProjectGraphs, ProjectMiddleware, ProjectRegistry, ScopedProxy
from projects import build_router as build_projects_router
from userstate import UserStateStore
from userstate import build_router as build_userstate_router

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

# Shared local SQLite database (data/orchestrators.db); see db.py.
# Task graph and memory backends are chosen in storage.py.
db = Database()

# In-process pub/sub for live updates (SSE); see events.py. One uvicorn worker.
broker = Broker()
app.include_router(build_events_router(broker, ["graph", "runs", CONVERSATIONS_CHANNEL, PRESENCE_CHANNEL]))
# Live cursors, follow-mode viewports and broker events over one WebSocket
# per tab (WS /api/multiplayer/ws); presence itself is presence.py.
app.include_router(build_multiplayer_router(broker))

# Every task graph event appended by this process is also published live on
# the "graph" channel (GET /api/events?channel=graph).
default_taskgraph = TaskGraphService(
    PublishingEventStore(open_taskgraph_store(db), broker, "graph")
)

# Projects (docs/architecture/projects.md): the orch_project cookie picks a project
# per request; `taskgraph`, `runs`, `runlog`, `scratchpads` and `engine` below are
# proxies resolving to that project's own instances.
projects = ProjectRegistry(db)
app.add_middleware(ProjectMiddleware, registry=projects)
PROJECTS_DIR = (Path(db.path).parent if db.path != ":memory:" else Path("data")).resolve() / "projects"
project_graphs: ProjectGraphs  # built below, once memory and issues exist
taskgraph = ScopedProxy(lambda: project_graphs.pair()[0])

memory = MemoryService(
    open_memory_store(db),
    GeminiDistiller(MODEL) if os.environ.get("GEMINI_API_KEY") else None,
)
app.include_router(build_memory_router(memory, taskgraph))

# Issue list; screenshots under data/issues (ISSUES_PATH).
issues = IssueService(db)
app.include_router(build_issues_router(issues))


# Agent runs: a Gemini agent per run, in this process, when GEMINI_API_KEY is
# set (services/gemini_worker.py; TASKGRAPH_RUNNER overrides, e.g. "subprocess"
# with TASKGRAPH_RUNNER_CMD="python -m taskgraph.worker" for the one-shot
# worker, or "record"). Steps and scratchpads live in SQLite, and the engine
# starts runs on ready tasks while it is running.
# Steps and scratchpad edits are also published live on the "runs" channel.
def _publish_run_event(type_: str, data: dict) -> None:
    broker.publish("runs", type_, data)


def _project_parts(pid: str, service: TaskGraphService, pdb: Database, db_path: str | None):
    runlog = RunLog(pdb, _publish_run_event)
    scratchpads = Scratchpads(pdb, _publish_run_event)
    def _gemini() -> GeminiRunner:
        gemini = GeminiRunner(service, runlog, scratchpads, memory=memory, issues=issues)
        gemini.project_brief = lambda: projects.brief(pid)  # type: ignore[attr-defined]
        return gemini

    runner = runner_from_env(_gemini)
    if hasattr(runner, "env"):  # subprocess worker (TASKGRAPH_RUNNER=subprocess)
        runner.env.update({"TASKGRAPH_PROJECT": pid})
        if db_path:
            runner.env.update({"TASKGRAPH_DB": db_path,
                               "WORKER_ARTIFACTS": str(PROJECTS_DIR / f"{pid}-artifacts")})
            if hasattr(runner, "log_dir"):
                runner.log_dir = PROJECTS_DIR / f"{pid}-runs"
    manager = RunManager(service, runner)
    engine = Engine(service, manager, pdb)
    return service, manager, runlog, scratchpads, engine


def _new_project(pid: str):
    path = PROJECTS_DIR / f"{pid}.db"
    pdb = Database(path)
    service = TaskGraphService(PublishingEventStore(SqliteEventStore(pdb), broker, "graph"))
    parts = _project_parts(pid, service, pdb, str(path))
    parts[1].reconcile()
    parts[4].start_loop()
    return parts


project_graphs = ProjectGraphs(
    projects, _new_project, _project_parts(DEFAULT_PROJECT, default_taskgraph, db, None)
)
runs = ScopedProxy(lambda: project_graphs.pair()[1])
runlog = ScopedProxy(lambda: project_graphs.pair()[2])
scratchpads = ScopedProxy(lambda: project_graphs.pair()[3])
engine = ScopedProxy(lambda: project_graphs.pair()[4])
app.include_router(
    build_router(taskgraph, runs, runlog=runlog, scratchpads=scratchpads, engine=engine)
)
app.include_router(build_engine_router(engine))


@app.on_event("startup")
def _start_engine() -> None:
    # Runs this process did not finish before a restart are marked lost
    # (interrupted); the engine restarts them if it is running.
    default_parts = project_graphs.pair(DEFAULT_PROJECT)
    default_parts[1].reconcile()
    default_parts[4].start_loop()


@app.on_event("shutdown")
def _stop_engine() -> None:
    for parts in project_graphs.all():
        parts[4].stop_loop()


# Interview chat uses the same memory/taskgraph/issue ops as MCP, in-process.
configure_tools(AgentTools(memory, taskgraph, issues=issues))
configure_brief(projects.brief)


def _file_bug_task(issue: dict) -> None:
    """Bug reports become tasks in the Orchestrators project's graph."""
    from taskgraph import Actor
    from taskgraph.models import CreateTask

    number = issue.get("number")
    title = (issue.get("title") or "Bug report").strip()
    author = (issue.get("author") or {}).get("email") or "reporter"
    if str(author).startswith("agent:"):
        return  # filed by a task agent about its own project's work, not an app bug report
    task_id = f"bug-{number}" if number else None
    service = project_graphs.service(ORCHESTRATORS_PROJECT)
    if task_id and task_id in service.state.tasks:
        return
    body = (issue.get("description") or "").strip()
    ref = f"Issue #{number}" if number else f"Issue {issue.get('id')}"
    service.submit(
        Actor(type="human", id=str(author)),
        [CreateTask(id=task_id, title=f"Bug: {title}"[:200],
                    description=f"{body}\n\n{ref} (see the Issues page).".strip())],
        reason="bug report filed",
    )


issues.on_open = _file_bug_task

# Must match DATA in deploy/gcp/update.sh.
app.include_router(build_selfupdate_router("data"))
app.include_router(build_changelog_router("data"))
app.include_router(build_sysstats_router())
# GET /api/me plus per-user state; replaces identity.build_router().
app.include_router(build_userstate_router(UserStateStore(db)))

# Who is where / typing (ephemeral, in memory); see presence.py.
presence = PresenceStore(
    on_change=lambda entries: broker.publish(PRESENCE_CHANNEL, "presence", entries)
)
app.include_router(build_presence_router(presence))
app.include_router(build_projects_router(projects, presence.active))

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
