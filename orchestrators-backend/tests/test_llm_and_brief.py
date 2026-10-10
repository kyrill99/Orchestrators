import json
import unittest
from types import SimpleNamespace

from strategy_interview import GeminiLLM, LLMError, ModelConfig, render_brief
from strategy_interview.llm import gemini_schema
from strategy_interview.llm_io import (
    AdviceDraft,
    ExtractionResult,
    GuardVerdict,
    OwnerReply,
    QuestionDraft,
)
from strategy_interview.schema import ItemKey, Source

from .test_advisor import research_picture

IO_MODELS = [ExtractionResult, QuestionDraft, GuardVerdict, AdviceDraft, OwnerReply]


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


class SchemaTests(unittest.TestCase):
    def test_schemas_fit_geminis_subset(self):
        for model in IO_MODELS:
            schema = gemini_schema(model)
            for node in walk(schema):
                for banned in ("$ref", "$defs", "anyOf", "title", "default"):
                    self.assertNotIn(banned, node, f"{model.__name__}: {banned}")
                if "enum" in node:  # no nullable enums
                    self.assertEqual(node.get("type"), "string", model.__name__)

    def test_nullable_strings_become_type_arrays(self):
        schema = gemini_schema(AdviceDraft)
        self.assertEqual(schema["properties"]["chosen_option"]["type"], ["string", "null"])
        self.assertIn("none", schema["properties"]["gap_item"]["enum"])


class FakeInteractions:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return SimpleNamespace(output_text=result)


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def gemini(*results):
    interactions = FakeInteractions(*results)
    sleeps = []
    llm = GeminiLLM(
        ModelConfig(main_model="main-m", guard_model="guard-m"),
        client=SimpleNamespace(interactions=interactions),
        sleep=sleeps.append,
    )
    return llm, interactions, sleeps


OK = json.dumps({"strategy_level": True, "reason": "fine"})


def call(llm):
    return llm.generate(
        role="guard", system="sys", prompt="p", schema=GuardVerdict, temperature=0.0
    )


class GeminiTests(unittest.TestCase):
    def test_request_shape(self):
        llm, interactions, _ = gemini(OK)
        self.assertTrue(call(llm).strategy_level)
        sent = interactions.calls[0]
        self.assertEqual(sent["model"], "guard-m")
        self.assertEqual(sent["system_instruction"], "sys")
        self.assertEqual(sent["generation_config"], {"temperature": 0.0})
        self.assertEqual(sent["response_format"]["mime_type"], "application/json")
        self.assertIs(sent["store"], False)

    def test_transient_errors_are_retried_with_backoff(self):
        llm, interactions, sleeps = gemini(ApiError(503), "not json", OK)
        self.assertTrue(call(llm).strategy_level)
        self.assertEqual(len(interactions.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_client_errors_fail_fast(self):
        llm, interactions, _ = gemini(ApiError(401), OK)
        with self.assertRaises(LLMError):
            call(llm)
        self.assertEqual(len(interactions.calls), 1)

    def test_gives_up_after_max_attempts(self):
        llm, interactions, _ = gemini(ApiError(500), ApiError(500), ApiError(500))
        with self.assertRaises(LLMError):
            call(llm)
        self.assertEqual(len(interactions.calls), 3)

    def test_missing_api_key_fails_fast(self):
        from unittest import mock

        sleeps = []
        llm = GeminiLLM(ModelConfig(), sleep=sleeps.append)
        with mock.patch("google.genai.Client", side_effect=ValueError("No API key")):
            with self.assertRaisesRegex(LLMError, "GEMINI_API_KEY"):
                call(llm)
        self.assertEqual(sleeps, [])

    def test_models_come_from_env(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"STRATEGY_MAIN_MODEL": "m2"}):
            cfg = ModelConfig.from_env()
        self.assertEqual(cfg.model_for("main"), "m2")
        self.assertEqual(cfg.model_for("guard"), "gemini-3.5-flash-lite")


class BriefTests(unittest.TestCase):
    def test_brief_carries_intent_leans_policies_and_provenance(self):
        picture = research_picture()
        picture.open_gaps = [ItemKey.AUDIENCE]
        brief = render_brief(picture)
        self.assertIn("**Goal:** Evaluate our chunking algorithm (owner confirmed)", brief)
        self.assertIn("**Project type:** research (owner confirmed)", brief)
        self.assertIn("Strongly favouring trying several approaches", brief)
        self.assertIn("default for research, soft", brief)
        self.assertIn("## Working policies (research)", brief)
        self.assertIn("## Still unknown\n- audience", brief)
        self.assertIn("ask_strategy", brief)

    def test_brief_for_an_empty_picture(self):
        from strategy_interview import StrategyPicture

        brief = render_brief(StrategyPicture(project_id="p", owner_id="o"))
        self.assertIn("**Goal:** unknown", brief)
        self.assertNotIn("Working policies", brief)

    def test_stated_empty_list_reads_as_none(self):
        from strategy_interview.merge import Proposal, apply

        picture = research_picture()
        apply(picture, Proposal(ItemKey.CONSTRAINTS, [], Source.STATED, 0.9), turn=2)
        self.assertIn("**Hard constraints:** none (owner)", render_brief(picture))


if __name__ == "__main__":
    unittest.main()


class EvalHarnessTests(unittest.TestCase):
    def test_harness_runs_end_to_end_offline(self):
        import os
        import tempfile

        from strategy_interview import evals
        from strategy_interview.llm_io import AdviceDraft, ExtractionResult, OwnerReply

        from .fakes import FakeLLM, extraction, item

        llm = FakeLLM()
        llm.default(OwnerReply, OwnerReply(answer="It's research, rigour first."))
        llm.default(
            ExtractionResult,
            extraction(
                item("goal", "Evaluate"),
                item("project_type", "research"),
                item("success_criteria", "beats baseline"),
                wants_to_stop=True,
            ),
        )
        llm.default(
            AdviceDraft,
            AdviceDraft(settled=True, guidance="g", rationale="r", escalate=False),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            code = evals.main(["--runs", "1", "--out", out], llm=llm)
            with open(out) as fh:
                report = json.load(fh)
        self.assertIn(code, (0, 1))
        self.assertEqual(report["summary"]["runs"], 3)
        self.assertEqual(report["summary"]["answered_without_owner"], 1.0)
