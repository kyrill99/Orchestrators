# Memory API

What the agents learn and keep across sessions. One `MemoryService` behind REST
(`api.py`, for the GUI and the conversation code) and MCP (`mcp_server.py`, for agents).

## Scopes (narrowest wins on conflict)

| scope | holds | folder |
|---|---|---|
| `project` | strategy, success criteria, decisions, findings of one project | `projects/<projectId>/` |
| `user` | owner preferences that hold across projects | `users/<userId>/` |
| `global` | lessons about how the orchestrator itself should behave | `global/` |

## Model

Each memory is a markdown file `<scope folder>/<kind>/<slug>.md` under `data/memory`
(`MEMORY_PATH`): frontmatter with one `key: <json>` per line, then the body. Files can be
edited by hand. `log.jsonl` records every create / update / use / feedback.

- **Memory**: `id, scope, projectId, userId, kind (strategy|preference|decision|learning|note),
  title, body, tags[], source {type: conversation|decision|changeset|manual, ref},
  confidence (0–1), uses, helpful, unhelpful, status (active|superseded|archived),
  supersededBy, createdBy, createdAt, updatedAt`
- The id is the file path with dots: `projects.chunking-evaluation.strategy.success-criteria`.
- Storing a memory with the same scope, kind and title updates it; the replaced text is
  kept in the log as `previousBody`. Titles over 60 characters get a hash in the file
  name, so two long titles that start the same stay separate.

## The loop

1. **Before a session**: `context` returns a markdown block for the system prompt with
   all strategy and preferences that apply, plus the best-matching learnings, decisions
   and notes. Each memory in it gets `uses + 1`.
2. **During**: the agent calls `recall` before asking the owner anything and `remember`
   when the owner states a goal, preference or correction.
3. **After**: `reflect` sends the transcript and the existing memories to Gemini, which
   answers with `create` / `update` / `supersede` / `reinforce` operations.
   `sync` copies resolved decisions and owner-rejected changesets from the task graph.
4. **Feedback**: helpful `+0.1` confidence, not helpful `−0.2`; below `0.2` the memory is
   archived. Search ranks by keyword match × confidence.

## REST (`/api/memory`)

| | |
|---|---|
| `GET /context?projectId=&userId=&q=&limit=` | `{text, memoryIds}` for an agent prompt |
| `POST /reflect` | `{actor, messages[{role, content}], projectId, userId, ref?}` → `{applied, skipped}`; 503 without `GEMINI_API_KEY` |
| `POST /sync?projectId=` | pull decisions and rejected changesets from the task graph |
| `GET /memories?scope=&projectId=&userId=&kind=&status=&q=` | list, or search with `q` |
| `GET /memories/{id}` | one memory |
| `POST /memories` | `{actor, title, body, kind, scope?, projectId?, userId?, tags?, source?}` |
| `PATCH /memories/{id}` | `{actor, title?, body?, tags?, confidence?, status?}` |
| `DELETE /memories/{id}` | archive |
| `POST /memories/{id}/feedback` | `{actor, helpful, note?}` |
| `GET /tree` | folders and files for the Memory page |
| `GET /stats`, `GET /log?limit=` | counts and helpful rate; the learning log |

Errors: `{code, message}` with 404 not_found, 503 unavailable, 422 invalid.

## MCP tools

`get_context(query?)`, `recall(query, kind?, limit?)`, `remember(title, body, kind, scope?, tags?)`,
`memory_feedback(memory_id, helpful, note?)`, `list_memories(scope?, kind?)`.

They are registered on the task graph server (`python -m taskgraph.mcp_server`), so the
planner needs one connection. The project and owner come from `MEMORY_PROJECT_ID` and
`MEMORY_USER_ID` (default `default`). `python -m memory.mcp_server` runs them alone.

## Interview agent (`POST /api/agent/chat`)

The chat has the same tools in-process (`services/agent_tools.py`). Two things happen
without the model having to choose them:

- The owner is the signed-in user (`identity.current_user`): `anna@example.com` reads and
  writes `users/anna-example-com/`. Setting `MEMORY_USER_ID` overrides this for everyone.
- The first message of a conversation is sent with `context()` in front of it, so a new
  chat starts from what earlier sessions stored.

```bash
cd orchestrators-backend
python -m memory.seed                 # demo memories for "Chunking evaluation"
python -m unittest discover tests
```

## Example

```python
from memory import MemoryService, MarkdownMemoryStore, ChatMessage
from taskgraph import Actor

mem = MemoryService(MarkdownMemoryStore("data/memory"), distiller)
system_prompt += mem.context(project_id="chunking-evaluation", user_id="lorenz")["text"]
# ... conversation ...
mem.reflect(Actor(type="agent", id="orchestrator-agent"), messages,
            project_id="chunking-evaluation", user_id="lorenz", ref=chat_id)
```
