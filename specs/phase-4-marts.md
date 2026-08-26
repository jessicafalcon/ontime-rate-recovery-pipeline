# Phase 4 — On-time marts and metric definitions (APPROVED 2026-08-25 — implemented, in review)

Contract for the `phase-4-marts` branch. Source: `docs/PHASES.md` Phase 4.
Depends on Phase 3 merged (PR #5, `38c0f36`).

**Status: APPROVED 2026-08-25; implemented on `phase-4-marts`.** No new dependencies:
Phase 4 has no allowlist entry; the marts are dbt SQL, `make report` uses
`duckdb` (Phase 2) and the standard library. A need for any package is a
STOP-and-ask.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 3 shipped, and the carry-overs due
this phase. Items marked **design change** or **re-freeze** need approval
before any implementation; the rest are facts the spec pins. Numbers are
read off the built tiny (`data/tiny.duckdb` after `make dbt-build
PROFILE=tiny` on `main`).

1. **BACKLOG "The denominator contract's sum must exclude `delivery_fault`"
   — wording, not a design change** (carry-over 1, the row due this phase).
   The five labels partition every `stg_prompts` row (Phase 3 invariant 1),
   and `delivery_fault` is by construction the undelivered prompt (§2.5 rule
   1), so `sum(all five) = prompts_delivered + delivery_fault` on every
   cohort-day and the literal `sum(label counts) == prompts_delivered` in
   CLAUDE.md and the PHASES Done-when is false whenever a delivery fault
   exists (13 of tiny's 14 cohort-days). The identity this phase pins as the
   dbt test is `on_time + upload_fault + timing_gap + unattributed ==
   prompts_delivered` per `(cohort_id, prompt_date)`, with
   `prompts_delivered := count(delivered_in_grace)` — the same predicate rule
   1 negates, so the two cannot drift — and `delivery_fault` counted
   separately (`prompts_sent = prompts_delivered + delivery_fault` is the
   second half of the same test). Why wording: no data structure, write path
   or writer moves — the contract's intent (§2.6 "never user-days, or
   delivery faults vanish"; §4 invariant 6 "the denominator is
   `prompts_delivered`") is unchanged, the sentence just named the wrong sum
   before the table that would have caught it existed. CLAUDE.md's
   "Denominator contract" sentence and the PHASES Done-when are corrected in
   this branch's record updates; DECISIONS gets the one-line why. Rejected:
   reading the sentence as "sum over delivered labels" silently (the next
   reader would write the five-way test and watch it fail).
2. **`docs/METRICS.md` is a new LIVING doc, one definition per metric.**
   `scripts/check_docs.py` treats every `docs/*.md` outside the three plans
   as LIVING (links, anchors and every `make <target>` it names are checked),
   so the file is covered the moment it exists. Each metric gets exactly one
   block — grain, numerator, denominator, null policy, the dbt test that
   pins it — and `dbt/models/marts/schema.yml` descriptions point at the
   METRICS anchor rather than restating (a restatement is a second
   definition that drifts). A `TRACES` token for the partition test's file
   name keeps the doc honest about the test's existence. Fact.
3. **`ontime_retention` is "retained-at-28d" (§2.6) and tiny is 7 days —
   mismatch surfaced; recommendation: keep 28 as the definition, make
   "not observable" a NULL, never a false.** tiny's prompts span
   2026-01-05 → 01-11 local, its last event is 01-13, so no user's 28th day
   is inside the data and a boolean `retained_at_28d` would read `false` for
   all 20 — an all-churned cohort that is an artefact of the window, exactly
   the finding §7 says synthetic data cannot make. The mart is per user
   (grain `user_id`): `anchor_date` = the user's first `prompt_date`,
   `ontime_rate` = the user's on-time share over its delivered prompts in
   the trailing `retention_days` window from the anchor, `observed_through`
   = the data-derived `max(client_event_time_local)` over all events
   (`computed_as_of` per the determinism policy — never the clock),
   `retained` = `true` if an organic `app_opened` (the reachability signal,
   the only event with no `prompt_id`) falls on or after `anchor_date +
   retention_days`, `false` if `observed_through ≥ anchor_date +
   retention_days` and none does, and **NULL when the window has not
   closed**. Var `retention_days: 28`, defaulted in `dbt_project.yml`, named
   here. On tiny every `retained` is NULL and every `ontime_rate` is a
   number; the unit test builds a synthetic 30-day input where one user is
   retained, one is churned and one is unobservable, so the three states are
   proven without the fixture. The doc line (METRICS + schema.yml) is §7's:
   descriptive only, the retention gap in synthetic data is a designed
   property, never a finding. Rejected: `retention_days` defaulted to
   something tiny can close (the definition would bend to the fixture; a
   `medium` run would then compare a 7-day number to the plan's 28); scoping
   the mart to `medium` and stopping (`medium` is not frozen — PHASES Phase
   1 — so the mart would ship with no test data at all). Fact plus one var;
   no design change.
4. **Grain is local: `prompt_date := cast(sent_at_local as date)`** —
   recommendation, one paragraph. Cohorts are defined by the local send hour
   (`c-morning` 08, `c-evening` 20 — `generator/profiles/tiny.json`), so
   "how did this cohort's send time do on day D" is a question about the
   user's day, and the UTC date splits one cohort's morning across two rows:
   every Tokyo `c-morning` prompt (08:00 JST = 23:00 UTC the day before)
   would land on the previous UTC date, and on tiny 34 of 140 prompts have
   `cast(sent_at as date) ≠ cast(sent_at_local as date)` — every
   `c-morning` cohort-day's UTC range straddles two dates. Local is also
   what §2.7 partitions by (`prompt_date`) and what §8's Tokyo gotcha
   already warns about. The cast is ANSI `cast(… as date)`; the denylist
   forbids `::` in a model and `sent_at_local` is already computed once in
   staging via `to_local_time`, so no tz logic is repeated in the mart. Unit
   test `ontime_rate_daily_prompt_date_is_local`: one Tokyo prompt at
   `sent_at = 2026-01-04 23:00 UTC`, `sent_at_local = 2026-01-05 08:00` →
   one row dated `2026-01-05`, none dated `2026-01-04`. Where `prompt_date`
   is computed: in the mart, from the attribution column; Phase 7 moves it
   upstream when partitioning needs it on event-level models (a DECISIONS
   note then, not a column now). Rejected: UTC date (splits the cohort's
   moment; the metric would move when a user changes tz — SCD2 already
   handles that in staging). Fact.
5. **`make report` and the rate reading** — three parts.
   - *What it prints, where.* Console only (§3.1 eval row; `docs/RESULTS.md`
     is Phase 6's): one line `report OK: tiny, 14 cohort-days, 0 differ,
     ontime_rate 0.610 (pin 0.610)` — the golden diff of `ontime_rate_daily`
     against `fixtures/<p>/expected/ontime_rate_daily.csv` plus the overall
     on-time rate `sum(on_time) / sum(prompts_delivered)` over the mart
     (tiny: 75 / 123 = 0.6098), asserted against `tests/pins.py::
     ONTIME_RATE`; exit 1 on any differing row or a rate off the pin. `make
     report PROFILE=<p> WRITE=yes` writes `data/out/<p>/expected/
     ontime_rate_daily.csv` instead, the `attribution-golden` shape.
   - *The golden and its writer* — **re-freeze (needs approval)**. The
     golden is `cohort_id,prompt_date,prompts_sent,prompts_delivered,
     on_time,upload_fault,timing_gap,unattributed,delivery_fault,
     ontime_rate`, canonical CSV sorted by `(cohort_id, prompt_date)` — the
     key is unique, so the tie-break is the key itself. `eval/golden.py` is
     generalised, not copied: `export_rows(db, relation, columns, key)`,
     `render(rows, columns)`, `parse(text, columns)`, `diff_rows(built,
     frozen, key_width)` take the table and column list as arguments; the
     attribution golden becomes one call with its existing constants, byte-
     identical output (`fixtures/tiny/expected/attribution.csv` does not
     move — `tests/test_fixture.py` pins every existing hash). One writer
     path: `WRITE=yes` → `data/out/<p>/expected/` → `make freeze PROFILE=tiny
     CONFIRM=yes`, which adds exactly one manifest line. This spec carries
     `Freeze: fixtures/tiny/MANIFEST.sha256`; the fourteen existing lines
     (raw, dims, truth, `expected/attribution.csv`) are byte-identical before
     and after — a moved hash is a STOP. Rejected: a second golden module
     (two `COLUMNS`, two sort keys, two diff loops to keep in step); a second
     writer of `fixtures/` (Phase 3 decision, still in force).
   - *The rate on a day with nothing delivered* — wording in PHASES, the
     reading pinned here. `ontime_rate := safe_divide(on_time,
     prompts_delivered)`, §2.6's denominator and §4 invariant 6. Its contract
     is NULL on a zero denominator, so a cohort-day with **zero delivered
     prompts** (the literal "delivery-fault-only day") shows `ontime_rate`
     NULL with `prompts_sent`, `delivery_fault` and `prompts_delivered = 0`
     populated — the day is in the table, its faults are counted, and the
     rate is honestly undefined. A cohort-day with **delivered prompts and
     zero on time** shows `0`, not NULL — that is the case the Done-when
     means (a rate that goes missing when everything failed would be the
     "delivery faults vanish" failure §2.6 names, in the other direction).
     `on_time / prompts_sent` is rejected: it changes the denominator §4.6
     fixes and would make a delivery outage look like a timing problem. The
     PHASES clause becomes "a cohort-day with delivered prompts and none on
     time shows on-time rate 0, not null; a cohort-day with nothing delivered
     shows NULL with its counts populated". Neither case exists on tiny
     (every cohort-day has ≥ 6 delivered and ≥ 3 on time), so both are unit
     tests: `ontime_rate_daily_zero_on_time_is_zero` and
     `ontime_rate_daily_nothing_delivered_is_null` — the first test anywhere
     that renders `safe_divide`'s DuckDB body (BACKLOG-style "no caller"
     seam, now called).
6. **Other drift, facts.** (a) CLAUDE.md Repo map says `models/attribution
   (Phase 3; later marts, features, scores)` — `models/marts` lands, the
   line is updated. (b) `tests/test_dbt_conventions.py::
   test_every_model_has_description_and_a_test` iterates `("staging",
   "attribution")` — gains `"marts"`; `SINGULAR` gains the partition test.
   (c) ARCHITECTURE §3's diagram row for marts already matches; §2.6 gains
   the `prompt_date` grain sentence, the NULL policy and the `retention_days`
   var. (d) The struck BACKLOG row "Staging pins are counts only" asked at
   Phase 4 whether a mart reads a staged column no label does:
   `ontime_retention` reads `stg_events.client_event_time_local` for
   `app_opened` (no label reads organic opens) — the retention golden is
   not frozen (item 3: all-NULL on tiny pins nothing), so the answer is one
   pin, `tests/pins.py::ORGANIC_OPEN_ROWS` (count of staged `app_opened`),
   recorded on the row's revisit note rather than a staging row-hash.
   (e) §2.6's "counts per label" is the five counts plus `prompts_sent` and
   `prompts_delivered` — ten columns, listed in item 5.

Items 5 (the re-freeze adding `expected/ontime_rate_daily.csv`) is the one
approval gate; items 1 and 5's rate reading rewrite record sentences. STOP
here for approval; the spec body (Invariants, Evidence, Pinned decisions,
Threat model) follows in the next commit.

## Why

Phase 3 says what every prompt's evidence MEANS; nothing yet says how the
product is doing. The marts turn 140 labels into the number the project
exists to move — the on-time rate per cohort-day — and `docs/METRICS.md`
makes that number mean one thing everywhere it is quoted (the model in
Phase 5 optimises it, the simulation in Phase 6 reports it, the A/B design
pre-registers it). It is a phase and not a fix PR because it adds a dbt
layer, a second golden, the first caller of `safe_divide` and the metric
definitions every later phase cites.

## The central constraint

**`fixtures/tiny/{raw,dims,truth,expected/attribution.csv}` do not move;
`expected/ontime_rate_daily.csv` is added once.** `Freeze:
fixtures/tiny/MANIFEST.sha256` — the re-freeze adds exactly one line; every
one of the fourteen existing lines is byte-identical (reconciliation item 5;
a moved hash is a STOP). The attribution golden is regenerated through the
generalised `eval/golden.py` and must render byte-identically.

## DONE command

```
make review-gate SPEC=specs/phase-4-marts.md && make dbt-build PROFILE=tiny && make report PROFILE=tiny
```

- `make review-gate SPEC=…` — offline suite (mart unit, data and singular
  tests through the in-process `dbt build`; the rate, cohort-day and
  organic-open pins; the generalised golden on both tables; the Makefile
  literal tests; truth isolation; conventions), ruff, check-docs (METRICS.md
  is LIVING), Evidence ids, Record-updates files, the `Freeze:` declaration
  against the diff.
- `make dbt-build PROFILE=tiny` — the live gate: 5 models, every data, unit
  and singular test including the cohort-day partition test; prints
  `dbt-build OK: tiny/duckdb`.
- `make report PROFILE=tiny` — the mart vs `fixtures/tiny/expected/
  ontime_rate_daily.csv` and the overall rate vs the pin; prints `report OK:
  tiny, 14 cohort-days, 0 differ, ontime_rate 0.610 (pin 0.610)`; exit 1 on
  a differing row or a rate off the pin.

## Done-when

1. **Partition.** For every cohort-day on tiny, `on_time + upload_fault +
   timing_gap + unattributed == prompts_delivered` and `prompts_delivered +
   delivery_fault == prompts_sent`, as a dbt singular test; a unit test with
   all five labels on one cohort-day proves the sums and that
   `delivery_fault` is counted, not summed. *Evidence: row 1.*
2. **Golden and rate.** `make report PROFILE=tiny` reports 0 differing rows
   against `expected/ontime_rate_daily.csv` and an overall rate equal to
   `tests/pins.py::ONTIME_RATE` (75 / 123). *Evidence: row 2.*
3. **Zero, not null — and null, not zero.** A cohort-day with delivered
   prompts and none on time has `ontime_rate = 0`; a cohort-day with nothing
   delivered has `ontime_rate` NULL and its counts populated (reconciliation
   item 5; the first rendering of `safe_divide`). *Evidence: row 3.*
4. **Grain.** `prompt_date` is the local date; `(cohort_id, prompt_date)`
   is unique; a user with two prompts on one day counts 2 (never user-days).
   *Evidence: row 4.*
5. **Retention, descriptive.** `ontime_retention` is one row per user with
   `retained` in exactly the three states of reconciliation item 3 (true /
   false / NULL-unobservable) at `retention_days`, data-derived
   `observed_through`, byte-identical across two builds and under a non-UTC
   host zone; on tiny every `retained` is NULL. *Evidence: row 5.*
6. **One definition, one writer.** Every metric column of both marts has
   exactly one block in `docs/METRICS.md` and `schema.yml` links to it;
   `expected/ontime_rate_daily.csv` reaches `fixtures/` through `make
   freeze` alone and the attribution golden is byte-identical through the
   generalised writer. *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `dbt/tests/assert_cohort_day_partition.sql` (in `make dbt-build`; zero rows where either identity fails); unit test `ontime_rate_daily_five_labels_one_day` in `dbt/models/marts/schema.yml`; `tests/test_marts.py::test_partition_holds_on_every_tiny_cohort_day` (the identity recomputed from `attribution` in Python and compared row for row) |
| 2 | `make report PROFILE=tiny` → `report OK: tiny, 14 cohort-days, 0 differ, ontime_rate 0.610 (pin 0.610)`; `tests/test_marts.py::test_daily_golden_matches_fixture` (byte-identical render); `tests/test_marts.py::test_overall_rate_matches_pin`; `tests/test_eval.py::test_report_reports_a_planted_difference` (one changed count in a tmp copy → 1 differ, exit 1); `tests/test_eval.py::test_report_fails_when_the_rate_is_off_the_pin` |
| 3 | unit tests `ontime_rate_daily_zero_on_time_is_zero`, `ontime_rate_daily_nothing_delivered_is_null`; `tests/test_dbt_conventions.py::test_safe_divide_is_called_by_a_model` (the macro name appears in `models/marts/`; its DuckDB body renders in the built SQL under `dbt/target/`) |
| 4 | unit tests `ontime_rate_daily_prompt_date_is_local` (23:00 UTC / 08:00 JST → the local day), `ontime_rate_daily_counts_prompts_not_user_days` (one user, two prompts, one day → `prompts_sent = 2`); `schema.yml` `unique` on the combination of `cohort_id, prompt_date` (dbt `unique_combination`-free: a singular test `assert_cohort_day_key_unique.sql`) + `not_null` on both; `tests/test_marts.py::test_prompt_date_is_local_on_tiny` (34 rows where the UTC date differs, none misplaced) |
| 5 | unit test `ontime_retention_three_states` (30-day synthetic input: retained / churned / unobservable); `tests/test_marts.py::test_retention_is_all_null_on_tiny` (`RETENTION_ROWS = 20`, `retained` NULL on every row, `ontime_rate` never NULL); `tests/test_marts.py::test_two_builds_give_the_same_marts`; `::test_marts_under_a_non_utc_host_zone_are_identical`; `tests/test_dbt_conventions.py::test_no_clock_call_in_any_model_or_macro` |
| 6 | `tests/test_metrics_doc.py::test_every_mart_metric_has_exactly_one_definition` (each metric column of both marts appears as one `### ` heading in METRICS.md and its `schema.yml` description carries the anchor, no restated formula); `make check-docs` → METRICS.md links/targets; `tests/test_attribution.py::test_golden_matches_fixture` (unchanged, now through the generalised writer); `tests/test_fixture.py::test_raw_dims_truth_hashes_are_the_phase_1_hashes` + `::test_phase_3_expected_hash_is_unchanged`; `make review-gate SPEC=…` → `PASS fixtures: fixtures/tiny/ re-frozen as the spec declares`; `tests/test_eval.py::test_report_write_only_on_literal_yes` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Partition.** For all `(cohort_id, prompt_date)`, `on_time + upload_fault + timing_gap + unattributed = prompts_delivered` and `prompts_delivered + delivery_fault = prompts_sent`; `delivery_fault` is counted, never in the delivered sum. | `assert_cohort_day_partition.sql`; unit `ontime_rate_daily_five_labels_one_day` (5 prompts, one per label → delivered 4, sent 5); `test_partition_holds_on_every_tiny_cohort_day` |
| 2. **Denominator.** For all cohort-days, `prompts_delivered` counts delivered prompts (`delivered_in_grace`), never users or user-days: a user with n delivered prompts on a day contributes n. | unit `ontime_rate_daily_counts_prompts_not_user_days`; `assert_cohort_day_partition.sql` (a user-day count breaks identity 1 whenever a user has two prompts) |
| 3. **Zero-not-null.** For all cohort-days with `prompts_delivered > 0`, `ontime_rate` is a number (0 when `on_time = 0`); for all cohort-days with `prompts_delivered = 0`, `ontime_rate` is NULL and every count column is populated. | unit `ontime_rate_daily_zero_on_time_is_zero`; unit `ontime_rate_daily_nothing_delivered_is_null`; `schema.yml` `not_null` on every count column; `test_safe_divide_is_called_by_a_model` |
| 4. **Grain.** For all prompts, the row it counts toward is `(cohort_id, cast(sent_at_local as date))`; each key appears once; a prompt whose UTC date differs from its local date lands on the local one. | unit `ontime_rate_daily_prompt_date_is_local`; `assert_cohort_day_key_unique.sql`; `test_prompt_date_is_local_on_tiny` |
| 5. **Golden.** For all builds on tiny, `ontime_rate_daily` sorted by `(cohort_id, prompt_date)` equals `expected/ontime_rate_daily.csv` row for row and byte for byte; a difference is reported by key, never masked; the attribution golden is byte-identical through the same writer. | `test_daily_golden_matches_fixture`; `test_report_reports_a_planted_difference`; `test_golden_matches_fixture` (attribution); mutations `eval/golden.py::diff_rows constant-return:[]`, `eval/golden.py::export_rows swap-sort-key` |
| 6. **Report.** For all profiles, `make report` prints the overall rate `sum(on_time) / sum(prompts_delivered)` over the mart and exits 1 when it differs from the pin or any row differs; on tiny the rate is `ONTIME_RATE`. | `test_overall_rate_matches_pin`; `test_report_fails_when_the_rate_is_off_the_pin`; mutation `eval/report.py::overall_rate constant-return:1.0` |
| 7. **Retention.** For all users, one row; `retained` is true iff an organic `app_opened` falls on or after `anchor_date + retention_days`, false iff none does AND `observed_through ≥ anchor_date + retention_days`, NULL otherwise; `observed_through` is data-derived; the mart is a function of raw + dims + vars. | unit `ontime_retention_three_states`; `test_retention_is_all_null_on_tiny`; `test_two_builds_give_the_same_marts`; `test_marts_under_a_non_utc_host_zone_are_identical`; mutations `drop-arm:1`, `drop-arm:2` on `ontime_retention.sql::retained` |
| 8. **One definition.** For all metric columns of both marts, exactly one `### ` block in `docs/METRICS.md` (grain, numerator, denominator, null policy, pinning test) and a `schema.yml` description that links, not restates. | `test_every_mart_metric_has_exactly_one_definition`; `make check-docs` |
| 9. **Freeze scope.** For all freezes, `expected/ontime_rate_daily.csv` enters `fixtures/` only via `make freeze`; the fourteen existing manifest lines do not move. | `test_raw_dims_truth_hashes_are_the_phase_1_hashes`; `test_phase_3_expected_hash_is_unchanged`; `test_report_write_only_on_literal_yes`; review-gate `PASS fixtures` |
| 10. **Carried forward.** For all models under `dbt/models/marts/`: no clock call, no inline dialect form, no truth, the five macros and no sixth, a description and a test per model. | `test_no_clock_call_in_any_model_or_macro`; `test_no_dialect_function_in_any_model`; `test_pipeline_dirs_never_mention_truth`; `test_exactly_five_dispatch_macros`; `test_every_model_has_description_and_a_test` (folders gain `marts`) |

```mutations
eval/golden.py::diff_rows                                       constant-return:[]
eval/golden.py::export_rows                                     swap-sort-key
eval/report.py::overall_rate                                    constant-return:1.0
dbt/models/marts/ontime_retention.sql::retained                 drop-arm:1
dbt/models/marts/ontime_retention.sql::retained                 drop-arm:2
```

Equivalent-mutant exclusions, named up front:

- `ontime_retention.sql::retained swap-arms:1,2` — arm 1 is "window not
  closed → NULL", arm 2 is "an open on or after the close → true". An open
  after the close date implies `observed_through` is past it (the open is an
  event, `observed_through` is the max over events), so the arms are
  disjoint and their order unobservable. The three-state unit test still
  pins both.
- `ontime_rate_daily.sql` has no multi-arm `case`: every count is a
  single-arm `sum(case when … then 1 else 0 end)` — the `sum(` wraps the
  `case`, so there is no `end as <alias>` for the operator to address and a
  line naming one is refused (verified: `refusing: no \`end as
  prompts_delivered\``), and `swap-arms` needs two arms. Those columns are
  pinned by the partition singular test, the five-labels unit test and the
  golden (a dropped or mis-aimed count changes a frozen row).
- Both exclusions were run once through `make mutate` on a scratch copy of
  the block: `swap-arms:1,2` → SURVIVED, the single-arm drop → refused.

## Pinned decisions (do not re-litigate)

- **`ontime_rate_daily` = one `group by cohort_id, cast(sent_at_local as
  date)` over `attribution`**, ten columns (`cohort_id, prompt_date,
  prompts_sent, prompts_delivered, on_time, upload_fault, timing_gap,
  unattributed, delivery_fault, ontime_rate`); `prompts_delivered =
  sum(delivered_in_grace)`, `ontime_rate = safe_divide(on_time,
  prompts_delivered)` — satisfies invariants 1–4. Rejected: `on_time /
  prompts_sent` (moves the §4.6 denominator); a UTC date (splits the
  cohort's moment, reconciliation item 4); reading `dim_user.cohort_id`
  (Phase 3 decision).
- **Partition as a singular test over the mart, plus the unit test** —
  satisfies invariant 1. The singular test returns every cohort-day where
  either identity fails; it reads the mart, not `attribution`, so a mart
  that miscounts is what turns red. Rejected: a Python-only check (the
  contract says dbt test).
- **`ontime_retention` per `user_id`** with `anchor_date`, `ontime_rate`,
  `observed_through`, `retained` as a three-arm `case` (NULL / true /
  false), var `retention_days: 28` — satisfies invariant 7 (reconciliation
  item 3). Not golden-frozen (all-NULL on tiny pins nothing);
  `RETENTION_ROWS` and `ORGANIC_OPEN_ROWS` are the pins. Rejected: a
  fixture-sized default; scoping to `medium`.
- **`eval/golden.py` generalised — `export_rows(db, relation, columns, key)`,
  `render(rows, columns)`, `parse(text, columns)`, `diff_rows(built, frozen,
  key_width)`** — with two spec tuples (`ATTRIBUTION`, `ONTIME_RATE_DAILY`)
  naming relation, columns and key; `eval/report.py` computes the overall
  rate from the mart; `eval/cli.py` gains `report` — satisfies invariants
  5, 6, 9. `report` writes `data/out/<p>/expected/ontime_rate_daily.csv`
  on `WRITE=yes` only; `make freeze` is the one writer of `fixtures/`.
  Rejected: a copy of the module; a second writer.
- **`docs/METRICS.md` is the definition; `schema.yml` links** — one `### `
  block per metric with grain / numerator / denominator / null policy /
  pinning test; the doc test greps both — satisfies invariant 8.
  Rejected: definitions in `schema.yml` descriptions (dbt docs would then
  be the source and METRICS a copy).
- **Rates render as the engine's float; the golden renders `ontime_rate`
  rounded to 6 places** (`round(…, 6)` in the mart's select, ANSI on both
  dialects), so the CSV is stable across engines and the pin
  `ONTIME_RATE = 75 / 123` is compared exactly in Python — satisfies
  invariant 5. Rejected: an unrounded double in a frozen file (a DuckDB
  minor version could move the last digit).

## Scope (files)

- `dbt/models/marts/ontime_rate_daily.sql`, `ontime_retention.sql`,
  `schema.yml`; `dbt/dbt_project.yml` (var `retention_days`; the `marts`
  folder config, schema `marts`)
- `dbt/tests/assert_cohort_day_partition.sql`,
  `assert_cohort_day_key_unique.sql`
- `eval/golden.py` (generalised), `eval/report.py` (new), `eval/cli.py`
  (`report`); `Makefile` (`report`)
- `scripts/check_docs.py` (a `TRACES` token for the partition test)
- `fixtures/tiny/expected/ontime_rate_daily.csv`,
  `fixtures/tiny/MANIFEST.sha256`
- `tests/pins.py` (`ONTIME_RATE`, `COHORT_DAYS`, `RETENTION_ROWS`,
  `ORGANIC_OPEN_ROWS`, `PHASE3_MANIFEST_LINES`), `tests/test_marts.py`
  (new), `tests/test_metrics_doc.py` (new), `tests/test_eval.py`,
  `tests/test_attribution.py` (call-site only), `tests/test_fixture.py`,
  `tests/test_makefile.py`, `tests/test_dbt_conventions.py`
- `docs/METRICS.md` (new); records below

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 4 appendix (denominator identity as wording;
      local grain; NULL-vs-0 reading; retention three-state at 28; golden
      generalised; rounding); "Decisions still in force" gains the
      denominator line
- [ ] `docs/PHASES.md` — Phase 4 Done-when corrected (four delivered labels;
      the NULL/0 clause); "Delivered" paragraph
- [ ] `CLAUDE.md` — Denominator contract sentence; Current status; Commands
      (`report`); Repo map (`dbt/models/marts`, `eval/report.py`,
      `docs/METRICS.md`); Event model facts (`prompt_date` local); BACKLOG
      count
- [ ] `docs/ARCHITECTURE.md` — §2.6 grain, NULL policy, `retention_days`;
      §8 if a surprise lands
- [ ] `BACKLOG.md` — close "The denominator contract's sum must exclude
      `delivery_fault`"; the struck "Staging pins are counts only" row's
      revisit note answered (`ORGANIC_OPEN_ROWS`); open any deferred finding
- [ ] Spec amendments — none (no later spec exists)
- [ ] `docs/METRICS.md` — new: `ontime_rate` (cohort-day), the seven count
      columns, `retained`, the per-user `ontime_rate`
- [ ] README — none (no README yet)

## Threat model (REQUIRED)

`report` takes `PROFILE` and `WRITE` in the settled shape (one Python
process, `[a-z0-9_]+`, every path derived, `$(call _Q,$(value VAR))`,
both already `unexport`ed). Only the literal `yes` writes
`data/out/<p>/expected/ontime_rate_daily.csv` (gitignored; never
`fixtures/`); anything else is check mode. No delete, no cloud, no input.
Residual: `WRITE=yes` from the environment writes `data/out/` — the stated
Phase 3 class, no committed consequence.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make report PROFILE= [WRITE=yes]` | refused (`report: refused — bad profile name`) | refused, no path derived | one literal, refused | reaches Python, validated the same; `WRITE` from env is honoured (residual, stated) | n/a — no CONFIRM; `WRITE` must equal `yes` | `tests/test_makefile.py::test_report_passes_profile_as_one_literal`; `tests/test_eval.py::test_report_write_only_on_literal_yes`; `tests/test_eval.py::test_cli_refuses_bad_profile_before_any_path` |

## Review & stack risk

- **code-reviewer** (triggered — dbt models/tests, `eval/`, Makefile,
  `scripts/check_docs.py`): the denominator identity, no user-days, the
  local cast with no `::`, `safe_divide` for the rate, no clock, five
  macros, no truth under `dbt/`, the golden's sort key, `report` writes no
  table.
- **security-reviewer** (triggered — a Makefile target taking a variable
  and a `WRITE` knob): the `WRITE` residual, no write under `fixtures/`
  outside `freeze`.
- **functionality-tester** (triggered): DONE command; a planted golden
  difference; a rate off the pin; each mutation line KILLED and the two
  named exclusions reasoned; `make seed PROFILE=tiny` still `manifest match`
  with two `expected/` files.
- **coherence-auditor** at exit: CLAUDE.md's denominator sentence names the
  four labels; PHASES Phase 4 Done-when corrected; §2.6 grain/NULL/var;
  METRICS.md is the only place a formula lives; one BACKLOG row struck and
  the struck row's note answered; BACKLOG count; Repo map.
- Stack risk: a dbt unit test over a model that reads only `ref('attribution')`
  (one `given`) is the Phase 3 shape; `ontime_retention` reads two refs
  (`attribution`, `stg_events`) — verified in Phase 3. `round()` on a
  DuckDB `double` and a `date` arithmetic `anchor_date + retention_days`
  (`+ interval` is dialect; the ANSI form on both engines is to be verified
  in the first hour — a `date_add`-style call belongs behind a macro, which
  would be a sixth and a STOP). `cast(timestamp as date)` under
  `TimeZone = 'UTC'` — the pinned session zone (§8) must not enter a naive
  cast. STOP and log under §8 on any surprise.

## Out of scope (deferred, recorded)

- `provisional`/`final`, incremental marts and `prompt_date` as a
  partition column upstream — Phase 7 (PHASES; reconciliation item 4).
- A frozen retention golden — needs a profile whose window closes (`medium`
  or a `retention`-shaped profile); BACKLOG row with trigger "first frozen
  profile ≥ `retention_days` days".
- `docs/RESULTS.md` — Phase 6 owns the generated block; `report` is console.
- Reachable-window MAE and the simulation — Phases 5–6.
