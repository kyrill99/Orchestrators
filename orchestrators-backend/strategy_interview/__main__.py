"""Try the interview in a terminal against real Gemini (needs GEMINI_API_KEY).

    python -m strategy_interview --name Lorenz --project chunker --out picture.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from .brief import render_brief
from .interview import InterviewEngine
from .llm import GeminiLLM


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the objective interview in a terminal.")
    parser.add_argument("--name", default="there", help="owner's first name")
    parser.add_argument("--project", default="demo", help="project id")
    parser.add_argument("--owner", default="owner", help="owner id")
    parser.add_argument("--out", help="write the final picture as JSON to this file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    load_dotenv()  # GEMINI_API_KEY from orchestrators-backend/.env, as in main.py
    engine = InterviewEngine(GeminiLLM())
    turn = engine.start(args.project, args.owner, args.name)
    try:
        while not turn.done:
            print(f"\n{turn.question.text}")
            answer = input("> ")
            if answer.strip().lower() in {"/done", "/stop"}:
                turn = engine.finish(turn.session)
                break
            turn = engine.answer(turn.session, answer)
            if turn.changed:
                print(
                    f"  [updated: {', '.join(k.value for k in turn.changed)}; "
                    f"readiness {turn.readiness.score:.2f}]"
                )
    except (KeyboardInterrupt, EOFError):
        turn = engine.finish(turn.session)

    print(f"\nDone ({turn.session.stop_reason}) after {turn.session.questions_asked} questions.\n")
    print(render_brief(turn.session.picture))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(turn.session.picture.model_dump(mode="json"), fh, indent=2)
        print(f"Picture written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
