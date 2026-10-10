# strategy-interview: notes for Claude Code

Owner: Lorenz. This folder is the "Objective interview & strategy picture" workstream of
the Agentic Whiteboard (hackathon team: Lorenz, Vanya, Sergei, Kyrill).

## What this module is

A short interview fills one structured strategy picture (goal, project type, success
criteria, trade-off leans, each with source + confidence). Agents read it as a brief
(`render_brief`) and decide implementation details themselves; `StrategyAdvisor.ask`
(ask_strategy) answers their uncertainties from the picture and only asks the owner a
strategy-level question when it must.

Mission: the owner only ever talks about intent, never about a detail an agent who
understood the strategy could have decided. Any change that makes the owner answer
implementation questions is a regression.

Spec (source of truth for behaviour):
https://claude.ai/code/artifact/74471702-9e90-4e03-82d4-ef9553a13e24

## Rules of the design (keep them)

- Gemini only reads answers and phrases questions. What to ask next and when to stop is
  plain code (`scoring.py`, `merge.py`). Do not move those decisions into prompts.
- Every Gemini call has a deterministic fallback (templates, `status: unavailable`).
- Source ranks: stated = confirmed > inferred > memory > default. Lower never overwrites higher.
- `project_type` counts as known only when stated or confirmed by the owner.
- Engine and advisor are stateless: they take a session/picture and return a new one;
  never mutate inputs.
- Prompts live in `prompts.py`; bump `PROMPT_VERSION` on any prompt change.
- `llm_io.py` schemas must stay inside Gemini's JSON-schema subset (no nullable enums,
  no recursion); `tests/test_llm_and_brief.py::SchemaTests` enforces it.

## Out of scope here (teammates)

Chat UI and strategy card (Sergei), storing pictures and owner profiles (Kyrill),
running agents / the task graph. This module only exposes `Turn`, `StrategyPicture`,
`render_brief` and `StrategyAdvisor`.

## Commands

```bash
pip install -e ".[dev]"
python -m pytest                                   # 60 offline tests, fake model
python -m strategy_interview --name Lorenz         # live interview, needs GEMINI_API_KEY
python -m strategy_interview.evals --runs 3        # persona eval against real Gemini
python examples/build_examples.py                  # regenerate example pictures + briefs
```

## Open items

- `GeminiLLM` (Interactions API) is tested against a fake client only; confirm once
  against the live API with the terminal demo.
- Thresholds in `ScoringConfig` are first guesses; tune after the first eval run.
- Decide whether a project may change type mid-way, and one owner vs. team projects.
