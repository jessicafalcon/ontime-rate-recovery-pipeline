# Phase 5 — Send-time model as a dbt model (APPROVED 2026-08-25 — implemented, in review)

Contract for the `phase-5-send-time` branch. Source: `docs/PHASES.md` Phase 5.
Depends on Phase 4 merged (PR #6, `f39a31b`).

**Status: APPROVED 2026-08-25; implemented on `phase-5-send-time`.** No new dependencies:
Phase 5 has no allowlist entry; the model is dbt SQL, the eval extension uses
`duckdb` (Phase 2), `math` and `json` from the standard library. numpy, scipy
or any stats package is a STOP-and-ask — and unnecessary: circular means are
`sin`/`cos`/`atan2`.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 4 shipped, and the carry-overs due
this phase. Items marked **design change** need approval before any
implementation; the rest are facts the spec pins. Numbers are read off
`main` (`f39a31b`): `make dbt-build PROFILE=tiny` and, for item 1, `make
seed PROFILE=medium && make dbt-build PROFILE=medium` (unfrozen, from
`data/out/medium/`).

1. **`medium` — the Done-when pins MAE on a profile that has never been
   frozen or built. Recommendation: (b) run it unfrozen, pin the numbers,
   no fixture.** Facts: `make seed PROFILE=medium` takes 4.8 s and emits 38
   files / 418,112 records (2,000 users × 30 days, 109 MB on disk); `make
   dbt-build PROFILE=medium` already works on `main` (load falls back to
   `data/out/medium/`, marked `(unfrozen)`), 90 items green in 5.0 s
   (343,509 staged events, 71,966 organic opens, every user has ≥ 30).
   Both are seeded (`seed: 2`, one `Random`, fixed `SIM_START`), so the
   built tables are byte-identical on every machine — the pin *is* the
   manifest. Costs: (a) tiny-only would pin nothing about recovery (20
   users, 9–14 opens each — a regression pin, not a proof; PHASES' "on
   medium" would have to be weakened); (c) `fixtures/medium/` is 109 MB in
   git plus a second manifest, a second `Freeze:` machinery target and a
   fixture the read-only rule then binds forever — the largest diff of the
   project for a number a 10-second command reproduces. (b) costs the
   review gate and CI about +10 s (seed + load + build of medium inside one
   pytest, `OTR`-gated like nothing else: it is offline and in-process, so
   it runs under `make test`), touches no `Freeze:` machinery, and needs
   ONE code change outside the model: `eval/cli.py` resolves
   `<profile>/truth/` the way `loader/load.py::fixture_dir` resolves raw —
   `fixtures/<p>/` first, else `data/out/<p>/`, printed `(unfrozen)`. Truth
   stays under a `truth/` directory that only `eval/` reads (§3.1, §4.4 —
   the reader and the boundary do not move; `test_truth_isolation` is
   unchanged). PHASES' "tiny and medium" stays true: both are reported;
   medium is the proof (`MAE_MEDIUM`, `COVERAGE_MEDIUM`), tiny the
   regression pin (`MAE_TINY`, `COVERAGE_TINY`). Rejected: (a), (c) as
   above. **Not a design change** (no new fixture directory, no new writer,
   no new reader) — but it is the choice this item asks you to make, so it
   is an approval gate below.
2. **BACKLOG "A user whose `tz` changes mid-window straddles two local
   clocks" (DUE) — aggregate per `user_id`, pooled across tz rows, on each
   event's own local time. Fact, closes the row.** The generator draws ONE
   latent centre per user in *local* hours (`generate.py`: `cohort hour +
   N(0, 4)`, mod 24) and places every organic open at `centre + N(0,
   width/2)` local, converted to UTC through the tz valid *that day*
   (`local_to_utc(day, hour, tz)`). So a user who moves Tokyo → London
   keeps opening at the same local hour, and `stg_events.
   client_event_time_local` (SCD2 range join, Phase 2) already puts both
   halves on that one clock: u-000008's 9 Tokyo + 2 London opens and
   u-000010's 7 + 5 are samples of one centre each (`truth/users.jsonl` has
   one row per user — the eval grain agrees). A per-(user, tz) histogram
   would thin an already sparse sample (u-000008: 11 → 9 and 2) and force
   the score to pick a row; pooling in UTC would be the actual bug the row
   warns about (the halves land 9 hours apart) and is exactly what
   `to_local_time` in staging prevents. What the score serves: one row per
   `user_id` in local hours; which zone applies at send time is the serving
   table's `tz` column (§2.9), joined from the open `dim_user` row by the
   Phase 8 write-back, not carried on `scores_send_time` (§2.8's column list
   is unchanged). Unit test `features_user_hour_pools_a_tz_change`: one
   user, two `dim_user` rows (Tokyo until day 3, then London), opens at
   08:00 local on both sides (UTC 23:00 and 08:00) → one histogram row at
   hour 8 with count = all opens, no row per tz. Rejected: per user-tz row;
   the latest tz row only (throws away most of u-000008's sample).
3. **Circular hours — plain SQL, not a sixth macro. Fact.** Three
   operations are needed and each has one ANSI spelling that both engines
   accept: circular distance between two hours `h1 − h2 − 24 ·
   floor((h1 − h2 + 12) / 24)` (signed, in (−12, 12]); wrap into [0, 24)
   `x − 24 · floor(x / 24)`; the circular mean `atan2(sum(sin θ), sum(cos
   θ))` with `θ = 2π · hour / 24` and `π = acos(−1)`. `floor`, `atan2`,
   `sin`, `cos`, `sqrt`, `acos`, `least`, `greatest`, `abs` are in both
   dialects with the same names and semantics; the model uses `floor`
   rather than `mod`/`%` because BigQuery's `MOD` is integer/NUMERIC-only
   and `%` is not ANSI — so neither appears in a model (the denylist test
   gains `%` as a seventh form; `mod(` is allowed on integer hour bins
   only). Nothing here is dialect-divergent, which is the test for a macro
   (§3.2); a helper macro that merely shortens SQL would be a sixth with no
   dispatch reason. Verified: each expression rendered on DuckDB in the
   first hour; the BigQuery build (Phase 9) is the second check, as for
   every model. Rejected: a `circular_diff` dispatch macro (sixth, DECISIONS
   entry, no divergence to dispatch on); `mod` on floats.
4. **Shrinkage, the prior, and what `confidence` is. Fact — vars named
   here, defaults in `dbt_project.yml`.** *Teaching notes land in the spec
   body.* The user's evidence is the resultant vector of its opens,
   `(Σ cos θ_i, Σ sin θ_i)` over `n` opens at bin centres `θ = 2π (h +
   0.5) / 24`. The **prior** is the cohort's pooled histogram over the same
   window, summarised the same way: mean direction `μ_c` and mean resultant
   length `R̄_c ∈ [0, 1]` (1 = every open in one bin, 0 = uniform). The
   **posterior** direction is the angle of `user vector + k · R̄_c ·
   (cos μ_c, sin μ_c)`, `k` = var `shrinkage_pseudo_count` (default 5, "the
   prior is worth five opens"). **`confidence`** := the mean resultant
   length of that combined vector, `|combined| / (n + k)`, a number in [0,
   1] — how concentrated the evidence is after shrinkage. With `n = 0` the
   combined vector is `k · R̄_c · (cos μ_c, sin μ_c)`, so the centre is
   `μ_c` and `confidence = R̄_c` **exactly** (Done-when 2 is an algebraic
   identity, not a branch — no `case` arm to mutate, the unit test pins
   it). As `n` grows with opens at one hour, the posterior angle moves
   monotonically from `μ_c` to that hour (the invariant's N = 0 / 1 / many
   test). Users with zero opens do not appear in `features_user_hour`; the
   score starts from `dim_user` (one row per user — the open SCD2 row's
   `cohort_id`) and left-joins the features. Neither fixture has such a
   user (tiny: every user has ≥ 9 opens; medium ≥ 30), so it is a unit
   test only. The **cohort moment** (§2.8 "the window maximizing P(open
   within window_minutes)") is the integer local hour `h` whose
   `[h, h + window_minutes)` (circular; `window_minutes` read from the
   cohort's prompts, `max` over `stg_prompts` — one value per cohort on
   both profiles) holds the most pooled opens, `h` over the cohort's opened
   bins; ties → the smallest opened `h`
   (item 6 of the invariants, a two-way-tie unit test). The served time is
   the posterior centre clamped to `±max_user_shift_min` of the cohort
   moment (circular), split into `send_hour_local` (integer) and
   `send_minute_local` (0–59). Vars: `feature_window_days` 30 (covers all
   of tiny's 7 and medium's 30 days; the window is `(horizon −
   feature_window_days, horizon]` with `horizon = max(client_event_time)`
   over all staged events — never the clock), `max_user_shift_min` 120,
   `shrinkage_pseudo_count` 5, `model_version` `'v1'`. Rejected: argmax of
   the user's own 24-bin histogram as the centre (with 10 opens and σ = 3 h
   the mode is noise; the circular mean uses every open); `confidence =
   n / (n + k)` (a vibe about sample size, says nothing about
   concentration, and is 0 — not "the prior" — at n = 0).
5. **`computed_as_of` and `model_version` — facts.** `computed_as_of` :=
   `max(client_event_time)` over the organic opens inside the feature
   window (one value per build, on every row — the write-back compares it
   per row, §2.9); on tiny `2026-01-12 00:47:00` (the last organic open —
   the all-events horizon `02:31:53` is a later upload event), medium
   `2026-02-04 07:58:00`. `model_version` := `var('model_version')`, literal `'v1'`,
   selected as a column. Both are pinned by the two-build byte-identity test
   and the `TZ=Asia/Tokyo` build test (Phase 3/4 shape, `tests/
   test_scores.py`), plus the clock denylist. A build with a different
   `feature_window_days` legitimately moves `computed_as_of` (the window
   moved) — the determinism claim is per (raw, dims, vars), §4.2.
6. **eval: MAE, coverage, and a third golden. Recommendation: yes, freeze
   `expected/scores_send_time.csv` on tiny — re-freeze, needs approval.**
   `eval/score.py` gains `reachable_center_mae(built, truth)` — mean over
   users of the circular absolute difference in hours between the model's
   **unclamped posterior centre** and `truth.reachable_center_local_hour` —
   and `coverage(built, truth)` — the share of users whose **served**
   `send_hour_local + send_minute_local / 60` lies within `centre ± width /
   2` (circular). Two numbers because they answer two questions: MAE is "did
   organic opens recover the latent?" (§7 (b)), coverage is "does the
   product constraint still land inside the reachable window?" (§5: no
   per-user times outside the band; latent centres sit `N(0, 4 h)` off the
   cohort hour by construction, so a served time clamped to ±2 h will miss
   some users — coverage says how many, and that is the point, not a
   defect). This needs the unclamped centre as a column:
   **`center_hour_local`** (fractional, [0, 24)) is added to §2.8's list —
   **a design change, one paragraph:** *the invariant it serves is "for all
   users, the reported MAE is computed from a column the model built, never
   from a number Python re-derived" (model-is-a-model, CLAUDE.md); without
   the column, eval would have to recompute the shrinkage in Python to
   measure it, which is the second implementation of the model the contract
   forbids. It is a read-only diagnostic column beside the served ones; the
   write-back (Phase 8) does not carry it to `send_schedule`.* `make eval
   PROFILE=<p>` prints one more line, `eval OK: <p>, mae <x> h (pin <x>),
   coverage <y> (pin <y>), N users`, exit 1 when either is off its pin —
   the pins are exact (`MAE_TINY`, `COVERAGE_TINY`, `MAE_MEDIUM`,
   `COVERAGE_MEDIUM`, read off the first green build, `≤` for MAE as PHASES
   says and equality within 1e-9 as the regression pin). A planted shift
   (every centre + 3 h in a tmp copy of truth) raises MAE — the negative
   test. The **golden**: `scores_send_time` on tiny is 20 rows, a pure
   function of the data, and Phase 8's write-back input — a frozen CSV is
   the cheapest possible pin of Done-when 3 and of §2.9's contract, one
   more `Golden` spec (`relation main_scores.scores_send_time`, columns
   `user_id, cohort_id, send_hour_local, send_minute_local, center_hour_local,
   confidence, model_version, computed_as_of`, `key_width 1`, sorted by its
   first two columns `(user_id, cohort_id)` — `user_id` is unique, so the
   BACKLOG "sort key" row's trigger (`key_width > 2`) is **not** pulled;
   re-deferred unchanged). `confidence` and `center_hour_local` are
   `round(…, 6)` in the model (the Phase 4 rounding decision). `Freeze:
   fixtures/tiny/MANIFEST.sha256`, 15 → 16 lines, the fifteen existing
   byte-identical (a moved hash is a STOP). `make scores-golden PROFILE=<p>
   [WRITE=yes]` in the `report` shape. The retention golden's trigger
   ("first *frozen* profile ≥ 28 days") is **not** pulled by an unfrozen
   medium (item 1) — re-deferred with the same trigger, with a note that
   medium closes every window (2,000 non-NULL `retained`) the day it is
   frozen. Rejected: MAE off the served time (the band would dominate and
   the number would measure the product rule, not recovery); a Python
   re-derivation of the centre; no golden (then the only content pin is the
   two-build identity, which a wrong-but-stable model passes).
7. **Other drift, facts.** (a) CLAUDE.md Repo map: "later `features,
   scores`" → `models/features` (Phase 5: `features_user_hour`),
   `models/scores` (`scores_send_time`); `eval/score.py` gains MAE +
   coverage; Commands gains `scores-golden` and `eval`'s new line; Event
   model facts gains the four vars. (b) `tests/test_dbt_conventions.py`:
   the folders tuple `("staging", "attribution", "marts")` gains
   `"features", "scores"`; `SINGULAR` gains
   `assert_send_time_within_band.sql` (`ref('scores_send_time')`: every row's
   circular distance to its cohort moment ≤ `max_user_shift_min`; one score
   per user is a `schema.yml` `unique` test, not a singular); the dialect
   denylist gains `%`.
   (c) PHASES Phase 5: Goal's "on tiny and medium" stands (item 1); Done-when
   1 gains "(unfrozen, seeded; tiny as the regression pin)"; the `Delivered`
   paragraph at exit. (d) ARCHITECTURE §2.8: the column list gains
   `center_hour_local` (item 6, if approved), the two new vars, the
   aggregation unit (item 2) and "bin centres"; §3 diagram rows already
   match; §8 if a surprise lands. (e) `dbt_project.yml`: `features` and
   `scores` folder configs (schemas `features`, `scores`), four vars.
   (f) `test_truth_isolation.py`: `dbt/` is already a derived pipeline dir —
   the two new folders are guarded the day they exist; nothing to add.
   (g) DECISIONS "still in force" already carries the model line; the Phase
   5 appendix records items 1–4 and 6.

Approval gates: **item 1** (run medium unfrozen; the choice, not a design
change), **item 6** (`center_hour_local` as a column — design change; the
re-freeze adding `expected/scores_send_time.csv`). Items 2–5 and 7 are
facts. STOP here; the spec body (Invariants, Evidence, Pinned decisions,
Threat model, teaching notes) follows in the next commit.

## Teaching notes (first appearance in this project)

- **Exposure bias — why responses are not a feature.** A prompt response can
  only be observed at the hour the prompt was sent, so a histogram of
  response times is a histogram of the *schedule*, not of the user: a user
  prompted at 08:00 who answers at 08:10 looks like a morning person even
  if they are awake at 23:00. Organic `app_opened` is the only event whose
  timing the product did not choose, so it is the only signal that says
  where the user actually is (§2.8, DECISIONS "still in force").
- **Bayesian shrinkage / pseudo-counts.** With ten opens a per-user estimate
  is mostly noise. Shrinkage treats the cohort's pooled behaviour as if it
  were `k` extra observations of every user ("pseudo-counts"): a user with
  few opens is pulled toward the cohort, a user with many is barely moved,
  and a user with none *is* the cohort. `k` is a dial between "trust the
  cohort" and "trust the user"; it is a dbt var so a re-tune is a config
  diff, not a model rewrite.
- **Circular statistics — circular mean vs argmax.** Hours live on a circle:
  23:00 and 01:00 are two hours apart, and their ordinary mean (12:00) is
  the worst possible answer. Mapping each hour to a point on the unit circle
  and averaging the points gives the circular mean (via `atan2`), and the
  length of that average — the resultant length, 0 to 1 — says how
  concentrated the opens are. The argmax of a 24-bin histogram is also
  circular-safe but throws away every open outside the winning bin; with
  ten samples that is most of them.
- **The cohort-band constraint — why not per-user send times.** The daily
  prompt is a shared moment (everyone in `c-morning` is asked at the same
  local hour); fully personal send times dissolve that product and make
  every downstream comparison a comparison of schedules. The model picks the
  cohort's best window from pooled opens and lets each user drift at most
  `max_user_shift_min` from it (§5 non-goal: per-user times outside the
  band). Coverage (item 6) measures what the band costs.
- **dbt vars as model hyper-parameters.** `feature_window_days`,
  `max_user_shift_min`, `shrinkage_pseudo_count` and `model_version` are
  dbt vars with defaults in `dbt_project.yml`: the model reads them with
  `var(...)` at compile time, a unit test can override them per case, and
  `--vars` on the command line re-tunes a build without editing SQL. They
  are part of the determinism claim (§4.2: output is a function of raw +
  dims + vars) — change a var and `computed_as_of`/the scores may move; that
  is a different build, not non-determinism.

## Why

Phases 3–4 say how the product is doing; nothing yet says what to do about
it. The model turns organic opens into a send time per user that a
notification service can act on, inside the product's shared moment, and
`eval` says how close that time is to the latent window that generated the
data — §7's second reported number and the input to Phase 6's simulation
and Phase 8's write-back. It is a phase, not a fix PR: two dbt layers, a
third golden, the first read of an unfrozen profile, and four vars every
later phase tunes.

## The central constraint

**`fixtures/tiny/{raw,dims,truth,expected/*.csv}` do not move;
`expected/scores_send_time.csv` is added once; Python never computes a
score.** `Freeze: fixtures/tiny/MANIFEST.sha256` — 15 → 16 lines, the
fifteen existing byte-identical (a moved hash is a STOP). `eval/` measures
the columns the model built (`center_hour_local`, the served time); it never
re-derives a centre from the features.

## DONE command

```
make review-gate SPEC=specs/phase-5-send-time.md && make dbt-build PROFILE=tiny && make scores-golden PROFILE=tiny && make eval PROFILE=tiny && make seed PROFILE=medium && make dbt-build PROFILE=medium && make eval PROFILE=medium
```

- `make review-gate SPEC=…` — offline suite (features/scores unit, data and
  singular tests through the in-process `dbt build`; the tiny golden; the
  two-build and Tokyo identities; the medium seed + build + pins; eval's
  negative tests; conventions incl. the `%` denylist; truth isolation over
  the two new folders), ruff, check-docs, Evidence ids, Record-updates
  files, the `Freeze:` declaration.
- `make dbt-build PROFILE=tiny` — the live gate: 7 models; prints
  `dbt-build OK: tiny/duckdb`.
- `make scores-golden PROFILE=tiny` — `scores_send_time` vs
  `fixtures/tiny/expected/scores_send_time.csv`; prints `scores-golden OK:
  tiny, 20 rows, 0 differ`.
- `make eval PROFILE=tiny` — label accuracy as before, plus `eval OK: tiny,
  mae <MAE_TINY> h (pin …), coverage <COVERAGE_TINY> (pin …), 20 users`.
- `make seed PROFILE=medium && make dbt-build PROFILE=medium && make eval
  PROFILE=medium` — the proof profile, unfrozen (reconciliation item 1):
  `load: source=data/out/medium (unfrozen)`, `eval OK: medium, mae
  <MAE_MEDIUM> h …, 2000 users`.

## Done-when

1. **Recovery.** On medium, reachable-centre MAE (circular hours, off
   `center_hour_local`) `≤ tests/pins.py::MAE_MEDIUM` and coverage (served
   time inside `centre ± width/2`) `= COVERAGE_MEDIUM`; tiny reproduces
   `MAE_TINY` / `COVERAGE_TINY`; a planted +3 h shift of every truth centre
   raises MAE. *Evidence: row 1.*
2. **Sparse users.** A user with zero organic opens in the window is served
   `center_hour_local = μ_c`, the cohort default, with `confidence = R̄_c`
   exactly; with 1 open the centre lies strictly between `μ_c` and the
   open; with many it is at the open (reconciliation item 4). *Evidence:
   row 2.*
3. **Determinism.** Two `make dbt-build` runs, and a build under
   `TZ=Asia/Tokyo`, give byte-identical `features_user_hour` and
   `scores_send_time`; `computed_as_of` is the data-derived window
   maximum; `model_version` is the var. The tiny table equals the frozen
   golden row for row. *Evidence: row 3.*
4. **Organic-only, per user.** No prompt-linked event contributes to
   `features_user_hour`; a user whose tz changes mid-window has ONE
   histogram on their own local clock (BACKLOG row closed). *Evidence:
   row 4.*
5. **Band, circle, tie.** Every served time is within `max_user_shift_min`
   (circular) of its cohort moment (dbt singular test); a user opening at
   23:00 and 01:00 is centred at 00:30, never 12:30; a two-way tie for the
   cohort window resolves to the smaller opened hour. *Evidence: row 5.*
6. **Truth isolation and conventions.** `dbt/models/features` and
   `dbt/models/scores` never mention truth; no clock call, no `%`/`::`/
   dialect form inline, five macros, every model described and tested; the
   golden and the pins reach `fixtures/` through `make freeze` alone.
   *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `make eval PROFILE=medium` → `eval OK: medium, mae … (pin …), coverage … (pin …), 2000 users`; `tests/test_scores.py::test_medium_mae_and_coverage_match_pins` (seeds medium in-process to `data/out/medium/`, builds into a tmp DuckDB, asserts both pins — the +10 s test); `tests/test_scores.py::test_tiny_mae_and_coverage_match_pins`; `tests/test_eval.py::test_planted_center_shift_raises_mae` (truth copy in tmp, every centre + 3 h → MAE > pin, exit 1); `tests/test_eval.py::test_eval_reads_unfrozen_truth_and_says_so` (`(unfrozen)` in the output for a `data/out/` profile) |
| 2 | unit tests `scores_send_time_zero_opens_is_the_prior` (a `dim_user` row with no opens → `center_hour_local = μ_c`, `confidence = R̄_c` of the cohort's pooled opens, both to 6 places), `scores_send_time_shrinks_monotonically` (three users in one cohort: 0, 1, 30 opens at one hour → centres ordered `μ_c`, between, at the hour; `confidence` rises) in `dbt/models/scores/schema.yml` |
| 3 | `tests/test_scores.py::test_two_builds_give_the_same_features_and_scores`; `::test_scores_under_a_non_utc_host_zone_are_identical`; `::test_computed_as_of_is_the_window_max` (equals `max(client_event_time)` of `app_opened` in the window on tiny: `2026-01-12 00:47:00`, earlier than the all-events horizon); `::test_model_version_is_the_var`; `::test_scores_golden_matches_fixture` (byte-identical render); `tests/test_dbt_conventions.py::test_no_clock_call_in_any_model_or_macro` |
| 4 | unit tests `features_user_hour_is_organic_only` (a user with `response_recorded`/`prompt_opened`/`capture_started` at hour H and `app_opened` at hour K → one row, hour K), `features_user_hour_pools_a_tz_change` (reconciliation item 2), `features_user_hour_respects_the_window` (an open older than `feature_window_days` before the horizon is excluded; one at exactly the boundary is excluded — half-open, review round 1); `schema.yml` `unique` on the combination via singular-free `unique` on `user_id` in `scores_send_time` + `not_null`; `tests/test_scores.py::test_tiny_features_match_organic_open_pin` (`sum(n_opens)` = `ORGANIC_OPEN_ROWS` 211, 20 users) |
| 5 | `dbt/tests/assert_send_time_within_band.sql` (zero rows beyond the band; also `send_hour_local` in 0–23, `send_minute_local` in 0–59); unit tests `scores_send_time_clamps_to_the_band_edge` (one user far ahead of the cohort moment, one far behind → `moment + max`, `moment − max`, both sides), `scores_send_time_is_circular` (opens at 23:00 and 01:00 → `center_hour_local = 0.5`), `scores_send_time_breaks_a_window_tie_by_smaller_hour` (two hours with equal pooled mass → the smaller); `tests/test_scores.py::test_cohort_moments_and_as_of_match_pins` (tiny's own tie: `c-morning` bins 3 and 10 at 12 → 3), `::test_every_served_time_is_inside_the_band_and_in_range`; mutations `drop-arm:1`, `drop-arm:2` on `scores_send_time.sql::send_hour_frac` |
| 6 | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth` (derived dirs — `dbt/` already covered); `tests/test_dbt_conventions.py::test_no_dialect_function_in_any_model` (gains `%`), `::test_exactly_five_dispatch_macros`, `::test_every_model_has_description_and_a_test` (folders gain `features`, `scores`), `::test_singular_tests_exist_and_target_their_relation`; `tests/test_fixture.py::test_raw_dims_truth_hashes_are_the_phase_1_hashes` + `::test_phase_3_and_4_expected_hashes_are_unchanged`; `tests/test_eval.py::test_scores_golden_write_only_on_literal_yes`; review-gate `PASS fixtures` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Organic-only.** For all `features_user_hour` rows, every counted event is `app_opened` (the event with no `prompt_id`); a user's responses at hour H and opens at hour K yield a histogram at K only. | unit `features_user_hour_is_organic_only`; `test_tiny_features_match_organic_open_pin` |
| 2. **Per-user local clock.** For all users, one histogram keyed `user_id`, each open at its own `client_event_time_local` hour; a tz change mid-window neither splits nor shifts it. | unit `features_user_hour_pools_a_tz_change`; `test_scores_under_a_non_utc_host_zone_are_identical` |
| 3. **Window.** For all opens, counted iff `client_event_time` is in `(horizon − feature_window_days, horizon]`, `horizon = max(client_event_time)` over staged events; `computed_as_of = max(client_event_time)` of the counted opens. | unit `features_user_hour_respects_the_window`; `test_computed_as_of_is_the_window_max` |
| 4. **Shrinkage.** For all users, `center_hour_local` is the direction of `user vector + k·R̄_c·(cos μ_c, sin μ_c)` and `confidence = |combined| / (n + k)`; at `n = 0` they equal `μ_c` and `R̄_c` exactly; the centre moves monotonically toward the user's opens as `n` grows. | unit `scores_send_time_zero_opens_is_the_prior`; unit `scores_send_time_shrinks_monotonically` |
| 5. **Band.** For all users, the circular distance from the served time to the cohort moment is `≤ max_user_shift_min`; a centre beyond the band is served at the nearer edge. | `assert_send_time_within_band.sql`; unit `scores_send_time_clamps_to_the_band_edge`; mutations `drop-arm:1`, `drop-arm:2` on `send_hour_frac` |
| 6. **Circular.** For all users, the centre is the short-arc mean: opens at 23:00 and 01:00 centre at 00:30, never 12:30; distances never exceed 12 h. | unit `scores_send_time_is_circular`; `assert_send_time_within_band.sql` (a long-arc distance would exceed the band) |
| 7. **Tie-break.** For all cohorts, the cohort moment is the smallest OPENED hour among those with maximal pooled mass in `[h, h + window_minutes)` — declared key order `(mass desc, hour asc)`, never insertion order. | unit `scores_send_time_breaks_a_window_tie_by_smaller_hour`. **Gap:** the key is an `order by` in a window function, not a `case` arm and not Python — no mutation operator can express it; the unit test is the only pin (named, BACKLOG-style note in Out of scope) |
| 8. **Determinism.** For all builds on the same raw + dims + vars, `features_user_hour` and `scores_send_time` are byte-identical, on any host zone; `model_version` is the var; on tiny the table equals the frozen golden. | `test_two_builds_give_the_same_features_and_scores`; `test_scores_under_a_non_utc_host_zone_are_identical`; `test_model_version_is_the_var`; `test_scores_golden_matches_fixture`; mutations `eval/golden.py::diff_rows constant-return:[]` (Phase 4, kept) |
| 9. **Measurement, not modelling.** For all profiles, `eval` reports MAE off `center_hour_local` and coverage off the served time vs `truth/users.jsonl`, circular, exits 1 off the pin, and computes no centre of its own; a shifted truth raises MAE. | `test_medium_mae_and_coverage_match_pins`; `test_tiny_mae_and_coverage_match_pins`; `test_planted_center_shift_raises_mae`; mutations `eval/score.py::reachable_center_mae constant-return:0.0`, `eval/score.py::coverage constant-return:1.0`, `eval/score.py::circular_abs_diff_hours constant-return:0.0` (every distance 0 → MAE 0 and coverage 1 miss both pins; `delete-call` was refused — the function is called inside expressions, never as a statement), `eval/score.py::coverage invert-guard` |
| 10. **Unfrozen truth is named.** For all profiles, `eval` reads `fixtures/<p>/truth/` when it exists, else `data/out/<p>/truth/` and prints `(unfrozen)`; never any other path. | `test_eval_reads_unfrozen_truth_and_says_so`; `tests/test_eval.py::test_cli_refuses_bad_profile_before_any_path` (existing); mutation `eval/cli.py::truth_dir swap-sort-key` (the `(fixtures, data/out)` preference tuple — if the operator addresses it; else `invert-guard` on the `is_file()` check) |
| 11. **Freeze scope.** For all freezes, `expected/scores_send_time.csv` enters `fixtures/` only via `make freeze`; the fifteen existing manifest lines do not move. | `test_raw_dims_truth_hashes_are_the_phase_1_hashes`; `test_phase_3_and_4_expected_hashes_are_unchanged`; `test_scores_golden_write_only_on_literal_yes`; review-gate `PASS fixtures` |
| 12. **Carried forward.** For all models under `features/` and `scores/`: no clock, no inline dialect form (`%` included), no truth, five macros and no sixth, a description and a test each. | `test_no_clock_call_in_any_model_or_macro`; `test_no_dialect_function_in_any_model`; `test_pipeline_dirs_never_mention_truth`; `test_exactly_five_dispatch_macros`; `test_every_model_has_description_and_a_test` |

```mutations
eval/score.py::reachable_center_mae                              constant-return:0.0
eval/score.py::coverage                                          constant-return:1.0
eval/score.py::coverage                                          invert-guard
eval/score.py::circular_abs_diff_hours                           constant-return:0.0
eval/cli.py::truth_dir                                           invert-guard
dbt/models/scores/scores_send_time.sql::send_hour_frac           drop-arm:1
dbt/models/scores/scores_send_time.sql::send_hour_frac           drop-arm:2
```

Equivalent-mutant exclusions, named up front (verified once at
implementation through `make mutate` on a scratch copy of the block: the
seven real lines KILLED, `swap-arms:1,2` SURVIVED as predicted; the
`delete-call` line originally drafted for `circular_abs_diff_hours` was
refused — no statement-level call — and became `constant-return:0.0`):

- `scores_send_time.sql::send_hour_frac swap-arms:1,2` — arm 1 is "shift
  beyond +band → moment + max", arm 2 "shift beyond −band → moment − max";
  the conditions are disjoint (a signed circular shift is on one side), so
  their order is unobservable.
- The zero-open fallback is an identity of the shrinkage formula, not a
  `case` (reconciliation item 4) — there is no arm to drop; the unit test
  pins it.
- The cohort-window argmax and its tie-break are an `order by` inside a
  window function — outside every operator (invariant 7's named gap).
- `features_user_hour` has no multi-arm `case`: the organic filter is a
  `where`, the window a `where` — pinned by the three feature unit tests
  and `ORGANIC_OPEN_ROWS`.

## Implementation notes (2026-08-25)

- `cohort_hour_local` is a ninth column (the band's anchor): with it
  `assert_send_time_within_band.sql` reads the served table instead of
  recomputing the cohort moment. Recorded in DECISIONS Phase 5 with
  `center_hour_local`; the golden carries nine columns.
- The served minute is `round(frac × 60)` on the 1440-minute circle, split
  by integer arithmetic — a float `floor` turned 0.4999… h into 29 min in
  the first hour (the `is_circular` unit test pins 00:30).
- tiny's `c-morning` cohort moment is a real two-way tie (bins 3 and 10 at
  12 pooled opens → 3), so invariant 7 is exercised on the fixture as well
  as in the unit test (`test_cohort_moments_and_as_of_match_pins`).
- `tests/test_attribution.py::project_vars` tolerates a string var
  (`model_version: v1`) and its var-set pin lists the four new vars.

## Review round 1 fixes (2026-08-25)

Test-only: `features_user_hour_respects_the_window` gained an open at
exactly `horizon − feature_window_days` (excluded), so the hand mutation
`<` → `<=` on the window guard dies. Wording amendment: invariant 7, §2.8,
METRICS and CLAUDE.md now say the cohort moment ranges over the cohort's
OPENED bins (an optimal window can always start at one; the only divergence
from "all 24 hours" is which equal-mass start wins a tie inside a window
wider than one bin — unreachable at `window_minutes` 60 on every profile);
accepted to BACKLOG with trigger "a profile ships `window_minutes > 60`".
§2.8 names the integer `mod` beside `floor`/`atan2`.

## Review round 2 fixes (2026-08-25)

Records only: the BACKLOG table rows split by the round-1 edit repaired
(the `order by` row has its trigger back, the argmax row lost the stray
cell); §2.8 names both integer `mod`s (hour bins, minute-of-day); the
`scores_send_time.sql` header carries the "opened bins" qualifier (missed
in round 1).

## Pinned decisions (do not re-litigate)

- **`features_user_hour` = one row per `(user_id, hour_local)` with
  `n_opens`, over organic `app_opened` in the feature window, from
  `stg_events` alone** — satisfies invariants 1–3. Sparse (no row for an
  empty bin; the score's left join and `coalesce` supply zero), `hour_local
  = extract(hour from client_event_time_local)` — the ANSI form on both
  engines. Rejected: dense 24 × users rows (a cross join for zeros the
  vector sum never needs); reading `attribution` or `stg_prompts` (no
  prompt-linked signal, invariant 1).
- **`scores_send_time` = `dim_user` (open row, one per user) left-joined to
  the user's resultant vector and the cohort's pooled prior, the shrinkage
  of reconciliation item 4, the cohort moment as the argmax window over the
  pooled bins with `order by mass desc, hour asc`, the clamp as a
  three-arm `case … end as send_hour_frac`, split into hour/minute** —
  satisfies invariants 4–8. Angles use bin centres `h + 0.5` (an unbiased
  centre; the 23:00/01:00 case reads 00:30). Rejected: argmax of the user's
  own histogram (noise at n ≈ 10); a two-arm `least/greatest` clamp (not
  addressable by the arm operators); per-user-tz rows (item 2).
- **Vars `feature_window_days: 30`, `max_user_shift_min: 120`,
  `shrinkage_pseudo_count: 5`, `model_version: v1`, defaulted in
  `dbt_project.yml`** — satisfies invariants 3, 4, 5, 8. `window_minutes`
  is read from the cohort's prompts (`max` over `stg_prompts`), not a var
  — one knob, in the data. Rejected: a `window_minutes` var duplicating a
  staged column.
- **`eval/score.py` gains `circular_abs_diff_hours`, `reachable_center_mae`,
  `coverage`; `eval/cli.py` `score` prints the second line and `truth_dir`
  resolves fixtures-then-`data/out` with `(unfrozen)`; the pins are
  `MAE_TINY`, `COVERAGE_TINY`, `MAE_MEDIUM`, `COVERAGE_MEDIUM` in
  `tests/pins.py`** — satisfies invariants 9, 10. Console only. Rejected:
  MAE off the served time; a Python re-derivation; a `fixtures/medium/`.
- **Third `Golden` spec `SCORES_SEND_TIME` (`main_scores.scores_send_time`,
  eight columns, `key_width 1`, `expected/scores_send_time.csv`), `make
  scores-golden PROFILE=<p> [WRITE=yes]` in the `report` shape;
  `center_hour_local` and `confidence` rounded to 6 places in the model** —
  satisfies invariants 8, 11. One `Freeze:` line; 15 → 16. Rejected: no
  golden; a wider sort key (the BACKLOG row's trigger is not pulled).
- **The medium test seeds into `data/out/medium/` in-process and builds into
  a tmp DuckDB** — satisfies invariant 9 under `make test`. `data/out/` is
  the generator's sanctioned, gitignored output; the seed is idempotent
  (byte-identical). Rejected: an `OTR_INT`-gated integration test (it is
  offline and in-process — the marker is for services); a tmp output root
  (the loader derives every path from the profile name by design).

## Scope (files)

- `dbt/models/features/features_user_hour.sql`, `schema.yml`;
  `dbt/models/scores/scores_send_time.sql`, `schema.yml`;
  `dbt/dbt_project.yml` (two folder configs, four vars)
- `dbt/tests/assert_send_time_within_band.sql`
- `eval/score.py` (MAE, coverage), `eval/golden.py` (`SCORES_SEND_TIME`),
  `eval/cli.py` (`scores-golden`, `truth_dir`, the eval line); `Makefile`
  (`scores-golden`)
- `fixtures/tiny/expected/scores_send_time.csv`,
  `fixtures/tiny/MANIFEST.sha256`
- `tests/pins.py`, `tests/test_scores.py` (new), `tests/test_eval.py`,
  `tests/test_fixture.py`, `tests/test_makefile.py`,
  `tests/test_dbt_conventions.py`
- `docs/METRICS.md` (blocks for `center_hour_local`, `confidence`, the
  served time — a `### ` block each, the Phase 4 test extended to the
  scores folder); records below

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 5 appendix (medium unfrozen; per-user pooling;
      circular hours as ANSI, no sixth macro; shrinkage + confidence
      formula; `center_hour_local`; the third golden; the tie-break gap)
- [ ] `docs/PHASES.md` — Phase 5 Done-when as landed ("unfrozen, seeded";
      tiny as regression pin); "Delivered" paragraph
- [ ] `CLAUDE.md` — Current status; Commands (`scores-golden`, `eval`'s
      new line, `seed`/`dbt-build` on an unfrozen profile); Repo map
      (`dbt/models/features`, `dbt/models/scores`, `eval/score.py`); Event
      model facts (four vars, the aggregation unit); BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §2.8 `center_hour_local`, bin centres, the
      per-user unit, the vars; §8 if a surprise lands
- [ ] `BACKLOG.md` — close "A user whose `tz` changes mid-window …";
      re-defer "ontime_retention has no frozen golden" (note: medium closes
      every window when frozen) and "Every golden's sort key …" (not
      pulled); open "tie-break in a window `order by` has no mutation
      operator"
- [ ] Spec amendments — none (no later spec exists)
- [ ] `docs/METRICS.md` — new blocks: `center_hour_local`, `confidence`,
      `send_hour_local`/`send_minute_local`
- [ ] README — none (no README yet)

## Threat model (REQUIRED)

`scores-golden` takes `PROFILE` and `WRITE` in the settled shape (one Python
process, `[a-z0-9_]+`, every path derived, `$(call _Q,$(value VAR))`, both
already `unexport`ed). Only the literal `yes` writes
`data/out/<p>/expected/scores_send_time.csv` (never `fixtures/`). `eval`
gains no variable; its new `truth_dir` derives both candidates from the
validated name. No delete, no cloud, no input. Residual: `WRITE=yes` from
the environment writes `data/out/` — the stated Phase 3 class.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make scores-golden PROFILE= [WRITE=yes]` | refused (`scores-golden: refused — bad profile name`) | refused, no path derived | one literal, refused | reaches Python, validated the same; `WRITE` from env honoured (residual, stated) | n/a — no CONFIRM; `WRITE` must equal `yes` | `tests/test_makefile.py::test_scores_golden_passes_profile_as_one_literal`; `tests/test_eval.py::test_scores_golden_write_only_on_literal_yes`; `tests/test_eval.py::test_cli_refuses_bad_profile_before_any_path` |

## Review & stack risk

- **code-reviewer** (triggered — dbt models/tests, `eval/`, Makefile,
  tests): organic-only source, per-user unit, the window on
  `client_event_time` with a data-derived horizon, `floor`-based circular
  arithmetic with no `%`/`mod` on floats, the shrinkage identity at n = 0,
  `order by mass desc, hour asc`, no clock, five macros, no truth under
  `dbt/`, eval computes no centre, `truth_dir` never leaves the two roots.
- **security-reviewer** (triggered — a Makefile target taking a variable
  and a `WRITE` knob): the `WRITE` residual; no write under `fixtures/`
  outside `freeze`; the medium test writes only `data/out/medium/`.
- **functionality-tester** (triggered): DONE command; the planted truth
  shift; a planted golden difference; each mutation line KILLED and the
  four named exclusions reasoned; `make seed PROFILE=tiny` still
  `manifest match` with three `expected/` files; medium build byte-identical
  across two runs.
- **coherence-auditor** at exit: CLAUDE.md Repo map no longer says "later
  features, scores"; PHASES Phase 5 wording; §2.8 column list; METRICS has
  one block per score column; one BACKLOG row struck, two re-deferred, one
  opened; count.
- Stack risk (first hour, STOP on any surprise, §8): `extract(hour from
  timestamp)` and `atan2`/`acos(-1)` on DuckDB; a dbt unit test overriding
  vars per case (`overrides: vars:`); a unit test over a model with three
  `given` inputs (`stg_events`, `stg_prompts`, `dim_user` source) — the
  Phase 4 shape had two; `round()` on the atan2 result at exactly 6 places
  for the golden. `%` in DuckDB's compiled SQL from a macro body is fine —
  the denylist reads models only.

## Out of scope (deferred, recorded)

- The write-back of `scores_send_time` to `send_schedule` and its `tz`
  join — Phase 8 (§2.9).
- Simulation under the recommended schedule, `docs/RESULTS.md` — Phase 6.
- A frozen `medium` — not planned; BACKLOG rows keep "first frozen profile"
  triggers.
- A mutation operator for `order by` keys in SQL window functions —
  BACKLOG row opened this phase (invariant 7's gap).
- Per-user send times outside the band — §5 non-goal.
