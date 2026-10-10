import unittest

from strategy_interview import (
    ItemKey,
    LLMError,
    ProjectType,
    Source,
    StrategyAdvisor,
    StrategyPicture,
    StrategyQuery,
)
from strategy_interview.defaults import apply_type_defaults
from strategy_interview.llm_io import AdviceDraft, ExtractionResult, GuardVerdict
from strategy_interview.merge import Proposal, apply

from .fakes import FakeLLM, extraction, item


def research_picture() -> StrategyPicture:
    p = StrategyPicture(project_id="chunker", owner_id="lorenz")
    for key, value in [
        (ItemKey.GOAL, "Evaluate our chunking algorithm"),
        (ItemKey.PROJECT_TYPE, "research"),
        (ItemKey.SUCCESS_CRITERIA, ["beats the baseline on recall"]),
    ]:
        apply(p, Proposal(key, value, Source.CONFIRMED, 0.95, "test"), turn=1)
    apply_type_defaults(p)
    return p


QUERY = StrategyQuery(
    agent_id="impl-1",
    task="Chunker v2",
    uncertainty="Write tests for v2 now, or try approach 3 first?",
    options=["Write tests now", "Try approach 3 first"],
)

COST_QUERY = StrategyQuery(
    agent_id="impl-2",
    task="Embeddings for evaluation",
    uncertainty="Paid embedding API or a local model?",
)


def settled(option="try approach 3 FIRST", **extra):
    return AdviceDraft(
        settled=True,
        guidance="Try approach 3 first; only test the evaluation code.",
        chosen_option=option,
        rationale="Research explores 3 approaches before committing.",
        cited=[ItemKey.PROJECT_TYPE, ItemKey.EXPLORE_VS_FOCUS],
        escalate=False,
        **extra,
    )


def escalating(question="Is keeping costs at zero a hard limit for this research?"):
    return AdviceDraft(
        settled=False,
        guidance="Start with a local model; it is reversible.",
        rationale="Budget is not in the brief and changes the constraints.",
        cited=[ItemKey.CONSTRAINTS],
        escalate=True,
        gap_item="constraints",
        owner_question=question,
    )


class AskTests(unittest.TestCase):
    def test_answered_from_the_strategy(self):
        llm = FakeLLM().queue(AdviceDraft, settled())
        advice = StrategyAdvisor(llm).ask(research_picture(), QUERY)
        self.assertEqual(advice.status, "answered")
        self.assertEqual(advice.chosen_option, "Try approach 3 first")  # matched exactly
        self.assertEqual(advice.confidence, "high")
        self.assertIsNone(advice.owner_question)
        prompt = llm.calls_for(AdviceDraft)[0].prompt
        self.assertIn("Working policies (research)", prompt)
        self.assertIn("<agent>", prompt)

    def test_unknown_option_is_dropped(self):
        llm = FakeLLM().queue(AdviceDraft, settled(option="Rewrite everything"))
        advice = StrategyAdvisor(llm).ask(research_picture(), QUERY)
        self.assertIsNone(advice.chosen_option)

    def test_escalation_asks_a_vetted_strategy_question(self):
        llm = FakeLLM().queue(AdviceDraft, escalating())
        advice = StrategyAdvisor(llm).ask(research_picture(), COST_QUERY)
        self.assertEqual(advice.status, "needs_owner")
        self.assertEqual(advice.owner_question.origin, "model")
        self.assertEqual(advice.owner_question.target, ItemKey.CONSTRAINTS)
        self.assertEqual(advice.pending.query, COST_QUERY)
        self.assertEqual(advice.guidance, "Start with a local model; it is reversible.")
        self.assertEqual(len(llm.calls_for(GuardVerdict)), 1)

    def test_technical_owner_question_is_replaced_by_a_template(self):
        llm = FakeLLM().queue(AdviceDraft, escalating("OpenAI embeddings or bge-small?"))
        llm.default(GuardVerdict, GuardVerdict(strategy_level=False, reason="technical"))
        advice = StrategyAdvisor(llm).ask(research_picture(), COST_QUERY)
        self.assertEqual(advice.owner_question.origin, "template")
        self.assertIn("hard limits", advice.owner_question.text)

    def test_open_question_on_the_same_item_is_reused(self):
        llm = FakeLLM().queue(AdviceDraft, escalating(), escalating())
        advisor = StrategyAdvisor(llm)
        first = advisor.ask(research_picture(), COST_QUERY)
        guard_calls = len(llm.calls_for(GuardVerdict))
        second = advisor.ask(research_picture(), COST_QUERY, open_pending=[first.pending])
        self.assertEqual(second.pending.pending_id, first.pending.pending_id)
        self.assertEqual(len(llm.calls_for(GuardVerdict)), guard_calls)

    def test_outage_is_reported_and_never_reaches_the_owner(self):
        llm = FakeLLM().queue(AdviceDraft, LLMError("down"))
        advice = StrategyAdvisor(llm).ask(research_picture(), QUERY)
        self.assertEqual(advice.status, "unavailable")
        self.assertIsNone(advice.owner_question)


class ResolveTests(unittest.TestCase):
    def test_owner_answer_improves_the_picture_and_answers_the_query(self):
        llm = FakeLLM().queue(
            AdviceDraft,
            escalating(),
            AdviceDraft(
                settled=True,
                guidance="Use a local model.",
                rationale="Zero budget is a hard limit.",
                cited=[ItemKey.CONSTRAINTS],
                escalate=False,
            ),
        )
        llm.queue(ExtractionResult, extraction(item("constraints", "No paid services")))
        advisor = StrategyAdvisor(llm)
        picture = research_picture()
        asked = advisor.ask(picture, COST_QUERY)

        new_picture, advice = advisor.resolve(picture, asked.pending, "Yes, zero budget")
        self.assertEqual(new_picture.constraints.value, ["No paid services"])
        self.assertFalse(picture.constraints.is_set)  # input not mutated
        self.assertEqual((advice.status, advice.confidence), ("answered", "high"))
        self.assertIn("NOT allowed", llm.calls_for(AdviceDraft)[-1].prompt)

    def test_never_escalates_twice(self):
        llm = FakeLLM().queue(AdviceDraft, escalating(), escalating())
        llm.queue(ExtractionResult, extraction())
        advisor = StrategyAdvisor(llm)
        asked = advisor.ask(research_picture(), COST_QUERY)
        _, advice = advisor.resolve(research_picture(), asked.pending, "not sure")
        self.assertEqual((advice.status, advice.confidence), ("answered", "low"))
        self.assertIsNone(advice.owner_question)

    def test_type_stays_confirmed(self):
        llm = FakeLLM().queue(AdviceDraft, escalating(), settled())
        llm.queue(
            ExtractionResult,
            extraction(item("project_type", "hobby", source="inferred", confidence=0.6)),
        )
        advisor = StrategyAdvisor(llm)
        asked = advisor.ask(research_picture(), COST_QUERY)
        new_picture, _ = advisor.resolve(research_picture(), asked.pending, "it's just for fun")
        self.assertIs(new_picture.project_type.value, ProjectType.RESEARCH)


if __name__ == "__main__":
    unittest.main()
