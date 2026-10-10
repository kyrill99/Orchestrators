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
| `GET /runs?taskId=&active=` | agent runs (who works on what) |
| `POST /tasks/{id}/run/start` | `{actor, reason?, agentId?, instructions?}` → the run (agent defaults to `worker`) |
| `POST /tasks/{id}/run/stop` | `{actor, reason?}` → the stopped run; 409 if the task has no active run |
| `POST /tasks/{id}/run/restart` | `{actor, reason?, agentId?, instructions?}` → the new run (stops the active one first) |
| `GET /changesets?status=` | changesets |
| `POST /changes` | `{actor, ops[], reason, baseSeq?}` → `{changesetId, status, seq, createdIds}` |
| `POST /changesets/{id}/review` | `{actor, approve, note}` |
| `PATCH /settings` | `{actor, name?, autonomy?}` |

Errors: `{code, message, opIndex}` with 404 not_found, 409 conflict, 403 forbidden, 422 invalid/blocked/cycle,
500 runner (a run could not be launched).

## MCP tools (planner)

Reads: `get_graph`, `get_task`, `ready_tasks`, `blocked_tasks`, `list_decisions`, `list_activity`,
`list_changesets`, `get_events`.
Writes: `apply_changes(ops, reason)` (atomic batch) plus single-op tools `create_task`,
`update_task`, `set_status`, `assign_task`, `delete_task`, `add_relation`,
`remove_relation`, `add_comment`, `request_decision`, `resolve_decision`.
Runs: `start_task`, `stop_task`, `restart_task`, `start_ready_tasks`, `stop_all_runs`,
`finish_run`, `list_active_runs`, `list_runs`, `get_run`.
Memory tools are on the same server (see `../memory/API.md`). Reviewing changesets and
project settings stay human-only (REST).

```bash
cd orchestrators-backend
python -m taskgraph.mcp_server        # stdio; env TASKGRAPH_PATH, TASKGRAPH_AGENT_ID
python -m unittest discover tests     # tests
```

Hook it into an agent, e.g. Claude Code (`.mcp.json` in the project the planner runs in):

```json
{"mcpServers": {"taskgraph": {
  "command": "python", "args": ["-m", "taskgraph.mcp_server"],
  "cwd": "orchestrators-backend",
  "env": {"TASKGRAPH_PATH": "data/taskgraph.jsonl", "TASKGRAPH_AGENT_ID": "planner"}}}}
```

Point `TASKGRAPH_PATH` at the same file as the FastAPI app so the GUI sees the planner's
changes and runs live.

## Agent runs (`runs.py`)

A run is one attempt by an agent to work on a task. Runs are events in the same log
(`run.started`, `run.updated`, `run.ended`), so every process sees them; `GET /graph`
lists `activeRuns`, `GET /tasks/{id}` has `activeRun`, and `GET /runs?taskId=&active=`
lists them. The REST run endpoints and the MCP run tools call the same `RunManager`; the
FastAPI app uses the runner from `TASKGRAPH_RUNNER` too. Runs are not changesets: the autonomy switch does not queue them, except
that under `ask-all` only humans may start runs.

- **start**: the task must be startable (not closed, blockers closed, no pending decision,
  no other active run). It moves to `running` and is assigned to the run's agent.
- **stop**: the runner stops the work; the task goes back to `open`.
- **restart**: stop the active run if any, start attempt n+1 (agent and instructions carry over).
- **finish / exit**: `succeeded` moves the task to `in-review`, `failed` back to `open`.
- A task with an active run cannot be deleted.

What executes a run depends on `TASKGRAPH_RUNNER`:

| runner | start | stop | end |
|---|---|---|---|
| `record` (default) | **nothing is launched**; the run is a record that an agent works on the task | recorded only | someone calls `finish_run` |
| `subprocess` | launches `TASKGRAPH_RUNNER_CMD` (`{run_id}`, `{task_id}`, `{agent_id}` filled in; task brief on stdin; env `TASKGRAPH_RUN_ID`, `TASKGRAPH_TASK_ID`, `TASKGRAPH_AGENT_ID`); output in `TASKGRAPH_RUN_LOGS` (default `<dir of TASKGRAPH_PATH>/runs/<run>.log`) | SIGTERM to the process group, SIGKILL after 5 s | exit code 0 → `succeeded`, else `failed` (or the worker calls `finish_run` first) |

Example worker (untested): `TASKGRAPH_RUNNER_CMD='claude -p --mcp-config worker-mcp.json'`,
where `worker-mcp.json` runs this server for the worker, so it can comment and call
`finish_run`. Give that server the worker's id as `TASKGRAPH_AGENT_ID` so its changes are
attributed to the worker, not the planner.

A subprocess run belongs to the server process that launched it; when that process
exits, its children keep running but nobody records their exit. The next server on the
same host marks such runs `lost` once their pid is gone, and can stop them by pid.

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
