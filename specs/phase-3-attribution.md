# Phase 3 — Attribution ⭐ checkpoint (APPROVED 2026-08-25 — implemented, in review)

Contract for the `phase-3-attribution` branch. Source: `docs/PHASES.md`
Phase 3. Depends on Phase 2 merged (PR #4, `07dfb51`).

**Status: APPROVED 2026-08-25; implemented on `phase-3-attribution`.** No new dependencies:
Phase 3 has no allowlist entry; `eval/` uses `duckdb` (Phase 2) and the
standard library only. A need for any package is a STOP-and-ask.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 2 shipped, and the five carry-overs
due this phase. Items marked **design change** need approval before any
implementation; the rest are facts the spec pins.

1. **Skew is a gate on the clock evidence, evaluated before rules 2–4** —
   *design change* (ARCHITECTURE §2.5 wording). §2.5 lists `unattributed`
   fifth, "everything else (skew beyond bound, …)". But the skew injector
   shifts only the CLIENT-side events of a prompt (`prompt_opened`,
   `capture_started`, `upload_*`; `generator/generate.py::CLIENT_SIDE`);
   `prompt_delivered` and `response_recorded` are server-stamped and keep
   `client_event_time = server_received_time`. So every one of tiny's six
   skewed prompts carries a `response_recorded` whose two clocks are both
   inside the window — rule 2 matches first and labels it `on_time`, and the
   "first matching rule" reading of §2.5 is wrong by construction. Now: rule
   order is `delivery_fault` → **skew** (`min(upload_delay_seconds)` over the
   prompt's events `< −SKEW_MAX_MIN·60` → `unattributed`) → `on_time` →
   `upload_fault` → `timing_gap` → `unattributed` (residual). `delivery_fault`
   stays first because the receipt is server-stamped and skew cannot forge it.
   Invariant restored: §2.1 "skew beyond ±`SKEW_MAX_MIN` is `unattributed`,
   never guessed" — a rule that reads a skewed clock is a guess. §2.5's list
   gets the skew gate as its own line between 1 and 2, and the §8 gotcha's
   "Phase 3 pins the rule on the negative side" becomes the rule: only
   `upload_delay_seconds < −SKEW_MAX_MIN·60` is skew; a positive delay of any
   size is an upload delay and never `unattributed` on its own. Rejected:
   keeping `unattributed` last and excluding skewed prompts from rules 2–4
   by a predicate in each arm (four copies of the bound, and a survivor
   hides in whichever copy is dropped).
2. **Which delay is compared to the bound.** `stg_events.upload_delay_seconds`
   is per event; the label is per prompt. The prompt's skew signal is the
   minimum delay over its events (`prompt_id` join), so one skewed
   `capture_started` is enough. Events with no `prompt_id` (`app_opened`)
   never enter attribution. Fact, pinned by the skew unit test.
3. **BACKLOG "Mutation sweep has no operator for dbt SQL" — build it** (trigger
   arrived twice; carry-over 1). Two operators, SQL-text only, in
   `scripts/mutate.py`, line shape `dbt/models/attribution/attribution.sql::label
   <op>`: `drop-arm:<n>` deletes the n-th `when … then …` arm of the named
   `case` (the column alias after `end as`), `swap-arms:<i>,<j>` exchanges two
   arms. Killed the same way Python mutations are: the worktree suite runs
   `tests/test_staging.py`'s in-process `dbt build`, so a dbt unit test going
   red is a KILLED line. Each precedence arm gets one `drop-arm` line (its
   own unit test kills it) and each adjacent pair one `swap-arms` line (the
   overlap unit test kills it). Python otherwise unchanged; the `mutations`
   block stays one block. Rejected: re-deferring again (the five-arm `case`
   was the named trigger); a generic `swap-predicate` (which predicate to
   swap is undecidable without parsing SQL — arms are the unit the tests are
   written against).
4. **BACKLOG "`stg_prompts` carries two cohort keys" — the denominator key is
   `prompt_cohort_id`** (carry-over 2). Recommendation: attribution exposes
   `cohort_id := stg_prompts.prompt_cohort_id`, the cohort the notification
   service sent the prompt AS. Phase 4's rate is "how did this cohort's send
   time perform", so the cohort that chose the send hour is the grouping
   key; `dim_user.cohort_id` is the user's assignment at `client_event_time`
   and would move a prompt between cohorts on a later reassignment, changing
   a `final` label's denominator row. Today the two agree on every tiny row;
   a singular test `assert_prompt_cohort_matches_dim` (over `stg_prompts`)
   pins that and turns the first divergence into a red build rather than a
   silent choice. Rejected: `dim_user.cohort_id` (SCD2 on tz only — a cohort
   change today is a new row with no history; Phase 10's Spanner dim may add
   one, and the denominator must not depend on it).
5. **BACKLOG "Staging pins are counts only" — `fixtures/tiny/expected/attribution.csv`
   is the content pin** (carry-over 3) — *design change* (a new writer path).
   `make freeze` is the only writer of `fixtures/`, and it copies
   `data/out/<p>/` whole. So the golden is produced under `data/out/<p>/
   expected/attribution.csv` by `make attribution-golden PROFILE=<p>
   WRITE=yes` (canonical CSV: header, sorted by `prompt_id`, `\n`, no
   quoting needed — ids and labels are `[a-z0-9_-]+`) and reaches
   `fixtures/` only through `make freeze PROFILE=tiny CONFIRM=yes`, which
   re-renders the manifest with the new file. Without `WRITE`, the target
   diffs the built table against `fixtures/<p>/expected/attribution.csv` and
   exits 1 on any differing row (the DONE half). Two consequences the code
   must pin: (a) `make seed`'s self-check compares only the keys the
   generator wrote (`raw/`, `dims/`, `truth/`) — with `expected/` in the
   manifest the current whole-manifest diff would report it `missing` on
   every seed; (b) `freeze` refuses when `data/out/<p>/` lacks a file the
   current manifest lists (a freeze after a bare `seed` would silently drop
   `expected/`). This spec carries `Freeze: fixtures/tiny/MANIFEST.sha256`;
   the raw/dims/truth lines of the manifest are byte-identical before and
   after (a moved hash is a STOP), and `loader/load.py::manifest_drift`
   keeps hashing `raw/` + `dims/` only. Invariant restored: "did it work is a
   diff against a frozen file", with one writer of `fixtures/`. Rejected: a
   second writer that puts `expected/` under `fixtures/` directly (the
   Phase 1 rule exists so no tool can repair its own golden); keeping
   `expected/` out of the manifest (the gate's "fixture file changed without
   its manifest" check would then never see it).
6. **`eval/` is a new top-level package** (carry-over 4). Already in
   `test_truth_isolation.py::EXEMPT` and the Repo map. It reads the built
   DuckDB file and `fixtures/<p>/truth/prompts.jsonl`, writes console only
   (no `docs/RESULTS.md` block until Phase 6). Nothing under `dbt/` may
   `ref`/`source` anything it writes — it writes no table. The golden
   export (item 5) lives in `eval/` too: it reads dbt output and writes
   `data/out/`, which §3.1 allows (eval may not write a table the pipeline
   READS; `expected/` is read by nothing in `dbt/`). §3.1's eval row gains
   "`data/out/<p>/expected/`" in its writes column. Fact, no design change:
   the boundary is unchanged, the row is completed.
7. **The three vars** (carry-over 5): `skew_max_min: 5` (equal to
   `generator/models.py::SKEW_MAX_MIN`; a test asserts equality so the two
   pins cannot drift), `delivery_grace_min: 10` (the generator delivers 5–120
   s after send; a receipt later than grace is a delivery fault even though
   `stg_prompts.delivered_at` is populated), `unattributed_max: 0.10` (tiny
   is 6/140 = 0.043; the profile's `clock_skew_rate` is 0.05). All three
   defaulted in `dbt_project.yml`, lowercase as dbt vars, named here. The
   skew rule is negative-side only (item 1).
8. **PHASES Phase 3 says "`eval` scores labels vs truth" but not where the
   pin lives.** `tests/pins.py::LABEL_ACCURACY` and the per-label truth
   counts (75/34/17/8/6) are the pins; `eval` prints and asserts the same
   numbers. Fact.
9. **`provisional`/`final` status is Phase 7.** §2.5's last paragraph
   describes it; this phase's `attribution` is a full-rebuild table with no
   status column. Recorded as out of scope; no clause moves.
10. **ARCHITECTURE §3 diagram row "attribution — exhaustive label per
    prompt×user"** — the grain is `prompt_id` (one user per prompt,
    `stg_prompts` unique on `prompt_id`); the row stays, the spec says
    "prompt×user = prompt_id" once. Fact.

Items 1 and 5 are the design changes. STOP here for approval; the spec body
(Invariants, Evidence, Pinned decisions, Threat model) follows in the next
commit.

## Why

Phases 1–2 built the evidence (three clocks, delivery receipts, one row per
prompt); nothing yet says what the evidence MEANS. Phase 3 is the checkpoint
the core risk hangs on (PHASES): if the five-label `case` cannot recover the
generator's assigned causes from the events alone, the marts, the model and
the simulation downstream measure nothing. It is a phase and not a fix PR
because it adds a dbt layer, a golden fixture, the first truth reader and the
first SQL mutation operator — four surfaces, one contract.

## The central constraint

**`fixtures/tiny/{raw,dims,truth}` do not move; `expected/` is added once.**
`Freeze: fixtures/tiny/MANIFEST.sha256` — the re-freeze adds exactly one
line (`expected/attribution.csv`); every existing line is byte-identical
(reconciliation item 5; a moved raw/dims/truth hash is a STOP).

## DONE command

```
make review-gate SPEC=specs/phase-3-attribution.md && make dbt-build PROFILE=tiny && make attribution-golden PROFILE=tiny && make eval PROFILE=tiny
```

- `make review-gate SPEC=…` — offline suite (the attribution unit and
  singular tests through the in-process `dbt build`, the label/count pins,
  the accuracy pin via `eval`, the seed/freeze scope tests, the Makefile
  origin tests, truth isolation, conventions), ruff, check-docs, Evidence
  ids, Record-updates files, the `Freeze:` declaration against the diff.
- `make dbt-build PROFILE=tiny` — the live gate: 3 models, every data, unit
  and singular test, including `accepted_values` on `label` and the
  `unattributed` bound; prints `dbt-build OK: tiny/duckdb`.
- `make attribution-golden PROFILE=tiny` — the built table vs
  `fixtures/tiny/expected/attribution.csv`; prints
  `attribution-golden OK: tiny, 140 rows, 0 differ`; exit 1 otherwise.
- `make eval PROFILE=tiny` — label accuracy vs `truth/prompts.jsonl`; prints
  `eval OK: tiny, accuracy 1.000 (pin 1.000), 140 prompts`; exit 1 below the pin.

## Done-when

1. **Golden diff empty.** `make attribution-golden PROFILE=tiny` reports 0
   differing rows against the frozen `expected/attribution.csv`, sorted by
   `prompt_id`. *Evidence: row 1.*
2. **Accuracy ≥ pin.** `eval` scores every prompt's label against truth and
   reproduces `tests/pins.py::LABEL_ACCURACY`; the per-label counts equal
   `TRUTH_LABEL_COUNTS`. *Evidence: row 2.*
3. **`unattributed` bounded.** Share ≤ `unattributed_max` as a dbt singular
   test; the tiny share is pinned. *Evidence: row 3.*
4. **Exactly one of five per prompt.** `accepted_values` + `not_null` on
   `label`; `count(attribution) = count(stg_prompts)`; `prompt_id` unique.
   *Evidence: row 4.*
5. **Precedence is tested per arm and per adjacent overlap** — one dbt unit
   test per rule, one per adjacent pair, and the SQL mutation sweep kills
   every `drop-arm` / `swap-arms` line (five drops, three swaps; the 4–5
   swap is an equivalent mutant, see the block). *Evidence: row 5.*
6. **`expected/` lands through `freeze` alone; seed and freeze stay honest.**
   `make seed PROFILE=tiny` still prints `manifest match` with `expected/` in
   the manifest; `freeze` refuses when `data/out/<p>/` lacks a manifest-listed
   file. *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `make attribution-golden PROFILE=tiny` → `attribution-golden OK: tiny, 140 rows, 0 differ`; `tests/test_attribution.py::test_golden_matches_fixture`; `tests/test_eval.py::test_golden_reports_a_planted_difference` (a changed label in a tmp copy → 1 differ, exit 1) |
| 2 | `make eval PROFILE=tiny` → `eval OK: tiny, accuracy 1.000 (pin 1.000), 140 prompts`; `tests/test_eval.py::test_label_accuracy_matches_pin`; `tests/test_eval.py::test_truth_label_counts_match_pin`; `tests/test_eval.py::test_accuracy_drops_when_a_label_is_flipped` |
| 3 | dbt singular `dbt/tests/assert_unattributed_share_bounded.sql` (in `make dbt-build`); `tests/test_attribution.py::test_unattributed_share_matches_pin` |
| 4 | `dbt/models/attribution/schema.yml` (`accepted_values`, `not_null`, `unique`); `dbt/tests/assert_one_label_per_prompt.sql`; `tests/test_attribution.py::test_label_counts_match_pin` (sum = 140); `tests/test_dbt_conventions.py::test_schema_label_values_equal_the_contract` (the yml list == `Cause`) |
| 5 | unit tests in `dbt/models/attribution/schema.yml` (names in Invariants 2); `make mutate SPEC=specs/phase-3-attribution.md` → every SQL line `KILLED`; `tests/test_review_tools.py::test_drop_arm_removes_the_named_arm`, `::test_swap_arms_exchanges_two_arms`, `::test_sql_operator_refuses_unknown_case_or_arm` |
| 6 | `make seed PROFILE=tiny` → `seed OK: … manifest match`; `tests/test_generator.py::test_seed_self_check_ignores_expected_keys`; `tests/test_generator.py::test_freeze_refuses_when_output_lacks_a_manifest_file`; `make review-gate SPEC=…` → `PASS fixtures: fixtures/tiny/ re-frozen as the spec declares`; `tests/test_fixture.py::test_raw_dims_truth_hashes_are_the_phase_1_hashes` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Exhaustive-exclusive.** For all `prompt_id` in `stg_prompts`, exactly one row in `attribution` and its `label` is one of the five. | `assert_one_label_per_prompt.sql` (counts equal, no null); `accepted_values`; `unique`; `tests/test_attribution.py::test_label_counts_match_pin` |
| 2. **Precedence.** For all prompts, the label is the FIRST matching rule in the amended §2.5 order: delivery_fault → skew → on_time → upload_fault → timing_gap → unattributed. | per-arm unit tests `attribution_delivery_fault_no_receipt`, `attribution_delivery_fault_receipt_after_grace`, `attribution_skew_negative_delay`, `attribution_on_time`, `attribution_upload_fault_received_after_window`, `attribution_upload_fault_failed_chain`, `attribution_timing_gap`, `attribution_residual_is_unattributed` (capture without upload or response); overlap tests `attribution_delivery_fault_beats_everything` (no receipt, yet a response in window and a skewed event), `attribution_skew_beats_on_time` (in-window response, one skewed capture), `attribution_on_time_beats_upload_fault` (in-window response after an `upload_failed`), `attribution_upload_fault_beats_timing_gap` (`upload_failed` with no capture inside the window); `make mutate` `drop-arm` / `swap-arms` lines |
| 3. **Skew is negative-only.** For all prompts, `unattributed`-by-skew iff `min(upload_delay_seconds)` over the prompt's events `< −skew_max_min·60`; no positive delay of any size is skew. | `attribution_skew_negative_delay` (−301 s → unattributed; −300 s and +100000 s → not) ; `tests/test_attribution.py::test_skew_var_equals_generator_pin` |
| 4. **Bound.** For all builds on tiny, `unattributed` share ≤ `unattributed_max`. | `assert_unattributed_share_bounded.sql`; `tests/test_attribution.py::test_unattributed_share_matches_pin` |
| 5. **Golden.** For all builds on tiny, the table sorted by `(prompt_id, user_id)` equals `expected/attribution.csv` row for row; a difference is reported by row, never masked. | `test_golden_matches_fixture`; `test_golden_reports_a_planted_difference`; mutation `eval/golden.py::diff_rows constant-return:[]` |
| 6. **Accuracy.** For all prompts, `eval` compares the built label to `truth/prompts.jsonl` on `prompt_id`; the tiny accuracy equals the pin and a flipped label lowers it. | `test_label_accuracy_matches_pin`; `test_accuracy_drops_when_a_label_is_flipped`; mutation `eval/score.py::label_accuracy constant-return:1.0` |
| 7. **Determinism.** For all builds, `attribution` is a function of raw + dims + vars: byte-identical across two builds and under `TZ=Asia/Tokyo`; no clock call. | `tests/test_attribution.py::test_two_builds_give_the_same_golden`; `::test_build_under_a_non_utc_host_zone_is_identical`; `test_dbt_conventions.py::test_no_clock_call_in_any_model_or_macro` |
| 8. **Seam.** For all dialect-divergent expressions, the five existing macros; no sixth. | `test_dbt_conventions.py::test_exactly_five_dispatch_macros` |
| 9. **Truth isolation.** For all files under `dbt/`, no mention of truth; `eval/` is the only reader. | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth` |
| 10. **Cohort key.** For all `stg_prompts` rows on tiny, `prompt_cohort_id = cohort_id`; `attribution.cohort_id` is the prompt's. | `dbt/tests/assert_prompt_cohort_matches_dim.sql`; `attribution_cohort_is_the_prompts` unit test (dim and event differ → the event's wins) |
| 11. **Freeze scope.** For all seeds, the self-check covers only generator-written keys; for all freezes, a manifest-listed file missing from `data/out/<p>/` is a refusal. | `test_seed_self_check_ignores_expected_keys`; `test_freeze_refuses_when_output_lacks_a_manifest_file`; mutation `generator/cli.py::missing_from_output constant-return:[]` |

```mutations
eval/score.py::label_accuracy                                   constant-return:1.0
eval/golden.py::diff_rows                                       constant-return:[]
eval/golden.py::export_rows                                     swap-sort-key
generator/cli.py::missing_from_output                           constant-return:[]
generator/cli.py::generated_keys                                constant-return:{}
dbt/models/attribution/attribution.sql::label                   drop-arm:1
dbt/models/attribution/attribution.sql::label                   drop-arm:2
dbt/models/attribution/attribution.sql::label                   drop-arm:3
dbt/models/attribution/attribution.sql::label                   drop-arm:4
dbt/models/attribution/attribution.sql::label                   drop-arm:5
dbt/models/attribution/attribution.sql::label                   swap-arms:1,2
dbt/models/attribution/attribution.sql::label                   swap-arms:2,3
dbt/models/attribution/attribution.sql::label                   swap-arms:3,4
```

(`drop-arm:5` turns `timing_gap` into the `else` → `unattributed`; the arm
count is five `when`s plus `else`, so `drop-arm:6` is a refused line, tested.
`swap-arms:4,5` is NOT listed: the first sweep showed it SURVIVED because
arms 4 and 5 are disjoint by construction — `timing_gap` requires no upload
event and both `upload_fault` clauses imply one — so the swap is an
equivalent mutant; the overlap is still pinned by the unit test
`attribution_upload_fault_beats_timing_gap`, which proves the upload chain
is action.)

## Review round 1 fixes (2026-08-25)

`bool_or` (7×) was inline dialect SQL in `attribution.sql` — rewritten as the
ANSI `max(case when … then 1 else 0 end) = 1`, seam untouched (five macros);
`tests/test_dbt_conventions.py::test_no_dialect_function_in_any_model` greps
`dbt/models/**/*.sql` for a denylist so the next inline dialect call is a red
test. ARCHITECTURE §2.5 rule 2 now carries the `· 60` (seconds). The golden's
tie-break is named everywhere its order is stated.

## Pinned decisions (do not re-litigate)

- **One `case` over per-prompt evidence, arms in the amended §2.5 order with
  the skew gate second** — satisfies invariants 2, 3. Evidence is
  pre-aggregated once per `prompt_id` in CTEs (`delivered_in_grace`,
  `min_upload_delay_seconds`, `response_on_time`,
  `captured_in_window_received_late`, `has_response`,
  `has_response_in_window`, `has_capture_in_window`, `has_upload_failed`,
  `has_upload_event`) and exposed as columns, so every arm reads booleans
  and the unit tests assert the evidence as well as the label. Rule 4a reads
  the device clock off `capture_started` / `upload_*` — `response_recorded`
  is backend-stamped with equal clocks (found on the first build: 5 of 8
  upload faults fell to the residual under the literal reading; DECISIONS
  Phase 3, ARCHITECTURE §8).
  Window = `[sent_at, sent_at + window_minutes)` half-open; grace = `delivered_at
  − sent_at ≤ delivery_grace_min·60` s via `timestamp_diff`. Rejected:
  nested `case`s per rule (the arm is the mutation unit; nesting hides one).
- **The golden is `prompt_id,user_id,cohort_id,label`**, canonical CSV
  (header, `\n`, sorted by `(prompt_id, user_id)` — `prompt_id` is unique,
  `user_id` names the tie-break) — satisfies invariant 5. Evidence
  columns are not in the golden (a wording change to a boolean would move
  the file without moving a label). Closes BACKLOG "Staging pins are counts
  only": the label is a function of every staged column that matters.
- **`expected/` reaches `fixtures/` only through `make freeze`**; `seed`
  self-checks `raw/`, `dims/`, `truth/` keys only; `freeze` refuses a
  manifest-listed file missing from `data/out/<p>/` — satisfies invariant 11
  (reconciliation item 5).
- **`attribution.cohort_id := stg_prompts.prompt_cohort_id`**, with the
  equality singular test — satisfies invariant 10 (reconciliation item 4).
- **`eval/` is console-only**: `eval/cli.py` (`score`, `golden`) validates
  `PROFILE` with `loader.cli.validate_name`, derives `data/<p>.duckdb` and
  the fixture paths, reads `truth/prompts.jsonl` (the ONLY reader), prints
  one `OK`/`FAIL` line, exits 1 below the pin. Pins live in `tests/pins.py`
  (`LABEL_ACCURACY`, `TRUTH_LABEL_COUNTS`, `ATTRIBUTION_LABEL_COUNTS`,
  `UNATTRIBUTED_SHARE`), read off the first green run. Rejected: a
  `docs/RESULTS.md` block now (Phase 6 owns the generated block).
- **Two SQL operators in `scripts/mutate.py`, `drop-arm:<n>` and
  `swap-arms:<i>,<j>`**, addressed `path.sql::<alias>` where `<alias>` is the
  `end as <alias>` of the target `case`; text-level over the `when … then …`
  arms of that one `case`; a missing alias, arm index out of range, or a
  file outside `dbt/models/` is a refused line — satisfies invariant 2
  (reconciliation item 3). Closes BACKLOG "Mutation sweep has no operator
  for dbt SQL".

## Scope (files)

- `dbt/models/attribution/attribution.sql`, `schema.yml`; `dbt/dbt_project.yml`
  (vars `skew_max_min`, `delivery_grace_min`, `unattributed_max`; the
  `attribution` folder config)
- `dbt/tests/assert_one_label_per_prompt.sql`,
  `assert_unattributed_share_bounded.sql`, `assert_prompt_cohort_matches_dim.sql`
- `eval/__init__.py`, `eval/cli.py`, `eval/score.py`, `eval/golden.py`
- `generator/cli.py` (seed scope, freeze refusal); `Makefile`
  (`attribution-golden`, `eval`; `WRITE` in `unexport`)
- `scripts/mutate.py` (two SQL operators); `scripts/check_docs.py` (the
  `unexport` trace token, a `drop-arm` trace)
- `fixtures/tiny/expected/attribution.csv`, `fixtures/tiny/MANIFEST.sha256`
- `tests/pins.py`, `tests/test_attribution.py`, `tests/test_eval.py`,
  `tests/test_generator.py`, `tests/test_fixture.py`, `tests/test_makefile.py`,
  `tests/test_review_tools.py`, `tests/test_dbt_conventions.py`
- records below

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 3 appendix (skew gate, golden via freeze,
      cohort key, SQL operators, vars); "Process" mutation entry updated
      (SQL now covered for `case` arms)
- [ ] `docs/PHASES.md` — Phase 3 "Delivered" paragraph
- [ ] `CLAUDE.md` — Current status; Commands (`attribution-golden`, `eval`,
      `mutate` operator list, `seed`/`freeze` scope); Repo map (`eval/`,
      `dbt/models/attribution`); Event model facts (label precedence with the
      skew gate); BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §2.5 skew line; §3.1 eval writes column; §8 if
      a surprise lands
- [ ] `BACKLOG.md` — close "Mutation sweep has no operator for dbt SQL",
      "`stg_prompts` carries two cohort keys", "Staging pins are counts only";
      open any deferred finding
- [ ] Spec amendments — none (no later spec exists)
- [ ] docs/RESULTS.md / METRICS.md / DEPLOYMENT.md — none
- [ ] README — none (no README yet)

## Threat model (REQUIRED)

Both new targets take `PROFILE` in the settled shape (one Python process,
`[a-z0-9_]+`, every path derived, `$(call _Q,$(value VAR))`, `unexport`).
`attribution-golden` also takes `WRITE`: only the literal `yes` writes
`data/out/<p>/expected/attribution.csv` (gitignored; never `fixtures/`);
anything else is check mode. No delete, no cloud, no input. Residual:
`WRITE=yes` from the environment writes `data/out/` — a mistake with no
committed consequence, the same class as `PROFILE` from the environment.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make attribution-golden PROFILE= [WRITE=yes]` | refused (`attribution-golden: refused — bad profile name`) | refused, no path derived | one literal, refused | reaches Python, validated the same; `WRITE` from env is honoured (residual, stated) | n/a — no CONFIRM; `WRITE` must equal `yes` | `tests/test_makefile.py::test_golden_and_eval_pass_profile_as_one_literal`; `tests/test_eval.py::test_golden_write_only_on_literal_yes` |
| `make eval PROFILE=` | refused | refused | one literal, refused | validated the same | n/a | `tests/test_makefile.py::test_golden_and_eval_pass_profile_as_one_literal` |

## Review & stack risk

- **code-reviewer** (triggered — dbt models/tests, `eval/`, `generator/cli.py`,
  `scripts/mutate.py`, Makefile): precedence order vs amended §2.5, no clock,
  five macros, no truth under `dbt/`, `eval` writes no table, the golden's sort
  key, seed/freeze scope.
- **security-reviewer** (triggered — `freeze` refusal path changes and a
  Makefile target taking a variable): the `WRITE` residual, no write under
  `fixtures/` outside `freeze`.
- **functionality-tester** (triggered): DONE command; a planted golden
  difference; a flipped label; each `drop-arm`/`swap-arms` line KILLED;
  `seed` still `manifest match`; `freeze` refusal with a missing file.
- **coherence-auditor** at exit: §2.5 lists the skew gate; §3.1 eval row;
  CLAUDE.md Commands names `attribution-golden` + `eval`; three BACKLOG rows
  struck; BACKLOG count; PHASES Phase 3 Delivered.
- Stack risk: dbt unit tests over a model with a `ref('stg_prompts')` +
  `ref('stg_events')` pair (two `given` inputs — verified in Phase 2 for
  sources, not yet for two refs); `bool_or` / `min` over a possibly empty
  group on DuckDB (a prompt with no events besides `prompt_sent` must yield
  NULLs, not drop the row — left joins from `stg_prompts`). STOP and log
  under §8 on any surprise.

## Out of scope (deferred, recorded)

- `provisional`/`final` status and the lookback — Phase 7 (PHASES).
- `medium` profile golden — not frozen (PHASES Phase 1: defined, not
  committed); tiny only.
- A SQL operator beyond `case` arms (`drop-where`, predicate swaps) —
  BACKLOG row if a survivor class is found that arms cannot express.
- Staging row-hash pins — closed by the golden (a staging content change that
  moves no label is by definition not a Phase 3 regression); revisit at
  Phase 4 if a mart needs a staged column the label does not.
