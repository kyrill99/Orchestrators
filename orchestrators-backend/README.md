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
| `MEMORY_PATH` | Memory directory (optional, default `data/memory`) |
| `CORS_ORIGINS` | Origins allowed to call the API (optional, default `http://localhost:3000`) |

Never commit `.env`.

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
  main.py            # FastAPI app entry (and temporary Gemini smoke script)
  services/
    gradium.py       # Gradium browser token minting
    agent.py         # Gemini strategy chat + tool loop
    agent_tools.py   # memory/taskgraph tool dispatch (MCP vocabulary)
  taskgraph/         # task graph: service, REST, MCP (see taskgraph/API.md)
  memory/            # memory across sessions: service, REST, MCP (see memory/API.md)
  strategy_interview/  # objective interview & strategy picture (see its README)
  tests/
  requirements.txt
  .env.example
  README.md
```
