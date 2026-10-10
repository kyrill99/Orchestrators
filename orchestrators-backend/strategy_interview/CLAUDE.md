# strategy_interview + orchestration: notes for Claude Code

Owner: Lorenz. Hackathon team: Lorenz, Vanya, Sergei, Kyrill. This file covers Lorenz's
part of `orchestrators-backend/`: `strategy_interview/` (this folder) and `orchestration/`.
The rest of the backend (`main.py` FastAPI app, `requirements.txt`, `.env.example`) and
`orchestrators-frontend/` belong to teammates. When their work overlaps ours, follow
their approach and only change what is clearly in this scope.

## How the parts fit

- `strategy_interview/`: a short interview fills one structured strategy picture (goal,
  project type, success criteria, trade-off leans, each with source + confidence). Agents
  read it as a brief (`render_brief`); `StrategyAdvisor.ask` (ask_strategy) answers their
  uncertainties from the picture and only asks the owner a strategy question when it must.
- `orchestration/`: the tool contract. Sergei and Kyrill expose their work as tools
  (`@tool` + a Pydantic args model, module listed in `TOOL_MODULES`); Lorenz's workflow
  generator calls them through Gemini function calling (`toolbox.declarations()` /
  `toolbox.call(step)`). The workflow generator itself is not written yet.

Mission: the owner only ever talks about intent, never about a detail an agent who
understood the strategy could have decided. Any change that makes the owner answer
implementation questions is a regression.

Spec (source of truth for interview behaviour):
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
- `llm_io.py` schemas and tool args must stay inside Gemini's JSON-schema subset (no
  nullable enums, no recursion); `tests/test_llm_and_brief.py::SchemaTests` enforces it.
- Entry points load `orchestrators-backend/.env` with `load_dotenv()` like `main.py`;
  library code only reads `os.environ`.

## Out of scope here (teammates)

- `orchestrators-frontend/` and the chat UI / strategy card (Sergei). Do not edit the
  frontend from this workstream.
- Storing pictures and owner profiles (Kyrill).
- The FastAPI app in `main.py`, running agents, the task graph runtime.

## Commands (from `orchestrators-backend/`)

```bash
pip install -r requirements.txt pytest
python -m pytest                                   # 65 offline tests, fake model
python -m strategy_interview --name Lorenz         # live interview, needs GEMINI_API_KEY
python -m strategy_interview.evals --runs 3        # persona eval against real Gemini
python examples/build_examples.py                  # regenerate example pictures + briefs
```

## Open items

- `GeminiLLM` (Interactions API) is tested against a fake client only; confirm once
  against the live API with the terminal demo.
- Thresholds in `ScoringConfig` are first guesses; tune after the first eval run.
- Decide whether a project may change type mid-way, and one owner vs. team projects.
- Workflow generator: strategy picture + registered tools -> task graph (the frontend's
  `Task` / `TaskEdge` shape in `orchestrators-frontend/lib/fixtures.ts`).
