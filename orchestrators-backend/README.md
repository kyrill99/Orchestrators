# Orchestrators Backend

API and agent orchestration services for Orchestrators.

## Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI
- **ASGI server:** Uvicorn
- **LLM:** Google Gemini (`google-genai`)
- **Config:** `python-dotenv` (`.env`)

## Setup

```bash
cd orchestrators-backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
cp .env.example .env
# fill in API keys in .env
pip install -r requirements.txt
```

## Environment

See [`.env.example`](.env.example). Required / expected keys:

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Google Gemini API access |
| `GRADIUM_API_KEY` | Gradium API access |
| `DATABASE_PATH` | Shared SQLite file (default `data/orchestrators.db`) |
| `TASKGRAPH_STORE` | `sqlite` (default) or `jsonl` |
| `MEMORY_STORE` | `sqlite` (default) or `markdown` |
| `TASKGRAPH_PATH` | JSONL log; also one-time import source into SQLite |
| `MEMORY_PATH` | Markdown memory dir; also one-time import source into SQLite |
| `CORS_ORIGINS` | Origins allowed to call the API (optional, default `http://localhost:3000`) |

Never commit `.env`.

## Persistence (SQLite)

By default the task graph and memory live in one SQLite file (`DATABASE_PATH`), shared by the FastAPI process and MCP servers (WAL mode). Legacy JSONL/markdown backends remain selectable via `TASKGRAPH_STORE` / `MEMORY_STORE`.

On first SQLite start, existing `TASKGRAPH_PATH` / `MEMORY_PATH` data is imported once (recorded in `storage_imports`; source files are left in place).

```bash
.venv/bin/python -m storage info              # row counts + imports
.venv/bin/python -m storage backup backups/   # online consistent copy
```

Per-user state (`GET/PUT /api/me/state/...`) also uses the same database.

## Run

```bash
# Dev API server (FastAPI) — use the venv binary
.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Or the Gemini interactions smoke script (if using the script entrypoint)
.venv/bin/python main.py
```

API docs (when the FastAPI app is running): [http://localhost:8000/docs](http://localhost:8000/docs)

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness |
| `GET` | `/api/me` | Signed-in user (oauth2-proxy header or `DEV_USER_EMAIL`) |
| `GET/PUT/DELETE` | `/api/me/state/...` | Per-user JSON preferences |
| `POST` | `/api/voice/token` | Mint a short-lived Gradium WebSocket token (STT/TTS) |
| `POST` | `/api/agent/chat` | Strategy agent reply via Gemini (`message`, optional `previous_interaction_id`) |

Voice flow: the browser asks for a token, opens Gradium STT/TTS WebSockets with `?token=...`, streams mic audio into the agent textbox, then speaks the agent reply with TTS. The Gradium API key never leaves the server.

## Memory, task graph, and the interview agent

- **REST** (GUI): `/api/memory`, `/api/taskgraph` — see [memory/API.md](memory/API.md) and [taskgraph/API.md](taskgraph/API.md).
- **MCP** (external planners): `python -m taskgraph.mcp_server` (task graph + memory tools on one stdio server), or `python -m memory.mcp_server` alone.
- **Interview chat** (`POST /api/agent/chat`): uses the **same tool names in-process** via Gemini function calling against the shared `MemoryService` / `TaskGraphService` instances (no MCP stdio from the API process). Bind project/owner with `MEMORY_PROJECT_ID` / `MEMORY_USER_ID` (default `default`).

## Layout

```
orchestrators-backend/
  main.py            # FastAPI app entry
  db.py              # shared SQLite helper
  storage.py         # backend factory, import, backup CLI
  userstate.py       # /api/me and per-user state
  services/
    gradium.py       # Gradium browser token minting
    agent.py         # Gemini strategy chat + tool loop
    agent_tools.py   # memory/taskgraph tool dispatch (MCP vocabulary)
  taskgraph/         # task graph: service, REST, MCP, sqlite_store
  memory/            # memory: service, REST, MCP, sqlite_store
  strategy_interview/  # objective interview & strategy picture (see its README)
  tests/
  requirements.txt
  .env.example
  README.md
```
