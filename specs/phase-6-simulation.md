# Phase 6 — Counterfactual simulation and A/B spec (APPROVED 2026-08-26 — implemented, in review)

Contract for the `phase-6-simulation` branch. Source: `docs/PHASES.md` Phase 6
(⭐ checkpoint). Depends on Phase 5 merged (PR #7, `0b467c1`).

**Status: APPROVED 2026-08-26; implemented on `phase-6-simulation`.** No new dependencies:
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

Design changes above — items 2, 3 (third arm), 5, 6 — **approved
2026-08-26**. Approval gates cleared; the sections below are the contract.

## Teaching notes (first appearance in this project)

- **Counterfactual simulation vs A/B.** An A/B test measures a treatment on
  people whose behaviour you do not know; the data answers. Here the
  "people" are latent records the generator drew (`truth/users.jsonl`),
  and their response to any hour is a formula we wrote
  (`open_probability`). Re-running that formula under a new schedule
  cannot discover anything the formula does not already encode — it shows
  that the model recovered the latent well enough that the schedule it
  chose scores well under the generator's own rule. That is a useful
  check on the pipeline and a useless claim about users, so it is named a
  simulation and the real experiment ships as a design (§7, brief §5b).
- **Common random numbers (CRN).** Two simulated arms differ by the
  schedule AND by the random draws each consumed; on 140 prompts the draw
  noise is as large as the effect. CRN gives every arm the same draws per
  prompt (here four uniforms), so a prompt's outcome differs between arms
  only where the schedule changes the threshold it is compared against.
  The lift becomes a deterministic function of the schedules, and causes
  the schedule cannot touch are identical across arms by construction.
- **Power, MDE, the normal approximation.** Power is the chance an
  experiment detects an effect of a given size (the minimum detectable
  effect, MDE) when it is real; 0.8 is the convention, with α = 0.05 the
  false-positive rate. For two proportions the required users per arm is
  `(z_{1−α/2} + z_{1−β})² · (p₁(1−p₁) + p₂(1−p₂)) / (p₂ − p₁)²` — the
  two-sample z-test's sample size, valid when `n·p` is not tiny, which
  on-time rates near 0.5 satisfy. The quantile `z` is the inverse of the
  normal CDF, which `math.erf` gives; a bisection inverts it to 1e-9.
- **Persistent holdout.** A slice of users who never receive the model's
  schedule, kept for the life of the feature (not just the test). It is
  the only way to measure the long-run effect once the rollout is at 100 %
  and the contemporaneous control is gone; the cost is the holdout's own
  worse on-time rate, which is why it is small (5 %).
- **Send-time jitter.** Once every user is sent at the model's hour, the
  data contains no other hour and the model can never learn that a
  different one would be better (the exposure bias §2.8 already names).
  A small random perturbation (± 15 min on a random 10 % of sends) keeps
  a continuous natural experiment in the data at a bounded cost.
- **Generated blocks in committed docs.** A number the reader should
  trust is written by the program that computes it, between markers a
  human never edits inside; the surrounding prose stays hand-written. The
  check mode (`make simulate PROFILE=<p>` without `WRITE`) recomputes the
  block and diffs it against the file, so CI fails the day the number
  drifts from the doc — the same idea as the golden CSVs, applied to
  Markdown.

## Why

Phases 1–5 prove that the pipeline recovers assigned causes (accuracy
1.000) and the latent reachable centre (MAE 0.35 h on medium). Neither
says whether the served schedule would have reduced `timing_gap`. Phase 6
answers that under the generator's own response rule — a counterfactual
simulation with the noise removed (CRN), reported per cause so the reader
sees which cause moved and which cannot — and ships the production A/B
design with a computed power table, so the project ends at a checkpoint
that states its claim honestly (§7). A fix PR cannot carry it: it is a
new eval output, the first written into a committed doc.

## The central constraint

**`fixtures/tiny/` does not move, the generator is not edited, and no dbt
model changes: the simulation consumes the served schedule and the
generator's response function, and re-derives neither.** A column wanted
by the simulation is a design change; a `Freeze:` line is a STOP.

## DONE command

```
make review-gate SPEC=specs/phase-6-simulation.md && make dbt-build PROFILE=tiny && make simulate PROFILE=tiny && make seed PROFILE=medium && make dbt-build PROFILE=medium && make simulate PROFILE=medium && make power
```

- `make review-gate SPEC=…` — offline suite (the simulation on tiny
  against the pinned arm counts and the committed block; the medium seed
  + build + block; CRN identities; planted-schedule monotonicity;
  determinism twice and under Tokyo; the write-path negatives; the power
  pins), ruff, check-docs (both new LIVING docs link-checked, two new
  `TRACES` rows), Evidence ids, Record-updates files, no `Freeze:` line.
- `make dbt-build PROFILE=tiny` — unchanged: 7 models, `dbt-build OK:
  tiny/duckdb`.
- `make simulate PROFILE=tiny` — renders tiny's block and diffs it against
  `docs/RESULTS.md`; prints `simulate OK: tiny, 140 prompts, 3 arms, block
  matches`.
- `make seed PROFILE=medium && make dbt-build PROFILE=medium && make
  simulate PROFILE=medium` — the proof, unfrozen: `simulate truth:
  data/out/medium/truth (unfrozen)`, `simulate OK: medium, 60000 prompts,
  3 arms, block matches`. Run twice → identical (Done-when 1).
- `make power` — renders the power table and diffs it against
  `docs/AB_DESIGN.md`; prints `power OK: 6 rows, block matches`.

## Done-when

1. **Deterministic block.** `make simulate PROFILE=medium` twice, and once
   under `TZ=Asia/Tokyo`, renders byte-identical blocks; the block is a
   function of the built tables, truth, the profile knobs and
   `SIMULATE_SEED` alone — a different seed gives a different block, and
   check mode exits 1 on any drift. *Evidence: row 1.*
2. **Lift per cause under CRN.** The block carries a `data` row (built
   `attribution` counts) and three simulated arms — `baseline` (the
   prompt's own hour), `cohort` (the band anchor), `recommended` (the
   served pair) — with the five label counts, `prompts_delivered` and
   `ontime_rate` each; per arm the five counts sum to `prompts_sent`;
   `delivery_fault` and `unattributed` are equal across arms; at the
   prompt level the only change between two arms is `timing_gap` ↔
   {`on_time`, `upload_fault`}. *Evidence: row 2.*
3. **Served only, and sane.** The recommended arm reads `send_hour_local +
   send_minute_local / 60`; planting a centre 12 h away changes nothing.
   A schedule at every user's latent centre scores ≥ the recommended arm;
   one 12 h from every centre scores ≤ baseline (planted schedules through
   the same function). *Evidence: row 3.*
4. **Lateness untouched by construction.** `eval/simulate.py` draws no
   time quantity: no upload delay, no received/upload time, no
   `timedelta`; `upload_fault` moves only through the responder count,
   and a prompt that responds in both arms carries the same
   `upload_fault`/`on_time` verdict in both. *Evidence: row 4.*
5. **Generated blocks, one write path.** `make simulate PROFILE=<p>
   [WRITE=yes]` and `make power [WRITE=yes]` write only on the literal
   `yes`, only the bytes between the profile's markers in
   `docs/RESULTS.md` / `docs/AB_DESIGN.md`, refuse a missing marker pair,
   and never touch another file; both tiny and medium blocks are
   committed, tiny pinned in the suite. *Evidence: row 5.*
6. **Power table computed and pinned; carried forward.** `eval/power.py`
   reproduces the pinned users-per-arm and days-to-power for `(tiny,
   medium) × MDE {1, 2, 5} pp` at α 0.05 / power 0.8; `n` falls with MDE
   and rises with power; `docs/AB_DESIGN.md` links METRICS for the
   primary metric and never restates it. No new package, no clock, no
   dbt change, no generator edit, no `Freeze:`; `truth/` read by `eval/`
   only. *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `make simulate PROFILE=medium` × 2 → identical `docs/RESULTS.md` bytes and `simulate OK: medium, 60000 prompts, 3 arms, block matches`; `tests/test_simulate.py::test_medium_block_matches_the_committed_block` (seeds + builds medium in-process as `test_scores.py` does, renders, equals the file's block); `::test_two_renders_are_byte_identical`; `::test_render_under_a_non_utc_host_zone_is_identical`; `::test_a_different_seed_gives_a_different_block`; `tests/test_eval.py::test_simulate_check_mode_exits_1_on_drift` (a planted byte inside the markers → exit 1, diff printed) |
| 2 | `tests/test_simulate.py::test_tiny_arm_counts_match_pins` (`SIMULATED_TINY` in `tests/pins.py`, read off the first green run; the `data` row = `ATTRIBUTION_LABEL_COUNTS`); `::test_every_arm_partitions_prompts_sent` (five counts sum to 140 / 60,000, `prompts_delivered = prompts_sent − delivery_fault`); `::test_fixed_causes_are_identical_across_arms`; `::test_only_timing_gap_moves_between_arms` (per-prompt labels of any two arms differ only as `timing_gap` ↔ {`on_time`, `upload_fault`}); `::test_cohort_arm_reads_the_band_anchor` |
| 3 | `tests/test_simulate.py::test_recommended_arm_reads_the_served_pair_not_the_centre` (copy of the built DB, `update … set center_hour_local = served + 12` → identical block); `::test_schedule_at_the_latent_centre_bounds_the_recommended_arm` (≥ on-time rate); `::test_schedule_twelve_hours_off_bounds_the_baseline` (≤) |
| 4 | `tests/test_simulate.py::test_simulation_draws_no_time_quantity` (the source of `eval/simulate.py` contains none of `upload_delay`, `server_received`, `server_upload`, `timedelta`, `datetime`, `_secs`, `uniform(`); `::test_only_timing_gap_moves_between_arms` (row 2 — the prompt-level identity is the lateness pin: a responder's `u4` verdict is arm-independent) |
| 5 | `tests/test_eval.py::test_simulate_write_only_on_literal_yes` (`YES`/`true`/`1`/` yes` refused, exit 2; `yes` rewrites only the bytes between the profile's markers of a tmp copy; the other profile's block and the prose are byte-identical); `::test_simulate_refuses_a_missing_marker_pair` (no file created, nothing written); `::test_power_write_only_on_literal_yes`; `::test_simulate_writes_no_other_file` (tmp tree snapshot before/after); `tests/test_simulate.py::test_tiny_block_matches_the_committed_block`; `tests/test_makefile.py::test_simulate_passes_profile_as_one_literal`, `::test_power_passes_write_as_one_literal` |
| 6 | `tests/test_power.py::test_power_table_matches_pins` (`POWER_TABLE` in `tests/pins.py`); `::test_sample_size_falls_with_mde_and_rises_with_power`; `::test_z_quantile_inverts_the_normal_cdf` (`z(0.975) = 1.959964` to 6 places, `z(0.8) = 0.841621`); `::test_ab_design_links_metrics_and_never_restates_ontime_rate` (the `docs/METRICS.md#ontime_rate` anchor present; no `sum(on_time)` formula in the doc); `make power` → `power OK: 6 rows, block matches`; `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth` (unchanged, `EXEMPT` unchanged); `tests/test_fixture.py::test_raw_dims_truth_hashes_are_the_phase_1_hashes` + `::test_phase_3_and_4_expected_hashes_are_unchanged`; `tests/test_dbt_conventions.py::test_no_clock_call_in_any_model_or_macro`; `tests/test_simulate.py::test_simulation_has_no_clock_call` (`now(`, `time.time`, `datetime.now` absent from `eval/simulate.py`, `eval/power.py`, `eval/blocks.py`); review-gate `PASS fixtures`; `git diff main --stat -- dbt/ generator/` empty |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Served only.** For all users, the recommended arm's hour is `send_hour_local + send_minute_local / 60` off the built `scores_send_time`; the cohort arm's is `cohort_hour_local`; no arm reads `center_hour_local`, and a planted centre changes no block. | `test_recommended_arm_reads_the_served_pair_not_the_centre`; `test_cohort_arm_reads_the_band_anchor`; mutation `eval/simulate.py::built_schedule constant-return:{}` |
| 2. **Determinism.** For all (built tables, truth, profile, seed), the rendered block is byte-identical across runs and host zones; a different seed gives a different block; check mode exits 1 on any drift. | `test_two_renders_are_byte_identical`; `test_render_under_a_non_utc_host_zone_is_identical`; `test_a_different_seed_gives_a_different_block`; `test_simulate_check_mode_exits_1_on_drift`; mutation `eval/simulate.py::draw_uniforms constant-return:[]` |
| 3. **Partition.** For all arms, `on_time + upload_fault + timing_gap + unattributed + delivery_fault = prompts_sent` and `prompts_delivered = prompts_sent − delivery_fault`; `ontime_rate = on_time / prompts_delivered`, NULL only when nothing is delivered. | `test_every_arm_partitions_prompts_sent`; `test_tiny_arm_counts_match_pins`; mutations `eval/simulate.py::cause_of constant-return:"on_time"`, `eval/simulate.py::ontime_rate constant-return:0.0`, `eval/simulate.py::ontime_rate invert-guard` |
| 4. **Common random numbers.** For all prompts and all pairs of arms, the four uniforms are the same and are applied in the generator's order (`delivery → skew → respond → upload`); hence `delivery_fault` and `unattributed` are identical across arms and a prompt's label differs only as `timing_gap` ↔ {`on_time`, `upload_fault`}. | `test_fixed_causes_are_identical_across_arms`; `test_only_timing_gap_moves_between_arms`; mutation `eval/simulate.py::cause_of invert-guard` (the delivery guard inverted → the fixed-cause counts leave the pin) |
| 5. **Lateness untouched.** For all arms, no time quantity beyond the send hour is drawn or written; a prompt responding in two arms carries the same upload verdict in both. | `test_simulation_draws_no_time_quantity`; `test_only_timing_gap_moves_between_arms` |
| 6. **Monotone sanity.** For all planted schedules through `simulate_arm`, the arm at every user's latent centre scores ≥ the recommended arm's on-time rate, and the arm 12 h from every centre scores ≤ the baseline's. | `test_schedule_at_the_latent_centre_bounds_the_recommended_arm`; `test_schedule_twelve_hours_off_bounds_the_baseline` |
| 7. **Data anchor.** For all profiles, the `data` row equals the built `attribution` label counts (on tiny, the pinned 75/8/17/34/6), read through `eval/score.py::built_labels`. | `test_tiny_arm_counts_match_pins` |
| 8. **One write path.** For all invocations, a file is written iff `WRITE` is the literal `yes`; the write replaces exactly the bytes between the profile's `simulate:begin`/`end` (or the `power:begin`/`end`) markers of the one named doc; a missing pair is a refusal; no other path is touched. | `test_simulate_write_only_on_literal_yes`; `test_simulate_refuses_a_missing_marker_pair`; `test_simulate_writes_no_other_file`; `test_power_write_only_on_literal_yes`; mutations `eval/blocks.py::replace_block invert-guard`, `eval/blocks.py::write_block delete-call`, `eval/cli.py::simulate_cmd invert-guard` |
| 9. **Declared order.** For all blocks, causes render in `LABELS` order and arms in `ARMS` order — explicit keys, never insertion order. | `test_tiny_block_matches_the_committed_block`; mutation `eval/simulate.py::render_block swap-sort-key` |
| 10. **Power.** For all `(p₁, MDE, α, power)`, `sample_size_per_arm` is the two-proportion formula on the erf-inverted quantiles, strictly decreasing in MDE and increasing in power; the table reproduces the pins. | `test_power_table_matches_pins`; `test_sample_size_falls_with_mde_and_rises_with_power`; `test_z_quantile_inverts_the_normal_cdf`; mutations `eval/power.py::sample_size_per_arm constant-return:1`, `eval/power.py::z_quantile constant-return:0.0`, `eval/power.py::z_quantile invert-guard` |
| 11. **Boundary and carry-forward.** For all files under `eval/`, reads are dbt outputs, `truth/`, the profile JSON and pins; writes are console, `data/out/<p>/expected/`, and the two marker blocks; `dbt/` and `generator/` are unchanged; no clock, no new package; `fixtures/tiny/` unchanged. | `test_pipeline_dirs_never_mention_truth`; `test_simulation_has_no_clock_call`; `test_raw_dims_truth_hashes_are_the_phase_1_hashes`; `test_phase_3_and_4_expected_hashes_are_unchanged`; review-gate `PASS fixtures`; `uv.lock` unchanged in the diff |

```mutations
eval/simulate.py::built_schedule           constant-return:{}
eval/simulate.py::draw_uniforms            constant-return:[]
eval/simulate.py::cause_of                 constant-return:"on_time"
eval/simulate.py::cause_of                 invert-guard
eval/simulate.py::ontime_rate              constant-return:0.0
eval/simulate.py::ontime_rate              invert-guard
eval/simulate.py::render_block             swap-sort-key
eval/blocks.py::replace_block              invert-guard
eval/blocks.py::write_block                delete-call
eval/cli.py::simulate_cmd                  invert-guard
eval/power.py::sample_size_per_arm         constant-return:1
eval/power.py::z_quantile                  constant-return:0.0
eval/power.py::z_quantile                  invert-guard
```

Equivalent-mutant exclusions, named up front (each verified once at
implementation on a scratch copy of the block; the verdicts are recorded
in Implementation notes):

- `cause_of` has four guards; `invert-guard` addresses only the first
  (delivery). The skew, response and upload guards are pinned by
  `SIMULATED_TINY` (any inversion moves every arm's counts) and by the
  fixed-cause / only-timing-gap identities — no second operator can
  target them, so they are named here, not left implicit.
- `simulate_arm` and `arm_rows` contain no sort and no guard: prompts are
  pre-sorted by the single key `prompt_id` in `read_prompts` (a one-element
  key `swap-sort-key` cannot address; the uniform-to-prompt pairing is
  pinned by `SIMULATED_TINY`), arms are iterated in the `ARMS` tuple.
- `find_block` shares `replace_block`'s missing-marker guard; one
  `invert-guard` line covers the write path.
- `days_to_power` is one `ceil` of a ratio — `constant-return` on
  `sample_size_per_arm` already moves every row it feeds; a separate line
  would kill through the same pin.
- `eval/score.py::built_labels` is the Phase 3 reader — its `delete-call`
  would be refused (called as a value); the `data` row's pin covers it.

## Pinned decisions (do not re-litigate)

- **Four uniforms per prompt, one `Random(SIMULATE_SEED)`, `prompt_id`
  order, thresholds in the generator's order via `open_probability`
  (reconciliation item 2)** — satisfies invariants 2, 4, 5. `responds` is
  not reused (it owns its draw); `generator/response.py` is not edited.
  Rejected: independent streams + CI; a generator refactor.
- **Three arms plus the `data` row; `baseline` = the prompt's own
  `local_send_hour` from truth, `cohort` = `cohort_hour_local`,
  `recommended` = the served pair; the `data` row = built `attribution`
  counts (items 3, 4)** — satisfies invariants 1, 3, 7. The tz-change
  instant is not re-created in the simulated arms (assumption, ≤ 0.01 %
  of medium). Rejected: a `center` arm; a data-vs-simulated lift.
- **`docs/RESULTS.md` blocks between `<!-- simulate:begin <p> -->` /
  `<!-- simulate:end <p> -->`, both tiny and medium committed; `make
  simulate PROFILE=<p> [WRITE=yes]` in the `report` shape, check mode
  diffs and exits 1, `yes` replaces the marked bytes only, a missing pair
  refuses; `eval/blocks.py` holds `find_block` / `replace_block` /
  `write_block` shared with `power` (item 5)** — satisfies invariants 2,
  8, 9. `SIMULATE_SEED` is a `tests/pins.py` constant passed as a
  parameter; no Makefile knob. The `(unfrozen)` tag is printed, never
  written into the block. Rejected: a seed variable (a fifth threat-model
  column for a value nothing should vary); a RESULTS file per profile.
- **`eval/power.py`: `z_quantile` (bisection on `math.erf`),
  `sample_size_per_arm`, `days_to_power` (one prompt per user-day × the
  profile's delivered share), rendered as the `<!-- power:begin -->` block
  of `docs/AB_DESIGN.md` by `make power [WRITE=yes]`; rows `(tiny, medium)
  × MDE {1, 2, 5} pp`, α 0.05, power 0.8; pins `ONTIME_RATE_MEDIUM`
  (0.461143), `PROMPTS_SENT_MEDIUM` (60,000), `PROMPTS_DELIVERED_MEDIUM`
  (55,293), `POWER_TABLE` (item 6)** — satisfies invariant 10. Rejected:
  hand-written numbers; scipy.
- **`docs/AB_DESIGN.md` prose: randomisation unit = user within cohort
  (both arms keep a shared moment — the treatment is the cohort's
  schedule, users are split so the control cohort-moment and the treated
  one run side by side), 5 % persistent holdout, primary metric = the
  METRICS `ontime_rate` block by link, guardrails (opt-outs, unsubscribes,
  response rate), ± 15 min jitter on 10 % of sends** — satisfies
  invariant 11's "never restates". Rejected: cohort-level randomisation
  (three cohorts is no sample).
- **`eval/` gains `simulate.py`, `power.py`, `blocks.py` and two CLI
  subcommands; it imports `generator.response.open_probability` and
  `generator.profiles`; `EXEMPT` unchanged; ARCHITECTURE §3.1's eval row
  gains "`docs/AB_DESIGN.md` block" (item 7)** — satisfies invariant 11.
  Rejected: a top-level `simulation/` package (a new boundary for one
  reader).

## Scope (files)

- `eval/simulate.py` (new), `eval/power.py` (new), `eval/blocks.py`
  (new), `eval/cli.py` (`simulate`, `power` subcommands; `truth_dir`
  reused)
- `Makefile` (`simulate`, `power`)
- `docs/RESULTS.md` (new: prose + two marker pairs + generated blocks),
  `docs/AB_DESIGN.md` (new: prose + one marker pair + generated block)
- `tests/test_simulate.py` (new), `tests/test_power.py` (new),
  `tests/test_eval.py`, `tests/test_makefile.py`, `tests/pins.py`
- `scripts/check_docs.py` (`TRACES` +2: `("eval/simulate.py",
  "simulate:begin")`, `("eval/power.py", "power:begin")`)
- Records: `specs/phase-6-simulation.md`, `DECISIONS.md`, `docs/PHASES.md`,
  `CLAUDE.md`, `docs/ARCHITECTURE.md` (§3.1 cell; §8 only if a surprise)
- Untouched by contract: `dbt/`, `generator/`, `fixtures/`, `loader/`,
  `pyproject.toml`, `uv.lock`

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 6 entries: CRN via four uniforms and
      `open_probability`; the third arm; both arms simulated with the data
      anchor; generated blocks and the seed-as-pin; computed power; the
      tz-change assumption. "Decisions still in force": the
      counterfactual-simulation line gains "(CRN, per cause — Phase 6)"
- [ ] `docs/PHASES.md` — Phase 6 "Delivered" paragraph; Done-when as landed
- [ ] `CLAUDE.md` — Repo map (`eval/`: `simulate.py`, `power.py`,
      `blocks.py`; `docs/`: RESULTS.md, AB_DESIGN.md present); Commands
      (`simulate`, `power`; "Later phases add" loses `simulate (6)`);
      Determinism policy (both blocks); Current status; Open BACKLOG rows
      unchanged at 10
- [ ] `docs/ARCHITECTURE.md` — §3.1 eval row writes cell gains
      `docs/AB_DESIGN.md` block; §8 Gotchas only if a surprise lands
- [ ] BACKLOG — none: nothing is due (reconciliation item 8), nothing
      opened; the count stays 10
- [ ] Spec amendments — none: no later spec exists
- [ ] `docs/RESULTS.md` / `docs/AB_DESIGN.md` — the generated blocks (and
      the new prose around them); METRICS and DEPLOYMENT untouched
- [ ] README — none (no README in the repo)

## Threat model (REQUIRED)

`simulate` takes `PROFILE` and `WRITE`, `power` takes `WRITE`, in the
settled shape (one Python process, `[a-z0-9_]+`, every path derived —
`data/<p>.duckdb`, `truth_dir(p)`, `generator/profiles/<p>.json`,
`docs/RESULTS.md`; `$(call _Q,$(value VAR))`; both already `unexport`ed).
Only the literal `yes` writes, and the one writable path is a committed
doc — the first eval target with one: the write is confined to the bytes
between an existing marker pair (no create, no append, no other file), so
the worst case of a stray `WRITE=yes` is a block the check mode would
have regenerated anyway, visible in `git diff`. No delete, no cloud, no
input. Residual: `WRITE=yes` from the environment writes the block — the
stated Phase 3 class, now on a tracked file; the reviewer sees it in the
diff.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make simulate PROFILE= [WRITE=yes]` | refused (`simulate: refused — bad profile name`) | refused, no path derived | one literal, refused | reaches Python, validated the same; `WRITE` from env honoured (residual, stated) | n/a — no CONFIRM; `WRITE` must equal `yes` | `tests/test_makefile.py::test_simulate_passes_profile_as_one_literal`; `tests/test_eval.py::test_simulate_write_only_on_literal_yes`; `::test_cli_refuses_bad_profile_before_any_path` (gains `simulate`) |
| `make power [WRITE=yes]` | n/a (no PROFILE; empty `WRITE` = check mode) | n/a — no path argument exists | one literal, refused (`power: refused — WRITE takes only the literal \`yes\``) | `WRITE` from env honoured (residual, stated) | n/a | `tests/test_makefile.py::test_power_passes_write_as_one_literal`; `tests/test_eval.py::test_power_write_only_on_literal_yes` |

## Review & stack risk

- **code-reviewer** (triggered — `eval/`, Makefile, tests, `scripts/`):
  the served pair read and nothing else from `scores_send_time` beyond
  `cohort_hour_local`; four uniforms in `prompt_id` order and the
  generator's threshold order; no time quantity; `open_probability`
  imported, `responds` not; no clock; the write confined to the markers;
  `truth_dir` reused; no generator or dbt diff; pins read off the run,
  never adjusted.
- **security-reviewer** (triggered — a target that writes a tracked file
  under a `WRITE` knob): the marker-confined write, the env residual, no
  path argument, no write outside `docs/RESULTS.md` / `docs/AB_DESIGN.md`.
- **functionality-tester** (triggered): DONE command; the planted centre;
  the planted schedules; a planted byte inside and outside the markers;
  each mutation line KILLED and the five exclusions reasoned; `make seed
  PROFILE=tiny` still `manifest match`; two medium runs byte-identical.
- **coherence-auditor** at exit (mandatory, whole repo): CLAUDE.md no
  longer says "later `simulate.py`" / "later AB_DESIGN.md, RESULTS.md" /
  "Later phases add: `simulate` (6)"; §3.1 cell; PHASES Phase 6
  "Delivered"; DECISIONS in-force line; both docs link-clean; the count.
- Stack risk (first hour, STOP on any surprise, §8): `duckdb.connect` on a
  copy of a built DB for the planted-centre test (an `update` on a dbt-
  built table — a `.wal` left behind is the thing to watch); a Markdown
  table inside HTML comments passing `check_docs`' link regex; the
  bisection's fixed iteration count giving the same float on every
  platform (pin to 6 places, as Phase 4/5 did).

## Out of scope (deferred, recorded)

- A frozen `medium` — not planned (Phase 5).
- Re-creating the tz-change send instant in the simulated arms — an
  assumption stated in DECISIONS Phase 6, not a BACKLOG row (≤ 0.01 % of
  medium's prompts, zero on tiny).
- A confidence interval on the lift — CRN makes the lift deterministic;
  an interval over seeds is a different question (model uncertainty), not
  asked by §7.
- The write-back and the production experiment itself — Phase 8 and the
  A/B design's reader.
- Guardrail metrics as marts — the design names them; no event exists for
  opt-outs in the generator (§5: out-of-scope items get a BACKLOG row only
  if code would follow; none will in v1).
