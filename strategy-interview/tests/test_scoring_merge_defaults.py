import unittest

from strategy_interview.defaults import apply_profile, apply_type_defaults
from strategy_interview.merge import InvalidValue, Proposal, apply, normalize_value
from strategy_interview.schema import (
    Axis,
    ItemKey,
    Lifespan,
    OwnerProfile,
    ProjectType,
    Source,
    StrategyPicture,
)
from strategy_interview.scoring import ScoringConfig, pick_gap, readiness


def picture() -> StrategyPicture:
    return StrategyPicture(project_id="p1", owner_id="lorenz")


def put(p: StrategyPicture, key: ItemKey, value, source=Source.STATED, conf=0.95):
    return apply(p, Proposal(key, value, source, conf, "test"), turn=1)


class NormalizeTests(unittest.TestCase):
    def test_list_is_cleaned_deduplicated_and_capped(self):
        raw = ["  a  ", "A", "b"] + [f"x{i}" for i in range(20)]
        out = normalize_value(ItemKey.CONSTRAINTS, raw)
        self.assertEqual(out[:2], ["a", "b"])
        self.assertEqual(len(out), 8)

    def test_enum_values(self):
        self.assertIs(normalize_value(ItemKey.PROJECT_TYPE, ["Research"]), ProjectType.RESEARCH)
        self.assertIs(normalize_value(ItemKey.LIFESPAN, "years"), Lifespan.YEARS)
        with self.assertRaises(InvalidValue):
            normalize_value(ItemKey.PROJECT_TYPE, "startup")

    def test_leans_are_clamped(self):
        self.assertEqual(normalize_value(ItemKey.SPEED_VS_QUALITY, ["5"]), 2)
        self.assertEqual(normalize_value(ItemKey.SPEED_VS_QUALITY, "-3"), -2)
        self.assertEqual(normalize_value(ItemKey.SPEED_VS_QUALITY, "1.4"), 1)
        with self.assertRaises(InvalidValue):
            normalize_value(ItemKey.SPEED_VS_QUALITY, "quality")

    def test_goal_is_truncated_and_required(self):
        self.assertEqual(len(normalize_value(ItemKey.GOAL, "x" * 500)), 300)
        with self.assertRaises(InvalidValue):
            normalize_value(ItemKey.GOAL, [])


class MergeTests(unittest.TestCase):
    def test_lower_rank_never_overwrites_higher(self):
        p = picture()
        put(p, ItemKey.AUDIENCE, "my research group", Source.STATED)
        changed = put(p, ItemKey.AUDIENCE, "the public", Source.INFERRED, 0.8)
        self.assertFalse(changed)
        self.assertEqual(p.audience.value, "my research group")

    def test_higher_rank_and_equal_rank_newer_win(self):
        p = picture()
        put(p, ItemKey.AUDIENCE, "the public", Source.INFERRED, 0.6)
        self.assertTrue(put(p, ItemKey.AUDIENCE, "my group", Source.STATED))
        self.assertTrue(put(p, ItemKey.AUDIENCE, "my lab", Source.CONFIRMED))
        self.assertEqual(p.audience.value, "my lab")

    def test_confidence_is_bounded_by_source(self):
        p = picture()
        put(p, ItemKey.AUDIENCE, "a", Source.STATED, 0.3)
        self.assertEqual(p.audience.confidence, 0.9)
        put(p, ItemKey.GOAL, "g", Source.INFERRED, 0.99)
        self.assertEqual(p.goal.confidence, 0.85)

    def test_revision_counts_only_real_changes(self):
        p = picture()
        put(p, ItemKey.AUDIENCE, "a")
        put(p, ItemKey.AUDIENCE, "a")
        self.assertEqual(p.revision, 1)

    def test_stated_empty_list_is_set(self):
        p = picture()
        put(p, ItemKey.CONSTRAINTS, [])
        self.assertTrue(p.constraints.is_set)
        self.assertIn(ItemKey.CONSTRAINTS, readiness(p).known)

    def test_values_are_typed_after_merge(self):
        p = picture()
        put(p, ItemKey.PROJECT_TYPE, "research")
        self.assertIs(p.project_type.value, ProjectType.RESEARCH)
        reloaded = StrategyPicture.model_validate_json(p.model_dump_json())
        self.assertIs(reloaded.project_type.value, ProjectType.RESEARCH)


