# Phase 6 — Counterfactual simulation and A/B spec (PROPOSED)

Contract for the `phase-6-simulation` branch. Source: `docs/PHASES.md` Phase 6
(⭐ checkpoint). Depends on Phase 5 merged (PR #7, `0b467c1`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
Phase 6 has no allowlist entry; the simulation is `random.Random` +
`generator.response.open_probability` (imported, unchanged), the power
calculation is `math.erf` + a bisection; `duckdb` (Phase 2) reads the built
tables. numpy, scipy or statsmodels are a STOP-and-ask — and unnecessary.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 5 shipped, and the carry-overs due
this phase. Items marked **design change** need approval before any
implementation; the rest are facts the spec pins. Numbers are read off
`main` (`0b467c1`): `make dbt-build PROFILE=tiny`, `make seed PROFILE=medium
&& make dbt-build PROFILE=medium` (unfrozen), and the two `truth/` roots
`eval/cli.py::truth_dir` already resolves.

1. **Served schedule only — fact, an invariant with a test.** The simulation
   reads `send_hour_local + send_minute_local / 60` off the built
   `scores_send_time` (the pair `eval/score.py::built_scores` already reads
   for coverage), never `center_hour_local`. It matters on the data: 15 of
   tiny's 20 users and 1,209 of medium's 2,000 have a served time that is
   not their centre (the band clamp) — a simulation off the centre would
   measure a schedule the product never sends (§5 non-goal). Test: build
   tiny into a tmp DuckDB, `update … set center_hour_local = served + 12`
   on a copy, simulate both — byte-identical block. `cohort_hour_local` is
   the band's anchor (§2.8) and is read for the third arm (item 3); no
   other column is read. Not a design change (a reader of an existing
   table).

2. **Common random numbers — design change, recommend (a).** The
   generator draws per prompt in the order `delivery → skew → responds →
   upload` (`generate.py::assign_cause`) with early returns, and `_prompt`
   then draws event timings from the SAME `Random` per cause. So the
   generator's stream cannot be replayed (a prompt's draws depend on every
   prior prompt's cause), and two arms that call `responds` (which draws
   inside) would consume different amounts of stream and diverge from the
   first prompt whose cause differs — the "lift" would carry draw noise of
   the same order as the effect on tiny (140 prompts). (a) **Four uniforms
   per prompt, drawn up front in `prompt_id` order from one
   `Random(SIMULATE_SEED)`, thresholds applied in the generator's order:**
   `u1 < delivery_fault_rate → delivery_fault`; `u2 < clock_skew_rate →
   unattributed`; `u3 ≥ open_probability(local_hour, user, window_minutes)
   → timing_gap`; `u4 < upload_fault_rate → upload_fault`; else `on_time`.
   Every arm applies its schedule to the same `(u1, u2, u3, u4)`, so
   `delivery_fault` and `unattributed` are identical across arms BY
   CONSTRUCTION (not merely in expectation), and only the `u3` threshold —
   the response probability at the arm's hour — can move a prompt. The
   reused symbol is `open_probability` (a pure function of hour, latent
   user, window); `responds` stays the generator's (it owns its draw).
   `generator/response.py` is imported and not edited — its docstring
   ("`eval/simulate.py` imports it unchanged") stays true of the module.
   Rejected: (b) independent streams per arm with a CI — reports noise a
   deterministic pairing removes, and the block would need N large enough to
   swamp it, which tiny never is; (c) refactor `assign_cause` to take the
   uniforms — a generator change, and although the draw values would be
   identical, the rule "the generator is not touched" exists so that no
   re-freeze question is ever opened by a downstream phase. Profile knobs
   (`delivery_fault_rate`, `clock_skew_rate`, `upload_fault_rate`,
   `window_minutes`) come from `generator/profiles/<p>.json` via
   `generator.profiles`; the latent user from `truth/users.jsonl`; the
   prompt list (`prompt_id`, `user_id`, `local_send_hour`) from
   `truth/prompts.jsonl` — all three are eval's to read (§3.1).
   **Approval gate.**

