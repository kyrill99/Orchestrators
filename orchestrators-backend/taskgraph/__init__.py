"""Task graph harness shared by the GUI (REST) and the planner agent (MCP).

    from taskgraph import TaskGraphService, JsonlEventStore, Actor
    svc = TaskGraphService(JsonlEventStore("data/taskgraph.jsonl"))
"""

from .models import Actor, Changeset, Event, Op, Task
from .service import TaskGraphError, TaskGraphService
from .store import JsonlEventStore, MemoryEventStore

__all__ = [
    "Actor",
    "Changeset",
    "Event",
    "JsonlEventStore",
    "MemoryEventStore",
    "Op",
    "Task",
    "TaskGraphError",
    "TaskGraphService",
]
