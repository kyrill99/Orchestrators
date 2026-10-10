import os

from dotenv import load_dotenv
from fastapi import FastAPI
from google import genai

from taskgraph import JsonlEventStore, TaskGraphService
from taskgraph.api import build_router

load_dotenv()

MODEL = "gemini-3.8-flash"

app = FastAPI(
    title="Orchestrators API",
    description="Backend for agentic project orchestration",
    version="0.1.0",
)


taskgraph = TaskGraphService(
    JsonlEventStore(os.environ.get("TASKGRAPH_PATH", "data/taskgraph.jsonl"))
)
app.include_router(build_router(taskgraph))


@app.get("/health")
def health():
    return {"status": "ok"}


def _smoke_gemini() -> None:
    """Local smoke script: python -c 'from main import _smoke_gemini; _smoke_gemini()'"""
    client = genai.Client()
    first = client.interactions.create(
        model=MODEL,
        input="Give me one sentence on what an orchestrator agent does.",
    )
    print(first.output_text)
    second = client.interactions.create(
        model=MODEL,
        input="Now give a concrete example.",
        previous_interaction_id=first.id,
    )
    print(second.output_text)


if __name__ == "__main__":
    _smoke_gemini()