3. **"By cause", what is held fixed, and a third arm — fact plus one
   recommendation.** Per arm, the count of prompts in each of the five
   labels and the on-time rate `on_time / (prompts_sent − delivery_fault)`
   (the METRICS denominator: a simulated prompt is delivered iff it is not
   a delivery fault; on tiny 140 − 17 = 123 = `prompts_delivered`, the
   grace window is a generator emission detail no arm re-creates). Held
   fixed across arms: `delivery_fault` and `unattributed` (same `u1`, `u2`
   — item 2). Moved by the schedule: `timing_gap` (the only cause the hour
   enters). Moved *conditionally*: `upload_fault` — it is drawn only among
   responders (`u4` after `u3` passes), so its COUNT follows the responder
   count while its RATE among responders and its lateness are untouched:
   the simulation has no time quantity beyond the send hour — no
   `upload_delay`, no received time, no `timedelta` (a grep-level test on
   `eval/simulate.py` plus the identity). The identity every arm pins:
   `on_time + upload_fault + timing_gap + unattributed + delivery_fault =
   prompts_sent` (140 tiny; 60,000 medium). Schedules: **baseline** = the
   prompt's own `local_send_hour` from truth (the hour the data was
   generated at — 8.0 / 20.0 on tiny; on medium six of 60,000 prompts sit
   at 1.0–12.0 because the send fell on a tz-change day, and the baseline
   keeps them as they were); **recommended** = the user's served pair for
   every prompt of that user (the tz-change instant is not re-created —
   ≤ 0.01 % of medium's prompts, recorded as an assumption, not a BACKLOG
   row). **Third arm, recommend yes: `cohort`** = the user's
   `cohort_hour_local` (the band anchor, sent at the top of the hour as the
   generator does) — shows what the band move
   buys before the per-user shift, the number the A/B design needs to
   justify per-user shifts at all. It is one more schedule through the same
   code path and one more column in the block; the identity and the
   fixed-cause equalities hold for it too. Rejected: a `center` arm (the
   unclamped centre is not a schedule the product sends; item 1 forbids
   reading it for the served claim — the monotone-sanity test plants it
   only to bound the recommended arm, see the Invariants).

4. **Baseline: both arms simulated under CRN, with the data's counts
   printed beside — fact, argued.** §7 says "simulated on-time rate under
   the recommended schedule vs baseline". A baseline read off the data
   (attribution counts 75/8/17/34/6) is one realisation of the generator's
   interleaved stream; a recommended arm is a realisation of the
   simulation's stream — subtracting the two would report the difference
   between two RNG histories on top of the schedule effect (on tiny, the
   noise of ~140 Bernoulli draws is ± several prompts, the same size as the
   effect). Under CRN the simulated baseline and every other arm share
   their uniforms, so the lift is the schedule's alone. The block therefore
   carries a **`data` row** — the built `attribution` label counts
   (`eval/score.py::built_labels`, the pipeline's own output; equal to
   truth at accuracy 1.000) — as the anchor the simulated baseline must sit
   near, and the lift is `recommended − baseline`, both simulated. Not a
   design change (a second reader of `attribution`, already read by
   `score`).

