"""Memory tools for agents over MCP.

`register_tools` adds them to any MCP server; the task graph server does this so
the planner has one connection. Standalone:

Run:  python -m memory.mcp_server
Env:  MEMORY_PATH        memory directory (default: data/memory)
      MEMORY_PROJECT_ID  project the agent works on (default: default)
      MEMORY_USER_ID     project owner (default: default)
      MEMORY_AGENT_ID    agent id recorded on changes (default: planner)
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from taskgraph.models import Actor

from .models import MemoryKind, MemorySource, Scope
from .service import DEFAULT_ID, MemoryService, MemoryServiceError
from .store import MarkdownMemoryStore

INSTRUCTIONS = """Memory holds what is already known about the owner, this project and
how to orchestrate well. Call get_context when you start. Call recall before you ask
the owner anything; if memory answers it, do not ask. Call remember when the owner
states a goal, priority, success criterion, preference or correction. Call
memory_feedback when a memory turned out right (helpful) or the owner contradicted
it (not helpful)."""


def _dump(out: Any) -> Any:
    if isinstance(out, list):
        return [_dump(item) for item in out]
    return out.dump() if hasattr(out, "dump") else out


def service_from_env() -> MemoryService:
    return MemoryService(MarkdownMemoryStore(os.environ.get("MEMORY_PATH", "data/memory")))


def register_tools(
    server: MCPServer,
    service: MemoryService,
    agent_id: str = "planner",
    *,
    project_id: str | None = None,
    user_id: str | None = None,
) -> None:
    me = Actor(type="agent", id=agent_id)
    project = project_id or os.environ.get("MEMORY_PROJECT_ID", DEFAULT_ID)
    user = user_id or os.environ.get("MEMORY_USER_ID", DEFAULT_ID)

    def run(fn, *args, **kwargs) -> Any:
        try:
            out = fn(*args, **kwargs)
        except MemoryServiceError as e:
            raise ToolError(f"{e.code}: {e}") from None
        return _dump(out)

    @server.tool()
    def get_context(query: str | None = None) -> dict:
        """Everything memory knows that applies now: project strategy, owner
        preferences, orchestrator lessons, plus entries matching `query`."""
        return run(service.context, project_id=project, user_id=user, query=query)

    @server.tool()
    def recall(query: str, kind: MemoryKind | None = None, limit: int = 5) -> list[dict]:
        """Search memory. Use before asking the owner a question."""
        return run(service.search, query, project_id=project, user_id=user, kind=kind, limit=limit)

    @server.tool()
    def remember(
        title: str,
        body: str,
        kind: MemoryKind,
        scope: Scope = "project",
        tags: list[str] | None = None,
    ) -> dict:
        """Store something worth knowing next session. scope 'project' for this
        project, 'user' for an owner preference that holds across projects,
        'global' for a lesson about orchestrating in general. Same title updates."""
        return run(
            service.remember, me, title=title, body=body, kind=kind, scope=scope,
            project_id=project, user_id=user, tags=tags or [],
            source=MemorySource(type="conversation"),
        )

    @server.tool()
    def memory_feedback(memory_id: str, helpful: bool, note: str | None = None) -> dict:
        """Report whether a memory held up. Not helpful lowers its confidence;
        repeated, it is archived."""
        return run(service.feedback, me, memory_id, helpful, note)

    @server.tool()
    def list_memories(scope: Scope | None = None, kind: MemoryKind | None = None) -> list[dict]:
        """List active memories, optionally by scope or kind."""
        return run(service.list_memories, scope=scope, kind=kind)


def main() -> None:
    server = MCPServer("orchestrators-memory", instructions=INSTRUCTIONS)
    register_tools(server, service_from_env(), os.environ.get("MEMORY_AGENT_ID", "planner"))
    server.run("stdio")


if __name__ == "__main__":
    main()
