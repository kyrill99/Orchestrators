"""Memory shared by the GUI (REST), the conversation code and agents (MCP).

    from memory import MemoryService, MarkdownMemoryStore
    mem = MemoryService(MarkdownMemoryStore("data/memory"))
    prompt = mem.context(project_id="chunking-evaluation", user_id="lorenz")["text"]
"""

from .models import ChatMessage, Memory, MemorySource, ReflectOp
from .service import MemoryService, MemoryServiceError
from .store import MarkdownMemoryStore

__all__ = [
    "ChatMessage",
    "MarkdownMemoryStore",
    "Memory",
    "MemoryService",
    "MemoryServiceError",
    "MemorySource",
    "ReflectOp",
]