class DefaultsTests(unittest.TestCase):
    def test_type_sets_defaults(self):
        p = picture()
        put(p, ItemKey.PROJECT_TYPE, "research")
        changed = apply_type_defaults(p)
        self.assertEqual(len(changed), 5)
        self.assertEqual(p.tradeoffs.explore_vs_focus.value, -2)
        self.assertIs(p.tradeoffs.explore_vs_focus.source, Source.DEFAULT)
        self.assertIs(p.lifespan.value, Lifespan.MONTHS)
        self.assertEqual(p.policies.approaches_before_committing, 3)

    def test_learned_leans_beat_defaults(self):
        p = picture()
        put(p, ItemKey.PROJECT_TYPE, "research")
        profile = OwnerProfile(
            owner_id="lorenz", learned_leans={ProjectType.RESEARCH: {Axis.SPEED_VS_QUALITY: 2}}
        )
        apply_type_defaults(p, profile)
        self.assertEqual(p.tradeoffs.speed_vs_quality.value, 2)
        self.assertIs(p.tradeoffs.speed_vs_quality.source, Source.MEMORY)

    def test_type_change_rederives_defaults_but_keeps_owner_values(self):
        p = picture()
        put(p, ItemKey.PROJECT_TYPE, "research")
        apply_type_defaults(p)
        put(p, ItemKey.SPEED_VS_QUALITY, "-1")
        put(p, ItemKey.PROJECT_TYPE, "product")
        apply_type_defaults(p)
        self.assertEqual(p.tradeoffs.speed_vs_quality.value, -1)  # owner value kept
        self.assertEqual(p.tradeoffs.caution_vs_boldness.value, -2)  # product default
        self.assertIs(p.lifespan.value, Lifespan.YEARS)

    def test_dropped_type_clears_its_defaults(self):
        p = picture()
        put(p, ItemKey.PROJECT_TYPE, "research", Source.INFERRED, 0.7)
        apply_type_defaults(p)
        p.project_type = type(p.project_type)()
        apply_type_defaults(p)
        self.assertFalse(p.lifespan.is_set)
        self.assertFalse(p.tradeoffs.explore_vs_focus.is_set)

    def test_profile_prefills_constraints(self):
        p = picture()
        apply_profile(p, OwnerProfile(owner_id="lorenz", standing_constraints=["Use Python"]))
        self.assertEqual(p.constraints.value, ["Use Python"])
        self.assertIs(p.constraints.source, Source.MEMORY)

    def test_policies_are_in_the_json(self):
        p = picture()
        self.assertIsNone(p.model_dump()["policies"])
        put(p, ItemKey.PROJECT_TYPE, "prototype")
        self.assertEqual(p.model_dump()["policies"]["documentation"], "A short demo script")


class ScoringTests(unittest.TestCase):
    def test_empty_picture(self):
        state = readiness(picture())
        self.assertEqual(state.score, 0)
        self.assertFalse(state.ready)
        self.assertEqual(
            set(state.required_missing),
            {ItemKey.GOAL, ItemKey.PROJECT_TYPE, ItemKey.SUCCESS_CRITERIA},
        )
        gap = pick_gap(picture())
        self.assertEqual((gap.key, gap.mode), (ItemKey.GOAL, "open"))

    def test_inferred_type_is_not_known_and_gets_confirmed(self):
        p = picture()
        put(p, ItemKey.GOAL, "Evaluate the chunker")
        put(p, ItemKey.SUCCESS_CRITERIA, ["beats baseline recall"])
        put(p, ItemKey.PROJECT_TYPE, "research", Source.INFERRED, 0.85)
        apply_type_defaults(p)  # as extract_and_merge does after a type change
        self.assertIn(ItemKey.PROJECT_TYPE, readiness(p).required_missing)
        gap = pick_gap(p)
        self.assertEqual((gap.key, gap.mode), (ItemKey.PROJECT_TYPE, "confirm"))

    def test_items_asked_twice_are_skipped(self):
        gap = pick_gap(picture(), {ItemKey.GOAL: 2})
        self.assertEqual(gap.key, ItemKey.PROJECT_TYPE)

    def test_ready_needs_required_items_and_score(self):
        p = picture()
        put(p, ItemKey.GOAL, "g")
        put(p, ItemKey.PROJECT_TYPE, "research")
        put(p, ItemKey.SUCCESS_CRITERIA, ["s"])
        apply_type_defaults(p)
        self.assertFalse(readiness(p).ready)  # required known, score too low
        put(p, ItemKey.LIFESPAN, "months")
        put(p, ItemKey.SPEED_VS_QUALITY, "1")
        put(p, ItemKey.AUDIENCE, "lab")
        state = readiness(p)
        self.assertTrue(state.ready, state)

    def test_thresholds_are_configurable(self):
        p = picture()
        put(p, ItemKey.GOAL, "g")
        put(p, ItemKey.PROJECT_TYPE, "research")
        put(p, ItemKey.SUCCESS_CRITERIA, ["s"])
        self.assertTrue(readiness(p, ScoringConfig(ready_threshold=0.4)).ready)


if __name__ == "__main__":
    unittest.main()
