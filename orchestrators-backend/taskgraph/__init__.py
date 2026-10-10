"""Task graph harness shared by the GUI (REST) and the planner agent (MCP).

    from taskgraph import TaskGraphService, JsonlEventStore, Actor
    svc = TaskGraphService(JsonlEventStore("data/taskgraph.jsonl"))
"""

from .models import Actor, AgentRun, Changeset, Event, Op, Task
from .runs import RecordRunner, RunManager, SubprocessRunner
from .service import TaskGraphError, TaskGraphService
from .store import JsonlEventStore, MemoryEventStore

__all__ = [
    "Actor",
    "AgentRun",
    "Changeset",
    "Event",
    "JsonlEventStore",
    "MemoryEventStore",
    "Op",
    "RecordRunner",
    "RunManager",
    "SubprocessRunner",
    "Task",
    "TaskGraphError",
    "TaskGraphService",
]
