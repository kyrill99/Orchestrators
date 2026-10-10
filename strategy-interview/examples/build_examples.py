"""Build the two example pictures (hobby and research) and their agent briefs.

Run from the repository root:  python examples/build_examples.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy_interview import ItemKey, Source, StrategyPicture, render_brief  # noqa: E402
from strategy_interview.defaults import apply_type_defaults  # noqa: E402
from strategy_interview.merge import Proposal, apply  # noqa: E402
from strategy_interview.scoring import readiness  # noqa: E402

HERE = Path(__file__).resolve().parent


def build(project_id: str, owner_id: str, facts: list[tuple]) -> StrategyPicture:
    picture = StrategyPicture(project_id=project_id, owner_id=owner_id)
    for turn, (key, value, source, confidence, evidence) in enumerate(facts, start=1):
        apply(picture, Proposal(key, value, source, confidence, evidence), turn)
        if key is ItemKey.PROJECT_TYPE:
            apply_type_defaults(picture, turn=turn)
    picture.open_gaps = readiness(picture).unknown
    return picture


RESEARCH = build(
    "chunker-eval",
    "lorenz",
    [
        (ItemKey.GOAL, "Find out whether our chunking algorithm beats the current one",
         Source.STATED, 0.95, "I want to evaluate our chunking algorithm"),
        (ItemKey.PROJECT_TYPE, "research", Source.CONFIRMED, 0.95, "Yes, it's research"),
        (ItemKey.SUCCESS_CRITERIA,
         ["Beats the baseline on retrieval recall", "Results are reproducible"],
         Source.STATED, 0.9, "success is beating the baseline on recall"),
        (ItemKey.SPEED_VS_QUALITY, "2", Source.STATED, 0.9, "it has to be rigorous"),
        (ItemKey.CONSTRAINTS, [], Source.STATED, 0.9, "no deadline"),
        (ItemKey.LIFESPAN, "months", Source.CONFIRMED, 0.95, "Yes, a few months"),
    ],
)

HOBBY = build(
    "plant-watering",
    "lorenz",
    [
        (ItemKey.GOAL, "Water my balcony plants automatically while I'm away",
         Source.STATED, 0.95, "water my plants when I'm on holiday"),
        (ItemKey.PROJECT_TYPE, "hobby", Source.CONFIRMED, 0.95, "Yep, just for fun"),
        (ItemKey.SUCCESS_CRITERIA, ["Plants survive a two-week holiday"],
         Source.STATED, 0.9, "they should survive two weeks"),
        (ItemKey.CONSTRAINTS, ["Runs on the Raspberry Pi I already own"],
         Source.STATED, 0.9, "I have a Pi lying around"),
        (ItemKey.AUDIENCE, "Just me", Source.INFERRED, 0.8, "my plants"),
        (ItemKey.LIFESPAN, "months", Source.CONFIRMED, 0.95, "a few months is fine"),
    ],
)

if __name__ == "__main__":
    for name, picture in [("research", RESEARCH), ("hobby", HOBBY)]:
        (HERE / f"{name}_picture.json").write_text(
            json.dumps(picture.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
        )
        (HERE / f"{name}_brief.md").write_text(render_brief(picture))
        state = readiness(picture)
        print(f"{name}: readiness {state.score}, ready={state.ready}, open gaps={len(picture.open_gaps)}")
