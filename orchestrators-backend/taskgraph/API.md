# Task graph API

One API for the GUI (REST, `api.py`) and the planner agent (MCP, `mcp_server.py`).
Both call `TaskGraphService`; every change is recorded in an append-only event log
(`data/taskgraph.jsonl`), so the activity feed, timeline and decision log are views
over the same events. GUI and MCP processes can share the log file safely.

## Model (JSON is camelCase, matching `orchestrators-frontend/lib/fixtures.ts`)

- **Task**: `id, title, kind (start|task|decision|end), status (open|running|in-review|closed),
  description, parentId, blockedBy[], relatedTo[], assignee {type: human|agent, id},
  startDate, endDate, progress (0–1), artifacts[], decision`
- **Decision** (on a task): `question, options[], context, state (pending|resolved), chosen, rationale, resolvedBy, resolvedAt`
- **Edge**: derived from `blockedBy` — `{id, source, target, type: "blocks"}`
- **Changeset**: a batch of ops by one actor with a `reason`; `status: applied|proposed|rejected`

## Operations (the only write vocabulary)

`task.create`, `task.update`, `task.set_status`, `task.assign`, `task.delete`,
`relation.add`, `relation.remove` (`type: blocks|related`), `comment.add`,
`decision.request`, `decision.resolve`.

A batch is validated as a whole: if any op fails, nothing is written. Rules:
no `blocks` cycles, no self-links, a task can only be running/in-review/closed when
all its blockers are closed, a pending decision closes via `decision.resolve`.
Agents must give a `reason`.

## Autonomy switch (project setting, humans only)

| autonomy | agent changesets |
|---|---|
| `auto` (default) | apply immediately; review afterwards in the log |
| `ask-structural` | create/delete/relations/decision.resolve wait for approval; status & comments apply |
| `ask-all` | every change waits for approval |

Humans' changes always apply. Humans approve/reject proposals.

## REST (`/api/taskgraph`)

| | |
|---|---|
| `GET /graph` | project, seq, tasks, edges, pendingDecisions, pendingChangesets |
| `GET /tasks/{id}` | task + activity, blocks, children |
| `GET /ready`, `GET /blocked` | startable tasks / what is stuck and why |
| `GET /decisions?state=` | decision log |
| `GET /activity?taskId=&since=` | activity feed |
| `GET /events?since=` | raw events for live polling |
| `GET /changesets?status=` | changesets |
| `POST /changes` | `{actor, ops[], reason, baseSeq?}` → `{changesetId, status, seq, createdIds}` |
| `POST /changesets/{id}/review` | `{actor, approve, note}` |
| `PATCH /settings` | `{actor, name?, autonomy?}` |

Errors: `{code, message, opIndex}` with 404 not_found, 409 conflict, 403 forbidden, 422 invalid/blocked/cycle.

## MCP tools (planner)

Reads: `get_graph`, `get_task`, `ready_tasks`, `blocked_tasks`, `list_decisions`, `list_activity`.
Writes: `apply_changes(ops, reason)` (atomic batch) plus single-op tools `create_task`,
`update_task`, `set_status`, `assign_task`, `delete_task`, `add_relation`,
`remove_relation`, `add_comment`, `request_decision`, `resolve_decision`.

```bash
cd orchestrators-backend
python -m taskgraph.mcp_server        # stdio; env TASKGRAPH_PATH, TASKGRAPH_AGENT_ID
python -m unittest discover tests     # tests
```

## Example

```json
POST /api/taskgraph/changes
{"actor": {"type": "agent", "id": "planner"}, "reason": "split research into prototypes",
 "ops": [
   {"op": "task.create", "id": "proto-1", "title": "Prototype Approach 1", "blockedBy": ["research"]},
   {"op": "task.create", "id": "decision", "title": "Decision: which approach", "kind": "decision", "blockedBy": ["proto-1"]},
   {"op": "decision.request", "taskId": "decision", "question": "Which matters more for this project: recall or latency?"}
 ]}
```
