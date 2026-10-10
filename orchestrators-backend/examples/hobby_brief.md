# Strategy brief: plant-watering (revision 7)

Decide implementation details yourself, in line with this brief. Values marked *soft* are assumptions; prefer reversible choices where they matter. If the brief does not settle something, call `ask_strategy` with your uncertainty; never ask the owner to pick between technical options.

## Intent
- **Goal:** Water my balcony plants automatically while I'm away (owner)
- **Project type:** hobby (owner confirmed)
- **Success looks like** (owner):
  - Plants survive a two-week holiday
- **Audience:** Just me (inferred, soft)
- **Lifespan:** months (owner confirmed)
- **Hard constraints** (owner):
  - Runs on the Raspberry Pi I already own
- **Non-goals:** unknown

## Trade-offs
- Leaning towards shipping fast over getting it right (lean -1; default for hobby, soft)
- Leaning towards trying several approaches over committing to one (lean -1; default for hobby, soft)
- Leaning towards keeping it minimal over building it to grow (lean -1; default for hobby, soft)
- Leaning towards taking bets over avoiding risk (lean +1; default for hobby, soft)

## Working policies (hobby)
- Testing: Smoke test of the main path
- Documentation: README with how to run it
- Approaches to try before committing: 1
- Dependencies: Popular and pleasant to use
- Involve the owner only when: The goal itself would change

## Still unknown
- non-goals
