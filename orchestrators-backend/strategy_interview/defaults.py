"""Project-type defaults: trade-off leans, lifespan and working policies.

Once the project type is known, every type-derived item the owner has not
spoken to falls back to these values, so a confirmed type alone already gives
agents a usable opinion.
"""

from __future__ import annotations

from .schema import (
    AXIS_ITEMS,
    Axis,
    ItemKey,
    Lifespan,
    OwnerProfile,
    Policies,
    ProjectType,
    TYPE_DERIVED_ITEMS,
    Source,
    StrategyPicture,
    Tracked,
)

DEFAULT_CONFIDENCE = 0.5
MEMORY_CONFIDENCE = 0.7

# -2 = strongly the left pole, +2 = strongly the right pole (see schema.AXIS_POLES).
DEFAULT_LEANS: dict[ProjectType, dict[Axis, int]] = {
    ProjectType.HOBBY: {
        Axis.SPEED_VS_QUALITY: -1,
        Axis.EXPLORE_VS_FOCUS: -1,
        Axis.SIMPLICITY_VS_EXTENSIBILITY: -1,
        Axis.CAUTION_VS_BOLDNESS: 1,
    },
    ProjectType.PROTOTYPE: {
        Axis.SPEED_VS_QUALITY: -2,
        Axis.EXPLORE_VS_FOCUS: 2,
        Axis.SIMPLICITY_VS_EXTENSIBILITY: -2,
        Axis.CAUTION_VS_BOLDNESS: 2,
    },
    ProjectType.RESEARCH: {
        Axis.SPEED_VS_QUALITY: 1,
        Axis.EXPLORE_VS_FOCUS: -2,
        Axis.SIMPLICITY_VS_EXTENSIBILITY: -1,
        Axis.CAUTION_VS_BOLDNESS: 0,
    },
    ProjectType.PRODUCT: {
        Axis.SPEED_VS_QUALITY: 2,
        Axis.EXPLORE_VS_FOCUS: 1,
        Axis.SIMPLICITY_VS_EXTENSIBILITY: 1,
        Axis.CAUTION_VS_BOLDNESS: -2,
    },
}

DEFAULT_LIFESPAN: dict[ProjectType, Lifespan] = {
    ProjectType.HOBBY: Lifespan.MONTHS,
    ProjectType.PROTOTYPE: Lifespan.THROWAWAY,
    ProjectType.RESEARCH: Lifespan.MONTHS,
    ProjectType.PRODUCT: Lifespan.YEARS,
}

POLICIES: dict[ProjectType, Policies] = {
    ProjectType.HOBBY: Policies(
        testing="Smoke test of the main path",
        documentation="README with how to run it",
        approaches_before_committing=1,
        dependencies="Popular and pleasant to use",
        ask_owner_when="The goal itself would change",
    ),
    ProjectType.PROTOTYPE: Policies(
        testing="Only what keeps the demo path working",
        documentation="A short demo script",
        approaches_before_committing=1,
        dependencies="Whatever is fastest",
        ask_owner_when="The demo is at risk",
    ),
    ProjectType.RESEARCH: Policies(
        testing="Tests on evaluation and metric code, fixed seeds",
        documentation="Experiment log: config, result, conclusion",
        approaches_before_committing=3,
        dependencies="Pinned versions for reproducibility",
        ask_owner_when="Results contradict the hypothesis or change direction",
    ),
    ProjectType.PRODUCT: Policies(
        testing="Unit and integration tests, CI gate",
        documentation="User and API docs, changelog",
        approaches_before_committing=2,
        dependencies="Maintained, licence-checked, pinned",
        ask_owner_when="Anything irreversible, user-facing or costly",
    ),
}

# Sources that a project-type (re)derivation may overwrite. Owner and inferred
# values are never touched by defaults.
_REDERIVABLE = frozenset({None, Source.DEFAULT, Source.MEMORY})


def apply_type_defaults(
    picture: StrategyPicture,
    profile: OwnerProfile | None = None,
    turn: int | None = None,
) -> list[ItemKey]:
    """Fill type-derived items from the project type. Mutates ``picture``.

    Learned leans from the owner's profile win over the static defaults.
    Returns the items that changed.
    """
    project_type = picture.project_type.value
    changed: list[ItemKey] = []
    if project_type is None:
        # The type was dropped (e.g. the owner rejected our guess): clear its defaults.
        for key in sorted(TYPE_DERIVED_ITEMS, key=lambda k: k.value):
            current = picture.get(key)
            if current.is_set and current.source in _REDERIVABLE:
                picture.put(key, type(current)())
                changed.append(key)
        if changed:
            picture.revision += 1
        return changed

    learned = (profile.learned_leans.get(project_type, {}) if profile else {}) or {}

    for axis, key in AXIS_ITEMS.items():
        current = picture.get(key)
        if current.source not in _REDERIVABLE:
            continue
        if axis in learned:
            lean = max(-2, min(2, int(learned[axis])))
            new = Tracked[int](
                value=lean,
                source=Source.MEMORY,
                confidence=MEMORY_CONFIDENCE,
                evidence=f"learned from past {project_type.value} projects",
                turn=turn,
            )
        else:
            new = Tracked[int](
                value=DEFAULT_LEANS[project_type][axis],
                source=Source.DEFAULT,
                confidence=DEFAULT_CONFIDENCE,
                evidence=f"default for {project_type.value}",
                turn=turn,
            )
        if (current.value, current.source) != (new.value, new.source):
            picture.put(key, new)
            changed.append(key)

    lifespan = picture.lifespan
    if lifespan.source in _REDERIVABLE:
        new_lifespan = Tracked[Lifespan](
            value=DEFAULT_LIFESPAN[project_type],
            source=Source.DEFAULT,
            confidence=DEFAULT_CONFIDENCE,
            evidence=f"default for {project_type.value}",
            turn=turn,
        )
        if (lifespan.value, lifespan.source) != (new_lifespan.value, new_lifespan.source):
            picture.lifespan = new_lifespan
            changed.append(ItemKey.LIFESPAN)

    if changed:
        picture.revision += 1
    return changed


def apply_profile(
    picture: StrategyPicture, profile: OwnerProfile | None, turn: int | None = None
) -> list[ItemKey]:
    """Pre-fill standing constraints from memory. Mutates ``picture``."""
    if profile is None or not profile.standing_constraints:
        return []
    if picture.constraints.source not in (None, Source.DEFAULT):
        return []
    entries = [c.strip()[:200] for c in profile.standing_constraints if c and c.strip()][:8]
    if not entries:
        return []
    picture.constraints = Tracked[list[str]](
        value=entries,
        source=Source.MEMORY,
        confidence=MEMORY_CONFIDENCE,
        evidence="standing constraints from past projects",
        turn=turn,
    )
    picture.revision += 1
    return [ItemKey.CONSTRAINTS]
