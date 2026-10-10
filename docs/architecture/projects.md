# Projects

A **project** is one body of work with its own task graph and a short **brief**:
a top-level description that the planner (strategy agent) and every task worker
receive as context. Teammates can each have a different project open and see who
is where.

## Goals

- Create projects in the UI, switch between them, edit the brief.
- Each project has its own task graph, runs, activity and decisions.
- The brief is injected into the planner's opening context and every worker prompt.
- Presence shows which project each teammate has open.
- Bug reports (screenshot / bug button) land as tasks in the **Orchestrators** project.
- Every existing API keeps working unchanged (no per-call-site changes).

Non-goals (for now): permissions per project, moving tasks between projects,
deleting projects.

## Memory

Memory has three scopes (see `orchestrators-backend/memory/API.md`):

| scope | shared with | follows |
|---|---|---|
| `project` | nobody outside the project | the active project (same cookie / contextvar as the task graph) |
| `user` | all projects of that person | the signed-in user |
| `global` | all projects, everyone | nothing; one shared set |

`MemoryService` is built with `project=current_project.get`, so any call without an
explicit `project_id` reads and writes the request's project. Runs happen outside a
request: each project's `GeminiRunner` carries its `project_id` and passes it on.
Subprocess / MCP workers use `TASKGRAPH_PROJECT`. Memories written before projects
existed sit under `projects/default` and stay with the default project.

## How the active project travels

```
browser ── cookie orch_project=<id> ──▶ oauth2-proxy ──▶ FastAPI
                                                         │ ProjectMiddleware (ASGI)
                                                         │   sets contextvar current_project
                                                         ▼
                        routers / AgentTools / loop ──▶ ScopedProxy ──▶ ProjectGraphs[id]
                                                                         ├─ TaskGraphService
                                                                         └─ RunManager (+ runner)
```

- The frontend stores the active project in the cookie `orch_project` (default
  `default`). Browsers send it with every same-origin request, including
  `EventSource` streams, so no fetch call needs to change. Switching sets the
  cookie and reloads.
- `ProjectMiddleware` (pure ASGI) reads the cookie, falls back to `default` for
  unknown ids, and sets the `current_project` context variable for the request.
  Starlette's threadpool and asyncio tasks copy context, so sync endpoints and
  agent tool calls see it too.
- `ScopedProxy` objects stand in for the task graph service and the run manager
  everywhere they were passed before (`build_router`, `AgentTools`, memory router).
  Each attribute access resolves to the current project's instance, so a request
  only ever touches its own project's graph.

## Storage

| What | Where |
|---|---|
| Project registry (`id, name, description, created_by, created_at`) | table `projects` in the main SQLite DB (`data/orchestrators.db`) |
| Task graph of `default` | unchanged: the existing `taskgraph_events` table in the main DB |
| Task graph of any other project | its own SQLite file `data/projects/<id>.db` (same `taskgraph_events` schema) |

Separate files keep the existing event-log code untouched (one log per store,
sequence numbers per project) and make a project easy to copy or delete.

Seeded on first start: `default` (the existing graph) and `orchestrators`
(the Orchestrators app itself, target for bug reports).

## Agents

- **Planner / strategy chat**: the opening context gets a `## Project` section with
  the project name and brief, ahead of memory and the board summary.
- **Workers**: each project has its own `RunManager` and runner. Subprocess runs get
  `TASKGRAPH_PROJECT` and `TASKGRAPH_DB` in their environment; `taskgraph/worker.py`
  opens that project's store and puts the brief at the top of its prompt.
- Run exit callbacks are bound to the concrete project manager (not the proxy), so
  a run that ends after the request is over still records into the right project.

## API

| | |
|---|---|
| `GET /api/projects` | `[{id, name, description, createdBy, createdAt, viewers[]}]`; `viewers` = emails whose presence heartbeat is in that project |
| `GET /api/projects/current` | the project selected by the cookie |
| `POST /api/projects` `{name, description}` | create (id = slug of the name), 201 |
| `PATCH /api/projects/{id}` `{name?, description?}` | edit the brief |

Presence entries gain `project` (set server-side from the cookie); the frontend
only shows teammates in the same project.

## Bug reports

The `orchestrators` project has one standing task, `work-open-issues` ("Work through
open issues"), created at startup. Its brief tells the task agent to go through the open
issues (list, investigate, comment, close). When a human files a bug (an issue becomes
open), the task gets a comment "New issue #n: ..." and is reopened if it was done, so
the engine picks it up again. Issues filed by task agents do not wake it.

## Risks / follow-ups

- The live "graph" SSE channel is shared: a change in project B makes clients of
  project A refetch their own (unchanged) graph. Harmless, slightly chatty.
- MCP server processes (outside FastAPI) still use the default project unless
  started with `TASKGRAPH_DB`/`TASKGRAPH_PROJECT`.
- No auth per project: anyone signed in sees all projects.
- Task workers read memory as user `default`, not as the person who owns the project,
  so they see project and global memories but not anyone's personal ones.
- Issues created through the MCP server (not the FastAPI app) do not become tasks.
- `python -m storage backup` copies only the main DB, not `data/projects/*.db`.

## Agent conversations

Conversations belong to a project (`conversations.project`, added by migration; old ones
are `default`). `GET /api/conversations` lists only the current project's; live
`conversation` events carry `project` and the frontend ignores other projects'. When a
message is posted, the planner runs in the **conversation's** project (not the poster's
cookie), so its tools, board summary and project brief always match that chat.
