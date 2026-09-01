# INSIGHT.md — what the numbers mean, and what they do not

One page, read after the [README](../README.md) table and before believing any
headline. Every figure cited here is a committed, pinned number
([docs/RESULTS.md](RESULTS.md), `tests/pins.py`); this doc frames them, it does
not compute new ones.

## The claim

The pipeline recovers a per-user send time and recommends a schedule. On the
2,000-user `medium` profile, the served schedule lifts the **simulated** on-time
rate by **+0.162371** over each prompt's own send hour (baseline 0.460920 →
recommended 0.623291; [RESULTS.md](RESULTS.md) `medium` block). The gain is
almost entirely `timing_gap` recovered — prompts that were delivered and unopened
because they arrived at the wrong local hour, now sent when the user is reachable.

## The three honest caveats

**1. Tiny's lift is negative, and that is expected.** On the frozen 20-user
`tiny` fixture the simulated lift is **−0.033333** ([RESULTS.md](RESULTS.md)
`tiny` block). Its `c-morning` cohort has a bin-3/bin-10 tie in the pooled open
histogram (twenty users, ~10 opens each), so the cohort anchor lands at hour 3
while the data was sent at 8 — a worse hour. Twenty users prove nothing about
recovery; `tiny` exists to pin the code path byte-for-byte, `medium` to show the
effect. We report the negative number rather than hide it: a headline that only
ever moved up would be a red flag, not a feature.

**2. The simulation is counterfactual, not an A/B — it is circular by
construction.** Outcomes are re-drawn from the *same latent reachability that
generated the data*, using the generator's own response rule
(`generator/response.py::open_probability`). Under [common random
numbers](RESULTS.md) — four uniforms per prompt from one seeded stream, in the
generator's draw order — `delivery_fault` and `unattributed` are identical across
arms, so only the schedule moves the outcome. That makes the lift a clean,
noise-free measure of *the schedule under the rule the data came from*. It says
**nothing** about real users, whose true reachability we do not know. It shows the
model learns the latent it was given; it does not show the latent matches reality.

**3. The real test is the A/B, which is specified but not run.**
[docs/AB_DESIGN.md](AB_DESIGN.md) carries the production experiment: randomisation
unit, a persistent holdout, the primary metric, guardrails, jitter, and a
generated power table (users per arm and days to power at α 0.05 / power 0.8). The
power table is the honest bridge: it says how much real traffic it would take to
detect a 1/2/5-point effect. Until that experiment runs, the recovery number is a
simulation result, and this document calls it one.

## Why the architecture makes these caveats checkable

- **Truth isolation.** The generator's assigned causes are a side-file only
  `eval/` reads — never a pipeline input. Label accuracy vs that truth is
  **1.000** on `tiny`: the attribution recovers exactly the causes the generator
  assigned, so "the pipeline is right about *why* a prompt was late" is a measured
  claim, not an assertion.
- **The model is a dbt model.** The recommendation is SQL (`scores_send_time`),
  scored against truth by reachable-centre MAE (**0.816201 h** tiny, **0.352354
  h** medium). Python never computes a number the pipeline serves, so there is no
  hidden scorer to disagree with the served table.
- **Determinism.** Same seed → byte-identical output, no clock on the data path.
  The simulation, the power table, and the very numbers on the front page all
  regenerate byte-for-byte; a drift is a red test. Nothing here is a
  one-time screenshot.

## The one-line summary

The pipeline provably attributes lateness and learns a send time, and under the
data's own rule the served schedule recovers a large chunk of `timing_gap`. That
is a strong *engineering* result and a *simulated* product result — the A/B in
[AB_DESIGN.md](AB_DESIGN.md) is what would turn the second into a real one.
