"""Seed memory with the "Chunking evaluation" demo (same content as the
frontend fixtures, plus the preferences Lorenz states in the task comments).

Run:  python -m memory.seed        (storage: see storage.py)
"""

from __future__ import annotations

from taskgraph.models import Actor

from .service import MemoryService

PROJECT = "chunking-evaluation"
OWNER = "lorenz"

LORENZ = Actor(type="human", id=OWNER)
RESEARCH = Actor(type="agent", id="research-agent")
PROTOTYPE = Actor(type="agent", id="prototype-agent")
ORCHESTRATOR = Actor(type="agent", id="orchestrator-agent")

SEED = [
    (LORENZ, dict(
        scope="project", kind="strategy", title="Mission",
        body="Evaluate our chunking algorithm: compare candidate approaches and ship an "
             "evaluation report with a runnable prototype.",
    )),
    (LORENZ, dict(
        scope="project", kind="strategy", title="Success criteria",
        body="- Compare chunking approaches on recall, latency, and coherence\n"
             "- Coherence first, then latency\n"
             "- Surface only strategy-level decisions to Lorenz",
    )),
    (RESEARCH, dict(
        scope="project", kind="note", title="Literature notes", tags=["research"],
        body="- Fixed windows: cheap baseline\n"
             "- Semantic splits: better boundaries, embedding cost\n"
             "- Hierarchical: summaries for long-context retrieval",
    )),
    (PROTOTYPE, dict(
        scope="project", kind="note", title="Benchmark plan", tags=["prototype"],
        body="Dataset: internal docs corpus (sample 200).\n"
             "Metrics: chunk coherence, retrieval recall@k, p95 latency.",
    )),
    (LORENZ, dict(
        scope="user", kind="preference", title="No A-vs-B questions",
        body="Do not ask the owner to pick between implementation options. If the "
             "success criteria cover it, decide and record the reasoning in the decision log.",
    )),
    (ORCHESTRATOR, dict(
        scope="global", kind="learning", title="Ask for the success metric first",
        tags=["research", "interview"],
        body="For research-type projects, ask what success looks like and how it is "
             "measured before planning prototype tracks; it decides how they are compared.",
    )),
]


def main() -> None:
    from storage import open_memory_store

    service = MemoryService(open_memory_store())
    for actor, fields in SEED:
        m = service.remember(actor, project_id=PROJECT, user_id=OWNER, **fields)
        print(m.path)


if __name__ == "__main__":
    main()