5. **`docs/RESULTS.md` as generated blocks in a committed LIVING doc —
   design change (eval's first write to a committed file).** One block per
   profile between `<!-- simulate:begin <p> -->` / `<!-- simulate:end <p>
   -->`, the prose around them hand-written. `make simulate PROFILE=<p>
   [WRITE=yes]`: check mode renders the block from the built tables +
   truth and compares it byte-for-byte to the block in the file (prints a
   unified diff, exit 1 — the CI/DONE mode); `WRITE=yes` (the literal only)
   replaces exactly the bytes between the profile's markers and nothing
   else; a missing marker pair is a refusal (the writer never creates a
   file, never appends — a human adds the pair once). `truth_dir` is reused
   (the `(unfrozen)` tag is printed, not written into the block, so the
   block is the same whether medium is ever frozen). **Both tiny and medium
   blocks are committed**: tiny is the regression pin the offline suite
   reproduces from the fixture; medium is the proof, regenerated from
   `data/out/medium/` (pins-as-manifest). The seed: a constant
   `tests/pins.py::SIMULATE_SEED` passed as a function parameter — no
   Makefile knob (a re-seed is a code change with a DECISIONS entry; a
   different seed gives a different block, the test proves it by calling
   the function). Review gate: `docs/RESULTS.md` matches `RECORD_FILES`
   (`docs/.*`), so it is on the Record-updates list like any record file —
   the gate needs no change; `check_docs.py` link-checks it the moment it
   exists (LIVING), so the block names only targets that exist (`make
   simulate`), and a `TRACES` row pins the marker token in
   `eval/simulate.py`. Threat-model row: PROFILE validated `[a-z0-9_]+`,
   every path derived, WRITE literal-only, the one writable path is
   `docs/RESULTS.md`. **Approval gate.**

6. **`docs/AB_DESIGN.md` with a computed power table — design change
   (a second generated block, a second target).** Prose: randomisation
   unit = user (the shared moment is per cohort, so the *treatment* is the
   cohort's schedule; users are randomised into arms within cohort so both
   arms keep a shared moment); persistent holdout; primary metric = the
   METRICS `ontime_rate` block, linked, never restated; guardrails
   (notification opt-outs, unsubscribes, response rate); send-time jitter.
   **Power, recommend computed:** `eval/power.py::sample_size(p_baseline,
   mde, alpha, power)` — the two-proportion normal approximation, `n per arm
   = (z_{1−α/2} + z_{1−β})² · (p₁(1−p₁) + p₂(1−p₂)) / (p₂ − p₁)²`, the
   quantile by bisection on `math.erf` (no scipy); rendered as a
   `<!-- power:begin -->` block in `docs/AB_DESIGN.md` by `make power
   [WRITE=yes]` (same check/WRITE shape, same writer function as item 5),
   one row per `(profile baseline rate, MDE ∈ {1, 2, 5} pp)` at α 0.05,
   power 0.8, plus days-to-power at one prompt per user-day and the
   profile's delivery rate. Baseline rates are pins: `ONTIME_RATE` (tiny,
   0.609756) and a new `ONTIME_RATE_MEDIUM` (0.461143, read off the mart
   on `main`). Tests: the table reproduces the pinned numbers; `n` is
   strictly decreasing in MDE and increasing in power (the mutation
   targets). Rejected: hand-written numbers (no test can catch a typo, and
   the doc would drift from the pins the day a rate moves). The DONE
   command chains `make power` after `make simulate` — one line, two
   generated blocks. **Approval gate** — or split as 6b if the pinned
   decisions overflow (the draft below counts six; the developer's call).

7. **`eval/` stays one package — fact.** §3.1's eval row already reads
   "dbt outputs, truth" in and "console, `data/out/<p>/expected/`,
   `docs/RESULTS.md` blocks *(Phase 6)*" out; this phase adds
   `docs/AB_DESIGN.md`'s block to that cell and nothing else: `simulate.py`
   reads the built `scores_send_time` + `attribution`, `truth/`, and the
   profile JSON (the generator's input, not its output — reading it is not
   a truth or fixture question); `power.py` reads pins. Neither writes a
   table, `data/out/`, or `fixtures/`. `eval/` importing
   `generator.response` and `generator.profiles` is new but inside the
   boundary (eval may read anything; the generator is not pipeline code —
   both are in `test_truth_isolation.EXEMPT`, unchanged). No new top-level
   package. Python never computes a served score: the simulation consumes
   the schedule and never re-derives it.

8. **Drift to correct at exit — facts.** `CLAUDE.md` Repo map ("later
   `simulate.py`" → `simulate.py`, `power.py`; "later AB_DESIGN.md,
   RESULTS.md" → present), Commands ("Later phases add: `simulate` (6)" →
   the two targets documented, `simulate` and `power`), Determinism policy
   ("RESULTS blocks regenerate byte-identically" — now also AB_DESIGN's
   power block), Event-model facts (nothing — no column moves), Current
   status; `docs/ARCHITECTURE.md` §3.1 eval row (AB_DESIGN block), §8 if a
   surprise lands; `docs/PHASES.md` Phase 6 "Delivered"; `DECISIONS.md`
   Phase 6 entries (items 2, 3, 5, 6); `scripts/check_docs.py::TRACES` +1
   row; `tests/pins.py` (`SIMULATE_SEED`, `ONTIME_RATE_MEDIUM`, the
   simulated arm counts for tiny). **BACKLOG: nothing is DUE.** The
   `order by` tie-break row's trigger ("a second window-function tie-break
   lands, Phase 6/7") is not pulled: the block's per-cause order is a
   Python tuple in `LABELS` order (`swap-sort-key`-able), and no dbt model
   changes. Every other open row's trigger is a later phase or a frozen
   profile. No re-freeze: `fixtures/tiny/` is untouched (no `Freeze:` line).

Design changes above: items 2, 5, 6 (and the third arm in item 3). The
remaining sections (Teaching notes, Why, central constraint, DONE command,
Done-when, Evidence, Invariants with the mutations block, Pinned decisions,
Scope, Record updates, Threat model, Review & stack risk, Out of scope) are
drafted after these are approved.
