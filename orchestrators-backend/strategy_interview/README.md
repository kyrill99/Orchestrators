# strategy_interview

The objective interview and strategy picture. A short interview fills one structured
**strategy picture** (goal, project type, success criteria, trade-offs, each with source
and confidence). Agents read it as a brief and decide implementation details themselves;
when they are unsure they call `ask_strategy`, which answers from the picture and only
asks the owner a strategy question when it has to.

Next to it, `../orchestration/` holds the tool contract the workflow generator uses to
call teammates' tools.

The full definition (schema, defaults, loop, stop rule, contracts) is in the spec doc
"Objective Interview & Strategy Picture — Spec".

## Setup

Follow the backend README (`cd orchestrators-backend`, `.venv`, `pip install -r
requirements.txt`, `GEMINI_API_KEY` in `orchestrators-backend/.env`). Run everything below
from `orchestrators-backend/`. For the tests also `pip install pytest`.

## Use from Python

```python
from strategy_interview import (
    GeminiLLM, InterviewEngine, StrategyAdvisor, StrategyQuery, render_brief,
)

llm = GeminiLLM()
engine = InterviewEngine(llm)

turn = engine.start(project_id="chunker", owner_id="lorenz", owner_name="Lorenz")
print(turn.question.text)                     # fixed opening question
turn = engine.answer(turn.session, "I want to evaluate our chunking algorithm.")
# ...repeat until turn.done; store turn.session.model_dump_json() between calls

picture = turn.session.picture                # Kyrill stores this (JSON)
brief = render_brief(picture)                 # goes into every agent's instructions

advisor = StrategyAdvisor(llm)
advice = advisor.ask(picture, StrategyQuery(
    agent_id="impl-1", task="Chunker v2",
    uncertainty="Write tests now or try approach 3 first?",
    options=["Write tests now", "Try approach 3 first"],
))
if advice.status == "needs_owner":            # show advice.owner_question in the chat
    picture, advice = advisor.resolve(picture, advice.pending, owner_answer)
```

Everything is a Pydantic model and round-trips through JSON. The engine and advisor
hold no state: pass the session, picture and any open `PendingQuestion`s back in.

| Call | Returns | Gemini calls |
| --- | --- | --- |
| `engine.start(...)` | `Turn` with the opening question | none |
| `engine.answer(session, text)` | `Turn`: next question or `done`, readiness, changed items | extract, compose, guard |
| `engine.correct(session, key, value)` | `Turn`; value stored as stated, confidence 1.0 | only if a new question is needed |
| `engine.finish(session)` | `Turn` with `done=True` | none |
| `advisor.ask(picture, query, profile, open_pending)` | `Advice`: `answered`, `needs_owner` or `unavailable` | advise (+ guard) |
| `advisor.resolve(picture, pending, answer)` | `(new picture, Advice)`; never escalates again | extract, advise |
| `render_brief(picture)` | Markdown brief for agents | none |

## Tools for the workflow (Sergei, Kyrill)

Each tool takes one Pydantic model as arguments and returns something JSON-serialisable:

```python
# e.g. memory_store/tools.py
from pydantic import BaseModel
from orchestration import tool

class SavePicture(BaseModel):
    project_id: str
    picture: dict

@tool("save_picture", "Store the strategy picture of a project.", SavePicture)
def save_picture(args: SavePicture) -> dict:
    ...
    return {"ok": True}
```

Then add the module path (`"memory_store.tools"`) to `TOOL_MODULES` in
`orchestration/tools.py`. The caller side uses the Gemini Interactions API:

```python
from orchestration import load_tools

box = load_tools()
interaction = client.interactions.create(model=..., input=brief, tools=box.declarations())
# For every step with type == "function_call":
results = [box.call(step) for step in interaction.steps if step.type == "function_call"]
interaction = client.interactions.create(
    model=..., input=results, tools=box.declarations(),
    previous_interaction_id=interaction.id,
)
```

`box.call` validates the arguments and returns a `function_result` step. Errors such as
bad arguments, an unknown tool, or an exception in the tool go back to the model as
`is_error`, so they never crash the workflow.

## Try it in a terminal

```bash
python -m strategy_interview --name Lorenz --project chunker --out picture.json
```

Type `/done` to stop early. The final brief is printed and the picture saved.

## Tests and evaluation

```bash
python -m pytest              # or: python -m unittest discover -s tests -t .
python -m strategy_interview.evals --runs 3 --out eval_report.json   # real Gemini
```

The 65 tests use a scripted fake model and need no network. The evaluation lets
Gemini play three owners (hobby, research, product) and checks the targets from the
spec: median ≤ 5 questions, 0 implementation questions, project type always right,
leans within ±1 at least 80% of the time, at least 80% of agent uncertainties answered
without the owner. It exits with code 1 if a target is missed.

## Configuration

| Variable | Default | Used for |
| --- | --- | --- |
| `GEMINI_API_KEY` | — | Gemini access (`orchestrators-backend/.env`) |
| `STRATEGY_MAIN_MODEL` | `gemini-3.8-flash` | extract, compose, advise |
| `STRATEGY_GUARD_MODEL` | `gemini-3.5-flash-lite` | the question guard |

Thresholds (known 0.7, ready 0.75, max 6 questions, max 2 asks per item) live in
`ScoringConfig` and can be passed to `InterviewEngine(llm, scoring=...)`.

## Layout

```
strategy_interview/
  schema.py      StrategyPicture, Tracked values, sources, item keys, OwnerProfile
  defaults.py    project-type leans, lifespans and policies
  merge.py       value validation and source-rank merge rules
  scoring.py     readiness, gap priority, stop rule
  templates.py   fixed questions (opening + fallbacks)
  prompts.py     all prompts, PROMPT_VERSION
  llm.py         StructuredLLM protocol, GeminiLLM (Interactions API), retries
  llm_io.py      structured outputs Gemini returns
  questions.py   compose + guard + template fallback
  updating.py    one owner answer -> picture updates (shared)
  interview.py   InterviewEngine, InterviewSession, Turn
  advisor.py     StrategyAdvisor (ask_strategy)
  brief.py       render_brief
  evals.py       persona evaluation
orchestration/
  tools.py       Tool, Toolbox, @tool, TOOL_MODULES, load_tools
examples/        research and hobby pictures with their briefs
tests/           offline tests with a scripted fake model
```

## Known limits

- `GeminiLLM` and the tool-call loop above follow the current Gemini docs
  (`client.interactions.create`). They are covered by tests against fakes but were not
  run against the live API here; run the terminal demo once with your key to confirm.
- One owner per project. Team projects with several owners are an open decision.
