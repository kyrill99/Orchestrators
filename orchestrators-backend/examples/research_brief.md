# Strategy brief: chunker-eval (revision 7)

Decide implementation details yourself, in line with this brief. Values marked *soft* are assumptions; prefer reversible choices where they matter. If the brief does not settle something, call `ask_strategy` with your uncertainty; never ask the owner to pick between technical options.

## Intent
- **Goal:** Find out whether our chunking algorithm beats the current one (owner)
- **Project type:** research (owner confirmed)
- **Success looks like** (owner):
  - Beats the baseline on retrieval recall
  - Results are reproducible
- **Audience:** unknown
- **Lifespan:** months (owner confirmed)
- **Hard constraints:** none (owner)
- **Non-goals:** unknown

## Trade-offs
- Strongly favouring getting it right over shipping fast (lean +2; owner)
- Strongly favouring trying several approaches over committing to one (lean -2; default for research, soft)
- Leaning towards keeping it minimal over building it to grow (lean -1; default for research, soft)
- Balancing avoiding risk and taking bets (lean +0; default for research, soft)

## Working policies (research)
- Testing: Tests on evaluation and metric code, fixed seeds
- Documentation: Experiment log: config, result, conclusion
- Approaches to try before committing: 3
- Dependencies: Pinned versions for reproducibility
- Involve the owner only when: Results contradict the hypothesis or change direction

## Still unknown
- audience
- non-goals
