# Phase 5 — Send-time model as a dbt model (PROPOSED)

Contract for the `phase-5-send-time` branch. Source: `docs/PHASES.md` Phase 5.
Depends on Phase 4 merged (PR #6, `f39a31b`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
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
   both profiles) holds the most pooled opens; ties → the smallest `h`
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
   per row, §2.9); on tiny `2026-01-12 02:31:53`, medium `2026-02-04
   07:58:00`. `model_version` := `var('model_version')`, literal `'v1'`,
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
   circular distance to its cohort moment ≤ `max_user_shift_min`) and
   `assert_one_score_per_user.sql`; the dialect denylist gains `%`.
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
