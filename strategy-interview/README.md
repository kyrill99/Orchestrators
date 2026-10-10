# strategy-interview

The objective interview and strategy picture for the Agentic Whiteboard. A short
interview fills one structured **strategy picture** (goal, project type, success
criteria, trade-offs, each with source and confidence). Agents read it as a brief
and decide implementation details themselves; when they are unsure they call
`ask_strategy`, which answers from the picture and only asks the owner a strategy
question when it has to.

The full definition (schema, defaults, loop, stop rule, contracts) is in the spec doc
"Objective Interview & Strategy Picture — Spec".

## Install

```bash
pip install -e ".[dev]"          # Python 3.10+
export GEMINI_API_KEY=...         # from Google AI Studio
```

## Use

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

The 60 tests use a scripted fake model and need no network. The evaluation lets
Gemini play three owners (hobby, research, product) and checks the targets from the
spec: median ≤ 5 questions, 0 implementation questions, project type always right,
leans within ±1 at least 80% of the time, at least 80% of agent uncertainties answered
without the owner. It exits with code 1 if a target is missed.

## Configuration

| Variable | Default | Used for |
| --- | --- | --- |
| `GEMINI_API_KEY` | — | Gemini access |
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
examples/        research and hobby pictures with their briefs
tests/           offline tests with a scripted fake model
```

## Known limits

- `GeminiLLM` follows the current Gemini docs (`client.interactions.create` with
  `response_format`, `system_instruction`, `generation_config`, `store=False`). It is
  covered by tests against a fake client but was not run against the live API here;
  run the terminal demo once with your key to confirm.
- One owner per project. Team projects with several owners are an open decision.
