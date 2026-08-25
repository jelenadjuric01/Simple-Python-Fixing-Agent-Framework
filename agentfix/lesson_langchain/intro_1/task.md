# How well does it actually work?

This step is discussion, not an exercise — do not run a live eval, it takes 8 minutes. The
numbers below are pre-computed and shipped with the repo.

## HumanEvalFix (20 tasks)

Running the agent over the 20-task HumanEvalFix workshop suite took **8m09s wall clock** and
**185,235 tokens** at **51 tok/s**. pass@1 is **0.60 (12/20)**, median 7 steps, max 10 (the
step-budget cap). Every one of the 8 failures spent the full 10 steps.


## Where to look

The raw numbers behind both figures are in `results/precomputed/humanevalfix.json` and
`results/precomputed/workshop.json`. Open them if you want the per-task detail —
just don't run a live eval to reproduce them; it's 8 minutes for the same numbers you already
have.
