"""The strategy picture rendered as Markdown, for every agent's instructions."""

from __future__ import annotations

from .schema import AXIS_ITEMS, ItemKey, Source, StrategyPicture, Tracked
from .templates import describe_value

_PROVENANCE = {
    Source.STATED: "owner",
    Source.CONFIRMED: "owner confirmed",
    Source.INFERRED: "inferred, soft",
    Source.MEMORY: "from past projects, soft",
}


def _provenance(t: Tracked, project_type: str | None) -> str:
    if t.source is Source.DEFAULT:
        return f"default for {project_type}, soft" if project_type else "default, soft"
    return _PROVENANCE.get(t.source, "unknown") if t.source else "unknown"


def _line(label: str, key: ItemKey, picture: StrategyPicture, ptype: str | None) -> str:
    t = picture.get(key)
    if not t.is_set:
        return f"- **{label}:** unknown"
    if isinstance(t.value, list):
        if not t.value:
            return f"- **{label}:** none ({_provenance(t, ptype)})"
        items = "\n".join(f"  - {v}" for v in t.value)
        return f"- **{label}** ({_provenance(t, ptype)}):\n{items}"
    value = t.value.value if hasattr(t.value, "value") else t.value
    return f"- **{label}:** {value} ({_provenance(t, ptype)})"


def render_brief(picture: StrategyPicture) -> str:
    ptype = picture.project_type.value.value if picture.project_type.value else None
    lines = [
        f"# Strategy brief: {picture.project_id} (revision {picture.revision})",
        "",
        "Decide implementation details yourself, in line with this brief. Values marked "
        "*soft* are assumptions; prefer reversible choices where they matter. If the brief "
        "does not settle something, call `ask_strategy` with your uncertainty; never ask the "
        "owner to pick between technical options.",
        "",
        "## Intent",
        _line("Goal", ItemKey.GOAL, picture, ptype),
        _line("Project type", ItemKey.PROJECT_TYPE, picture, ptype),
        _line("Success looks like", ItemKey.SUCCESS_CRITERIA, picture, ptype),
        _line("Audience", ItemKey.AUDIENCE, picture, ptype),
        _line("Lifespan", ItemKey.LIFESPAN, picture, ptype),
        _line("Hard constraints", ItemKey.CONSTRAINTS, picture, ptype),
        _line("Non-goals", ItemKey.NON_GOALS, picture, ptype),
        "",
        "## Trade-offs",
    ]
    for key in AXIS_ITEMS.values():
        t = picture.get(key)
        if t.is_set:
            lines.append(
                f"- {describe_value(key, t.value).capitalize()} "
                f"(lean {t.value:+d}; {_provenance(t, ptype)})"
            )
        else:
            lines.append(f"- {key.label.capitalize()}: unknown")

    policies = picture.policies
    if policies is not None:
        lines += [
            "",
            f"## Working policies ({ptype})",
            f"- Testing: {policies.testing}",
            f"- Documentation: {policies.documentation}",
            f"- Approaches to try before committing: {policies.approaches_before_committing}",
            f"- Dependencies: {policies.dependencies}",
            f"- Involve the owner only when: {policies.ask_owner_when}",
        ]

    # Gaps that carry a default are already marked *soft* above; list only the
    # ones with no value at all.
    unknown = [k for k in picture.open_gaps if not picture.get(k).is_set]
    if unknown:
        lines += ["", "## Still unknown"]
        lines += [f"- {k.label}" for k in unknown]

    return "\n".join(lines) + "\n"
