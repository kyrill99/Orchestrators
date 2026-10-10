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
| `TASKGRAPH_PATH` | JSONL event log: used with `TASKGRAPH_STORE=jsonl`, imported once otherwise (default `data/taskgraph.jsonl`) |
| `MEMORY_PATH` | Markdown memory folder: used with `MEMORY_STORE=markdown`, imported once otherwise (default `data/memory`) |
| `DEV_USER_EMAIL` | Signed-in user when there is no oauth2-proxy in front (optional, default `local@localhost`) |
| `CORS_ORIGINS` | Origins allowed to call the API (optional, default `http://localhost:3000`) |

Never commit `.env`.

## Persistence (SQLite)

Everything the backend keeps lives in one SQLite file (`DATABASE_PATH`, WAL mode,
shared by the FastAPI process and the MCP servers). Stores are built in
`storage.py` (`open_taskgraph_store`, `open_memory_store`); the old JSONL /
markdown stores stay selectable via `TASKGRAPH_STORE` / `MEMORY_STORE`.

| Table | Contents |
| --- | --- |
| `taskgraph_events` | Every task graph update, in order (`seq`); the graph is replayed from it on start |
| `memories`, `memory_log` | Memories (full JSON in `data`) and what happened to them |
| `users`, `user_state` | Who signed in (first/last seen) and per-user JSON values |
| `storage_imports` | Which JSONL / markdown sources were imported, and when |

On first start with the SQLite backends, an existing `TASKGRAPH_PATH` file and
`MEMORY_PATH` folder are imported once. The import only runs into empty tables
and is recorded in `storage_imports`, so restarts never duplicate data. The
original files are left in place but are no longer updated. Missing files are skipped.

```bash
.venv/bin/python -m storage info              # row counts per table, imports done
.venv/bin/python -m storage backup backups/   # consistent copy while the app runs (online backup API);
                                              # a directory gets orchestrators-<UTC time>.db
```

Back up with `python -m storage backup`, not `cp`: copying the `.db` file alone
can miss recent writes that are still in the `-wal` file. To restore, stop the
app, put the backup at `DATABASE_PATH`, and delete any `-wal` / `-shm` files
next to it.

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
| `GET` | `/api/me/state` | All of the user's stored values, `{key: value}` |
| `GET` / `PUT` / `DELETE` | `/api/me/state/{key}` | One per-user JSON value (PUT body is any JSON, max 64 KB; keys `A-Za-z0-9_.:-`, max 128 chars) |
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
  agent_identity/    # who Orchestrators is + role cards; every agent's prompt starts here
  strategy_interview/  # objective interview & strategy picture (see its README)
  tests/
  requirements.txt
  .env.example
  README.md
```
