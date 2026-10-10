"""Persona evaluation against real Gemini. Needs GEMINI_API_KEY.

    python -m strategy_interview.evals --runs 3 --out eval_report.json

Gemini plays three owners (hobby, research, product). For each run we check:
questions asked, implementation questions slipped through (independent guard
re-check), project type and lean accuracy, and how many scripted agent
uncertainties ask_strategy answers without the owner. Exits 1 if a target is missed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field

from dotenv import load_dotenv

from .advisor import StrategyAdvisor, StrategyQuery
from .interview import InterviewEngine
from .llm import GeminiLLM, LLMError, StructuredLLM
from .llm_io import GuardVerdict, OwnerReply
from .prompts import (
    GUARD_SYSTEM,
    OWNER_SIM_SYSTEM,
    PROMPT_VERSION,
    build_guard_prompt,
    build_owner_sim_prompt,
)
from .schema import Axis, ItemKey, ProjectType
from .scoring import REQUIRED

TARGETS = {
    "median_questions_max": 5,
    "max_questions": 6,
    "implementation_questions": 0,
    "type_accuracy_min": 1.0,
    "lean_accuracy_min": 0.8,
    "answered_without_owner_min": 0.8,
    "owner_questions_strategy_level_min": 1.0,
}


@dataclass(frozen=True)
class Persona:
    name: str
    owner_name: str
    description: str
    expected_type: ProjectType
    expected_leans: dict[Axis, int]
    uncertainties: tuple[StrategyQuery, ...]


PERSONAS = (
    Persona(
        name="hobby",
        owner_name="Mia",
        description=(
            "You are Mia. You want a small gadget that waters your balcony plants while you "
            "are on holiday, built on a Raspberry Pi you already own. It's purely for fun "
            "over a few weekends. Success: the plants survive a two-week trip. You don't "
            "care about polish and enjoy tinkering."
        ),
        expected_type=ProjectType.HOBBY,
        expected_leans={Axis.SPEED_VS_QUALITY: -1, Axis.CAUTION_VS_BOLDNESS: 1},
        uncertainties=(
            StrategyQuery(agent_id="impl", task="Pump control", uncertainty="Add unit tests for the pump timing or move on?", options=["Add unit tests", "Move on"]),
            StrategyQuery(agent_id="impl", task="Sensors", uncertainty="Use a cheap moisture sensor or a calibrated one?"),
            StrategyQuery(agent_id="docs", task="Docs", uncertainty="How much documentation should I write?"),
        ),
    ),
    Persona(
        name="research",
        owner_name="Lorenz",
        description=(
            "You are Lorenz. You want to evaluate whether your team's new document chunking "
            "algorithm beats the current one for retrieval, for a paper. Rigour and "
            "reproducibility matter more than speed; you want several approaches compared "
            "fairly. No hard deadline. The audience is your research group and reviewers."
        ),
        expected_type=ProjectType.RESEARCH,
        expected_leans={Axis.SPEED_VS_QUALITY: 1, Axis.EXPLORE_VS_FOCUS: -2},
        uncertainties=(
            StrategyQuery(agent_id="impl", task="Chunker v2", uncertainty="Write tests for v2 now, or try approach 3 first?", options=["Write tests now", "Try approach 3 first"]),
            StrategyQuery(agent_id="eval", task="Metrics", uncertainty="Report only recall@10, or also MRR and nDCG?"),
            StrategyQuery(agent_id="impl", task="Embeddings", uncertainty="Paid embedding API or a local model?"),
        ),
    ),
    Persona(
        name="product",
        owner_name="Sara",
        description=(
            "You are Sara. You run finance at a 40-person company and want an internal tool "
            "that turns supplier invoices into bookkeeping entries. Accountants will rely on "
            "it daily for years, so errors are costly and it must be reliable and auditable. "
            "It should go live in three months."
        ),
        expected_type=ProjectType.PRODUCT,
        expected_leans={Axis.SPEED_VS_QUALITY: 2, Axis.CAUTION_VS_BOLDNESS: -2},
        uncertainties=(
            StrategyQuery(agent_id="impl", task="Parser", uncertainty="Ship the parser with a known edge case, or fix it first?", options=["Ship now", "Fix first"]),
            StrategyQuery(agent_id="impl", task="Storage", uncertainty="Keep an audit log of every change?"),
            StrategyQuery(agent_id="impl", task="Scope", uncertainty="Also support credit notes in v1?"),
        ),
    ),
)


@dataclass
class RunResult:
    persona: str
    questions: int
    stop_reason: str | None
    implementation_questions: list[str] = field(default_factory=list)
    type_correct: bool = False
    leans_checked: int = 0
    leans_correct: int = 0
    required_filled: bool = False
    advisor_asked: int = 0
    advisor_answered_without_owner: int = 0
    owner_questions: int = 0
    owner_questions_strategy_level: int = 0
    errors: list[str] = field(default_factory=list)


def _strategy_level(llm: StructuredLLM, question: str) -> bool:
    verdict = llm.generate(
        role="main",  # a different model than the production guard: independent check
        system=GUARD_SYSTEM,
        prompt=build_guard_prompt(question),
        schema=GuardVerdict,
        temperature=0.0,
    )
    return verdict.strategy_level


def run_persona(llm: StructuredLLM, persona: Persona) -> RunResult:
    engine = InterviewEngine(llm)
    turn = engine.start(f"eval-{persona.name}", persona.name, persona.owner_name)
    result = RunResult(persona=persona.name, questions=0, stop_reason=None)
    try:
        while not turn.done:
            question = turn.question
            if question.mode != "opening" and not _strategy_level(llm, question.text):
                result.implementation_questions.append(question.text)
            reply = llm.generate(
                role="main",
                system=OWNER_SIM_SYSTEM,
                prompt=build_owner_sim_prompt(persona.description, question.text),
                schema=OwnerReply,
                temperature=0.7,
            )
            turn = engine.answer(turn.session, reply.answer)
    except LLMError as exc:
        result.errors.append(f"interview: {exc}")
        turn = engine.finish(turn.session)

    session = turn.session
    picture = session.picture
    result.questions = session.questions_asked
    result.stop_reason = session.stop_reason
    result.type_correct = picture.project_type.value == persona.expected_type
    for axis, expected in persona.expected_leans.items():
        tracked = picture.get(next(k for k in ItemKey if k.axis == axis))
        result.leans_checked += 1
        if tracked.is_set and abs(int(tracked.value) - expected) <= 1:
            result.leans_correct += 1
    result.required_filled = all(picture.get(k).is_set for k in REQUIRED)

    advisor = StrategyAdvisor(llm)
    for query in persona.uncertainties:
        result.advisor_asked += 1
        advice = advisor.ask(picture, query)
        if advice.status == "answered":
            result.advisor_answered_without_owner += 1
        elif advice.status == "needs_owner" and advice.owner_question:
            result.owner_questions += 1
            try:
                if _strategy_level(llm, advice.owner_question.text):
                    result.owner_questions_strategy_level += 1
            except LLMError as exc:
                result.errors.append(f"guard check: {exc}")
        else:
            result.errors.append(f"advisor unavailable for: {query.uncertainty}")
    return result


def summarize(results: list[RunResult]) -> dict:
    questions = [r.questions for r in results]
    leans_checked = sum(r.leans_checked for r in results) or 1
    asked = sum(r.advisor_asked for r in results) or 1
    owner_qs = sum(r.owner_questions for r in results)
    summary = {
        "prompt_version": PROMPT_VERSION,
        "runs": len(results),
        "median_questions": statistics.median(questions) if questions else None,
        "max_questions": max(questions) if questions else None,
        "implementation_questions": sum(len(r.implementation_questions) for r in results),
        "type_accuracy": sum(r.type_correct for r in results) / max(len(results), 1),
        "lean_accuracy": sum(r.leans_correct for r in results) / leans_checked,
        "answered_without_owner": sum(r.advisor_answered_without_owner for r in results) / asked,
        "owner_questions_strategy_level": (
            sum(r.owner_questions_strategy_level for r in results) / owner_qs if owner_qs else 1.0
        ),
    }
    summary["passed"] = (
        summary["median_questions"] is not None
        and summary["median_questions"] <= TARGETS["median_questions_max"]
        and summary["max_questions"] <= TARGETS["max_questions"]
        and summary["implementation_questions"] <= TARGETS["implementation_questions"]
        and summary["type_accuracy"] >= TARGETS["type_accuracy_min"]
        and summary["lean_accuracy"] >= TARGETS["lean_accuracy_min"]
        and summary["answered_without_owner"] >= TARGETS["answered_without_owner_min"]
        and summary["owner_questions_strategy_level"]
        >= TARGETS["owner_questions_strategy_level_min"]
    )
    return summary


def main(argv: list[str] | None = None, llm: StructuredLLM | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3, help="runs per persona")
    parser.add_argument("--out", default="eval_report.json")
    args = parser.parse_args(argv)

    if llm is None:
        load_dotenv()  # GEMINI_API_KEY from orchestrators-backend/.env, as in main.py
        llm = GeminiLLM()
    results = [run_persona(llm, p) for p in PERSONAS for _ in range(args.runs)]
    summary = summarize(results)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "targets": TARGETS, "runs": [asdict(r) for r in results]}, fh, indent=2, default=str)

    for r in results:
        print(
            f"{r.persona:9} questions={r.questions} stop={r.stop_reason} "
            f"type_ok={r.type_correct} leans={r.leans_correct}/{r.leans_checked} "
            f"answered={r.advisor_answered_without_owner}/{r.advisor_asked} "
            f"impl_qs={len(r.implementation_questions)}"
        )
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
