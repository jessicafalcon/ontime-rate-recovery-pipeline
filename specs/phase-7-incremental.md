# Phase 7 — Incrementality and late arrival (PROPOSED)

Contract for the `phase-7-incremental` branch. Source: `docs/PHASES.md` Phase 7.
Depends on Phase 6 merged (PR #8, `c8f16ef`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
dbt-duckdb 1.11.0 ships `["append", "delete+insert"]` and dbt-core 1.12.3 ships
custom incremental strategies (`get_incremental_<name>_sql`); both are already
installed. `insert_overwrite` is unavailable on DuckDB 1.5.5 (the adapter raises
"Please upgrade DuckDB or use 'append' or 'delete+insert'"), which is why the
project's own `partition_overwrite` macro is the seam, not a built-in — a
STOP-and-ask only if custom-strategy registration turns out unsupported (first-
hour check).

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 6 shipped, and the carry-overs due this
phase. Items marked **design change** were **approved 2026-08-26** in the
reconciliation pass; the rest are facts the spec pins. Numbers are read off
`main` (`c8f16ef`): `make dbt-build PROFILE=tiny`, `make seed PROFILE=medium &&
make dbt-build PROFILE=medium` (unfrozen), and the ten `fixtures/tiny/raw/`
landing files.

1. **What "a landing" is — design change, (b) with a bounded loader change.**
   A landing IS a raw-table state: the loader stays whole-table-recreate, and
   "two landings" means `load` a file subset → build, then `load` the full set
   → build. dbt's incremental state lives in the `staging`/`attribution`
   schemas, not in `raw`, so recreating `raw` each load is harmless. The only
   loader change is a subset filter — `loader.load(profile, db, through=None)`
   over `event_files(fixture, through)` — and **`make load PROFILE=<p>
   [THROUGH=<upload-date>]`**. A `landing_file` raw column is REJECTED: the
   Amplitude export does not carry it and `generator/models.py` does not model
   it, so it would force a `gen-sources` schema change, and it is unnecessary
   (partition + upload high-water mark already drive reprocessing). New Makefile
   variable → threat-model row. **Approved.**

2. **Which models become incremental, and the dedupe — fact.** `stg_events`
   (partition `event_date = cast(client_event_time_local as date)` — an
   `app_opened` has no `prompt_id`), `stg_prompts` and `attribution` (partition
   `prompt_date = cast(sent_at_local as date)`, moved upstream from the mart —
   DECISIONS Phase 4 anticipated this) become incremental; `features`, `scores`,
   `marts` stay `table` (an incremental table accumulates, so they always read
   the full inputs). **Dedupe invariant:** both copies of a duplicate share
   `client_event_time`, hence the same partition, so the `qualify` reprocesses
   or closes them together and never splits a duplicate; and because files are
   keyed by `server_upload_time` date and the dedupe keeps the earliest upload,
   the winning copy never lands in a later landing than a loser. On tiny exactly
   one duplicate straddles a file boundary (`e-0000259`, prompt `p-000037`,
   copies in `01-05`/`01-06`). Not a design change (a property of the existing
   dedupe under incrementality).

3. **Lookback, horizon, no clock — fact, one position blessed.** `LOOKBACK_DAYS`
   is a var (default **5**); the horizon is **data-derived** `max(server_upload_
   time)` (the landing's high-water mark), never a `run_date`, never a clock —
   the same reconciliation of "models take `run_date`/`as_of` as vars" that
   `computed_as_of` already makes. A partition is `final` when it is at least
   `LOOKBACK_DAYS` behind the horizon, `provisional` otherwise. **Pinned
   identity:** `LOOKBACK_DAYS · 24 > late_arrival_max_hours` on every profile —
   medium (72 h) is the binding floor (`LOOKBACK ≥ 4`), so 5 (120 h) clears both
   profiles with a 48 h margin that also absorbs the local-date-vs-UTC-upload
   smear. A too-small `LOOKBACK` fails the profile-knob test. **Approved
   (data-derived horizon, not a `run_date` var).**

4. **The macro vs the built-in — design change, keep the macro.** `insert_
   overwrite` is unavailable on our DuckDB and `delete+insert` deletes by
   `unique_key` match (not by partition), so neither gives partition-replace
   semantics or a place for the render test the DUE BACKLOG row demands. This
   phase **closes BACKLOG `partition_overwrite`** by completing it to
   delete-and-insert in the set-based subquery form (no unquoted list to
   mis-quote — the defect is removed by construction), adding a **render test**,
   and wiring the three models to it as a **custom incremental strategy**
   (`get_incremental_partition_overwrite_sql`, which calls the dispatched
   `partition_overwrite`). Dialect contract stays at **five dispatch macros**
   (the strategy macro is dbt plumbing, not a sixth `adapter.dispatch`); the
   BigQuery body keeps raising until Phase 9. DECISIONS entry (dialect contract
   touched). **Approved.**

5. **provisional/final without moving the golden — fact.** `status`
   (`'provisional' | 'final'`) is a `case` on `attribution` (a `drop-arm`
   target), kept OUT of `eval/golden.py::ATTRIBUTION` (still the four columns
   `prompt_id,user_id,cohort_id,label`), so `expected/attribution.csv` does not
   move and **no `Freeze:` line is needed**. Confirmed safe: no downstream
   `select *`, the mart reads named columns. Pinned by a count (final vs
   provisional prompts on tiny after the full landing) and the final-never-
   changes test. The convergence golden is the EXISTING `attribution.csv`,
   byte-identical after the two-landing build and the single-landing build.

6. **The Phase 6 carry-over — the central constraint.** A full single-landing
   `dbt build` must produce byte-identical staging/attribution/marts/scores to
   `main`, so every Phase 3–6 golden, pin and generated block is unchanged
   (`attribution-golden`, `report` 0.609756, `scores-golden`, `eval` accuracy
   1.000 / MAE 0.816201 / 0.352354, the RESULTS medium block, `SIMULATED_MEDIUM_
   ONTIME_RATE`, the power block). A first-run incremental build
   (`is_incremental()` false) is a full build, so equality is structural. Any
   drift is a Phase 7 bug; the sanctioned repair (`make simulate PROFILE=medium
   WRITE=yes` + a pin bump + DECISIONS line) is only for a deliberate, argued
   change — none is expected.

7. **The `--full-refresh` home — design change.** `make dbt-build PROFILE=<p>
   [FULL=yes]` passes `--full-refresh`; because it drops-and-recreates the
   incremental tables, `FULL=yes` is `$(origin)`-gated (command-line only), the
   `CONFIRM` shape. New gated target → threat-model row. **Approved.**

8. **Drift to correct at exit — facts.** CLAUDE.md: Event-model facts
   ("provisional until `LOOKBACK_DAYS` closes, then final forever (Phase 7)" and
   `prompt_date` upstream now real), Commands (`load` gains `THROUGH`,
   `dbt-build` gains `FULL` and its "tables are recreated" wording is now false
   for the three incremental models), Repo map (`loader/` `THROUGH`), Current
   status, BACKLOG count; `docs/ARCHITECTURE.md` §3.1 loader row (`THROUGH`), §8
   if a surprise lands; `docs/PHASES.md` Phase 7 Done-when + "Delivered";
   `DECISIONS.md` Phase 7; `tests/pins.py` (`LOOKBACK_DAYS`, the final/provisional
   counts, the landing split). **BACKLOG:** `partition_overwrite` closes (10 →
   9); the `order by` tie-break row ("a second window-function tie-break lands,
   Phase 6/7") is **re-deferred** — this phase adds `max()`/partition predicates
   and reuses the Phase 2 dedupe `qualify`, no NEW window-function tie-break, so
   the row's trigger is not pulled (confirmed in the Invariants). Every other
   open row is a Phase 9/10 or frozen-profile trigger.

Design changes above — items 1, 4, 7 — and the item-3 position **approved
2026-08-26**. One spec, not 7a/7b: the DUE BACKLOG row requires the macro's
insert half to land WITH its first caller (the incremental models), so the
plumbing and the models cannot be split across two PRs. The sections below are
the contract.

## Teaching notes (first appearance in this project)

- **Incremental materializations and `is_incremental()`.** A `table` model
  rebuilds every row on every run; an `incremental` model builds fully the first
  time, then on later runs processes only the rows its `{% if is_incremental()
  %}` filter admits and merges them into the existing table. `is_incremental()`
  is true only when the target already exists and the run is not `--full-refresh`
  — so the same model file is both the from-scratch build and the merge.

- **`delete+insert` / `insert_overwrite` and partition-by-date.** These are the
  two ways to merge a batch: `delete+insert` removes rows matching a key then
  inserts the batch; `insert_overwrite` (BigQuery) replaces whole partitions.
  Partitioning by a date column lets a late-arriving-facts model rewrite only
  the recent days it might have changed. Here neither built-in fits — DuckDB
  1.5.5 has no `insert_overwrite`, and we want "replace exactly the partitions in
  the batch" — so `partition_overwrite` does `delete … where <part> in (select
  distinct <part> from <batch>); insert … select … from <batch>`, one named
  macro both dialects dispatch on.

- **Reprocessing lookback windows vs late-arriving data.** An upload-fault event
  arrives hours after its prompt was sent, so its facts land in a later file
  than the day they belong to. A lookback window reprocesses a trailing band of
  partitions every run — wide enough that any event still in flight for a
  partition has already arrived — instead of trusting that yesterday is done.
  The width is `LOOKBACK_DAYS`; the guarantee is the identity `LOOKBACK_DAYS · 24
  > late_arrival_max_hours`.

- **provisional/final labels as a data contract.** A label computed while its
  partition is still inside the lookback is `provisional` — a later landing may
  add an event and change it. Once the partition falls behind the lookback it is
  `final`: no admissible event can still arrive, so the label is frozen and
  every later run must reproduce it byte-for-byte (ARCHITECTURE §4 invariant 3).
  The status is a promise about what may still move, not a mechanism.

- **Data-derived high-water mark vs wall-clock `run_date`.** The horizon that
  decides which partitions are final is `max(server_upload_time)` over the
  landed data — a property of the bytes, identical on every machine and every
  re-run. A wall-clock `now()` would make the same raw give a different answer
  tomorrow (ARCHITECTURE §4 invariant 2); the high-water mark is the determinism
  policy's "no clock on the data path" applied to incrementality, the same rule
  `computed_as_of` already follows.

## Why

Phases 1–6 build every model as a full table: correct, but a re-run reprocesses
all of history and nothing distinguishes a settled day from one still receiving
late uploads — the events the project is about. Phase 7 makes the three
event-level models incremental with a reprocessing lookback, stamps each label
`provisional`/`final`, and proves the three properties the plan names
(convergence, idempotence, final-never-changes) against the generator's own
late-arrival knob — without moving a single downstream number. A fix PR cannot
carry it: it changes the materialization of three models, completes a dialect
macro, and adds a status column to the label contract.

## The central constraint

**Incrementality is added UNDER the existing outputs, not over them.** A full
`dbt build` reproduces every Phase 3–6 golden, pin and generated block
byte-for-byte; `fixtures/tiny/` does not move (no `Freeze:` line — `status`
stays out of the golden export); the generator is not edited; the raw schema is
not regenerated (no landing column); and the dialect contract stays at five
dispatch macros. A column the golden would gain, a generator edit, or a sixth
macro is a STOP.

## DONE command

```
make review-gate SPEC=specs/phase-7-incremental.md && make dbt-build PROFILE=tiny && make attribution-golden PROFILE=tiny && make report PROFILE=tiny && make scores-golden PROFILE=tiny && make eval PROFILE=tiny && make simulate PROFILE=tiny && make seed PROFILE=medium && make dbt-build PROFILE=medium && make simulate PROFILE=medium && make power
```

- `make review-gate SPEC=…` — offline suite (the two-landing convergence,
  idempotence, final-never-changes, straddling-dedupe, planted-closed-partition
  and Tokyo tests on tiny in-process; the identity over every profile; the
  `partition_overwrite` render and five-macro tests; the `THROUGH`/`FULL`
  negatives and the power/simulate pins), ruff, check-docs, Evidence ids,
  Record-updates files, no `Freeze:` line.
- `make dbt-build PROFILE=tiny` — 7 models incremental-first-run == full build;
  `dbt-build OK: tiny/duckdb`.
- `make attribution-golden / report / scores-golden / eval / simulate
  PROFILE=tiny` — every Phase 3–6 tiny gate byte-identical to `main` (0 differ;
  ontime_rate 0.609756; accuracy 1.000, MAE 0.816201; block matches).
- `make seed medium && dbt-build medium && simulate medium && power` — the
  medium proof unchanged: MAE 0.352354, `simulate OK: medium … block matches`,
  `power OK: 6 rows`.

## Done-when

1. **Convergence.** A raw set split into two landings (bulk `≤ THROUGH`, then the
   late tail) builds incrementally to `stg_events`/`stg_prompts`/`attribution`
   table hashes identical to a single whole-set build, and to a byte-identical
   `expected/attribution.csv`. *Evidence: row 1.*
2. **Idempotence.** Running the second landing (load full + build) twice yields
   identical table hashes for all three models — the partition-overwrite is a
   no-op on a re-run. *Evidence: row 2.*
3. **Final never changes.** Every prompt whose partition is `final` after
   landing 1 carries the identical label after landing 2; `status` advances
   `provisional → final` only; and `LOOKBACK_DAYS · 24 > late_arrival_max_hours`
   on every profile, so no admissible late event lands on a closed partition.
   *Evidence: row 3.*
4. **Dedupe across landings.** A duplicate `insert_id` whose copies land in
   different landings stages to one row (earliest upload kept); `stg_events` is
   unique on `insert_id`. *Evidence: row 4.*
5. **No clock, lookback-driven.** The horizon is `max(server_upload_time)` over
   the data — no `now()`/`current_timestamp()` in any model or macro; a build
   under `TZ=Asia/Tokyo` is identical; a row planted in a closed partition
   survives a landing untouched (loads are driven by the partitions a landing
   touches, never the wall clock). *Evidence: row 5.*
6. **Dialect and carry-forward.** `partition_overwrite` renders delete-and-insert
   on DuckDB and raises on BigQuery; still five dispatch macros; every Phase 3–6
   golden/pin/block byte-identical after a full build; no new package, generator
   untouched, `fixtures/tiny/` untouched. *Evidence: row 6.*

(≤ 6. `docs/PHASES.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_incremental.py::test_two_landings_equal_one_landing` (loads `≤ THROUGH` then full into one db, hashes `stg_events`/`stg_prompts`/`attribution` == a single-build db; exports attribution and equals `fixtures/tiny/expected/attribution.csv`); `make attribution-golden PROFILE=tiny` → `0 differ` |
| 2 | `tests/test_incremental.py::test_landing_two_twice_is_a_noop` (second load+build applied twice → identical table hashes) |
| 3 | `tests/test_incremental.py::test_final_labels_never_change` (per-prompt join: label of every landing-1 `final` prompt equals its landing-2 label); `::test_status_advances_provisional_to_final_only`; `::test_identity_lookback_exceeds_late_arrival` (reads every `generator/profiles/*.json`); `tests/pins.py::FINAL_PROMPTS_TINY`/`PROVISIONAL_PROMPTS_TINY` |
| 4 | `tests/test_incremental.py::test_duplicate_straddling_a_landing_dedupes_to_one` (splits so `e-0000259`'s copies fall in different landings; one `stg_events` row, earliest upload kept); `make dbt-build PROFILE=tiny` unique test on `stg_events.insert_id` green |
| 5 | `tests/test_dbt_conventions.py::test_no_clock_call_in_any_model_or_macro` (unchanged, now covers the incremental models + `partition_overwrite`); `tests/test_incremental.py::test_build_under_tokyo_is_identical` (second-process `TZ=Asia/Tokyo` build, identical hashes); `::test_planted_row_in_a_closed_partition_survives_a_landing` (insert a row into a closed partition of `this`, run a landing, row unchanged) |
| 6 | `tests/test_dbt_conventions.py::test_partition_overwrite_renders_delete_and_insert_on_duckdb`, `::test_partition_overwrite_raises_on_bigquery`, `::test_exactly_five_dispatch_macros`; `make report/scores-golden/eval/simulate PROFILE=tiny` + `make simulate PROFILE=medium` + `make power` all `block matches`/`0 differ`/pins; `tests/test_fixture.py::test_raw_dims_truth_hashes_are_the_phase_1_hashes` + `::test_phase_3_and_4_expected_hashes_are_unchanged`; `git diff main --stat -- generator/` empty; `uv.lock` unchanged; review-gate `PASS fixtures` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Convergence.** For all splits of a raw set into a `≤ THROUGH` landing then the late tail, the incremental `stg_events`/`stg_prompts`/`attribution` equal the single-build tables (row content, table hash) and the exported `attribution.csv` is byte-identical. | `test_two_landings_equal_one_landing`; mutation `loader/load.py::event_files invert-guard` (landing 1 ignores `THROUGH` → loads all → the split degenerates) |
| 2. **Idempotence.** For all landings, applying it twice equals applying it once — the partition-overwrite delete-and-insert converges. | `test_landing_two_twice_is_a_noop` |
| 3. **Final never changes.** For all prompts whose partition is `final` after a landing, the label is identical on every later landing; `status` advances `provisional → final` only. | `test_final_labels_never_change`; `test_status_advances_provisional_to_final_only`; mutation `dbt/models/attribution/attribution.sql::status drop-arm:1` (kills the `final` arm → the final count and freeze both move) |
| 4. **Lookback covers late arrival.** For all profiles, `LOOKBACK_DAYS · 24 > late_arrival_max_hours`, so a late event's own partition is still inside the reprocessing window when it arrives and never lands on a closed one. | `test_identity_lookback_exceeds_late_arrival` (over every `profiles/*.json`) |
| 5. **Dedupe across landings.** For all duplicate `insert_id`, the copies share `client_event_time` (one partition) and the earliest upload is kept, so a duplicate straddling a landing stages to exactly one row; `stg_events` is unique on `insert_id`. | `test_duplicate_straddling_a_landing_dedupes_to_one`; the `stg_events` `unique` test |
| 6. **No clock.** For all models and macros, no `now()`/`current_timestamp()`/`getdate()`; the horizon is `max(server_upload_time)` over the data; a build under a non-UTC host zone is identical. | `test_no_clock_call_in_any_model_or_macro`; `test_build_under_tokyo_is_identical` |
| 7. **Partition, lookback-driven.** For all closed partitions (older than the lookback), a landing never rewrites them — a row planted in a closed partition of `this` survives untouched; loads are driven by the partitions a landing touches. | `test_planted_row_in_a_closed_partition_survives_a_landing` |
| 8. **Dialect.** For all dialects, `partition_overwrite` is one dispatch macro — a DuckDB delete-and-insert body, a BigQuery body that raises until Phase 9, no `default__`; exactly five dispatch macros exist. | `test_partition_overwrite_renders_delete_and_insert_on_duckdb`; `test_partition_overwrite_raises_on_bigquery`; `test_exactly_five_dispatch_macros` |
| 9. **Denominator and label contracts unchanged.** For all cohort-days, `on_time + upload_fault + timing_gap + unattributed = prompts_delivered` and `+ delivery_fault = prompts_sent`; the label set stays five (`accepted_values`). | `dbt/tests/assert_cohort_day_partition.sql`; the `label` `accepted_values` test (both unchanged, green on the incremental build) |
| 10. **Downstream unchanged.** For all Phase 3–6 outputs, a full build reproduces them byte-for-byte — `attribution.csv`, `ontime_rate_daily.csv`, `scores_send_time.csv`, the eval pins, the RESULTS blocks, the power block. | `make attribution-golden/report/scores-golden/eval/simulate/power`; `test_raw_dims_truth_hashes_are_the_phase_1_hashes`; `test_phase_3_and_4_expected_hashes_are_unchanged` |
| 11. **Carry-forward boundary.** For all of the phase, no new package, the generator is unchanged, `fixtures/tiny/` is unchanged (no `Freeze:`), the raw schema is not regenerated, and `truth/` is read by `eval/` only. | `git diff main -- generator/` empty; `uv.lock` unchanged; review-gate `PASS fixtures`; `tests/test_dbt_sources.py` (raw DDL/sources unchanged); `tests/test_truth_isolation.py` |

Rules — the horizon expression, the incremental `where`, and `partition_overwrite`'s
body are SQL predicates/DDL that no mutation operator addresses (the SQL
operators act on `case` arms only); they are pinned by the convergence,
idempotence, final-count, planted-closed-partition and identity tests above,
and killed in the sweep by the two-landing in-process build going red. Every
Python invariant gets a mutation line; the `status` case is the one SQL line.

```mutations
loader/load.py::event_files                       invert-guard
loader/load.py::_file_date                        constant-return:'2026-01-13'
loader/cli.py::validate_through                   invert-guard
loader/cli.py::full_refresh_args                  invert-guard
dbt/models/attribution/attribution.sql::status    drop-arm:1
```

Equivalent-mutant / refused exclusions, named up front and verified once at
implementation on a scratch copy (the Phase 6 pattern — a killing exclusion is
promoted into the block, a refusal is recorded):

- `loader/load.py::event_files swap-sort-key` — REFUSED (single-key sort by
  file name). Landing order is unobservable: the loader recreates the whole raw
  table and `stg_events` dedupes by content, so the order files are inserted
  cannot change any output. Pinned by `test_duplicate_straddling_a_landing_
  dedupes_to_one` (the earliest-upload winner is content-chosen).
- `dbt/models/attribution/attribution.sql::status swap-arms:1,2` — REFUSED (the
  `status` case has one `when` arm and an `else`; `swap-arms` needs two arms).
  The `final`/`provisional` split is pinned by `drop-arm:1` above and the count
  pins.
- The Phase 3 `label` case is not re-mutated here (unchanged from Phase 3; its
  arms stay pinned by that phase's block).

## Pinned decisions (do not re-litigate)

- **A landing is a raw-table state; the loader stays whole-table-recreate and
  gains a `THROUGH` file filter (reconciliation item 1)** — satisfies invariants
  1, 2. `make load PROFILE=<p> [THROUGH=<date>]` limits which `events_*.jsonl`
  populate the recreated `raw`; no `landing_file` column, no `gen-sources`
  change. Rejected: a raw landing column (a schema change for a column the
  export does not carry); an append-per-file loader (dbt owns the incremental
  state).
- **`stg_events` (partition `event_date`), `stg_prompts` and `attribution`
  (partition `prompt_date`, computed upstream) are incremental via the
  `partition_overwrite` custom strategy; `features`/`scores`/`marts` stay table
  (item 2)** — satisfies invariants 1, 5. The dedupe `qualify` runs over whole
  `raw` before the lookback filter, so duplicates never split. Rejected: making
  the marts incremental (they aggregate the full accumulated inputs — nothing to
  reprocess).
- **`LOOKBACK_DAYS = 5` (var), horizon = data-derived `max(server_upload_time)`,
  `final` when the partition is ≥ `LOOKBACK_DAYS` behind the horizon via
  `timestamp_diff('day', …)` (no date arithmetic, no new dialect); identity
  `LOOKBACK_DAYS · 24 > late_arrival_max_hours` per profile (item 3)** —
  satisfies invariants 3, 4, 6. Rejected: a `run_date` var (a clock on the data
  path); `date − int` arithmetic (dialect-divergent; `timestamp_diff` is the
  established day macro).
- **`partition_overwrite` completed as the single seam: `delete … where <part>
  in (select distinct <part> from <batch>); insert … select … from <batch>`,
  dispatched (DuckDB body, BigQuery raises), a render test, and a custom
  incremental strategy `get_incremental_partition_overwrite_sql` that calls it
  (item 4)** — satisfies invariant 8; closes BACKLOG `partition_overwrite`. The
  subquery form removes the "unquoted list" defect by construction. Rejected:
  the built-in `delete+insert`/`insert_overwrite` (no partition semantics on our
  DuckDB, no place for the render test — DECISIONS dialect entry updated);
  a sixth dispatch macro (the strategy macro is plumbing, not dispatched).
- **`status` (`'provisional' | 'final'`) is a `case` on `attribution`, kept out
  of `eval/golden.py::ATTRIBUTION` (item 5)** — satisfies invariant 3; keeps
  `expected/attribution.csv` frozen (no `Freeze:` line). Pinned by the
  final/provisional counts and the final-never-changes test. Rejected: adding
  `status` to the golden export (a re-freeze for a column derivable from
  `prompt_date` and the horizon).
- **`make dbt-build PROFILE=<p> [FULL=yes]` passes `--full-refresh`, `$(origin)`-
  gated (item 7)** — the rebuild path that reproduces `main` (invariant 10). It
  drops-and-recreates the incremental tables, so `FULL=yes` counts only from the
  command line (the `CONFIRM` shape). Rejected: a separate `--full-refresh`
  target (one build entry point, one validated knob).

## Scope (files)

- `loader/load.py` (`event_files(fixture, through)`, `_file_date`, `load(…,
  through)`), `loader/cli.py` (`validate_through`, `full_refresh_args`, `load`
  and `dbt_build` gain the knobs)
- `dbt/models/staging/stg_events.sql`, `stg_prompts.sql` (incremental config +
  lookback `where` + partition column), `dbt/models/attribution/attribution.sql`
  (incremental + `prompt_date` upstream + `status` case)
- `dbt/macros/partition_overwrite.sql` (delete-and-insert subquery form + the
  `get_incremental_partition_overwrite_sql` strategy macro)
- `dbt/models/**/schema.yml` (the `status` column description + test; the
  `prompt_date` column on `attribution`/`stg_prompts`), `dbt/dbt_project.yml`
  (`lookback_days` var)
- `Makefile` (`load` `THROUGH`, `dbt-build` `FULL`)
- `tests/test_incremental.py` (new), `tests/test_dbt_conventions.py`
  (partition_overwrite render/raise, five macros), `tests/test_loader.py`
  (`THROUGH` subset, `validate_through`, `full_refresh_args`),
  `tests/test_makefile.py` (`THROUGH`/`FULL` literal tests), `tests/pins.py`
  (`LOOKBACK_DAYS`, final/provisional counts, the landing split)
- Records: `DECISIONS.md`, `docs/PHASES.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`
  (§3.1 loader row; §8 only if a surprise), `BACKLOG.md`
- Untouched by contract: `generator/`, `fixtures/`, `pyproject.toml`, `uv.lock`,
  `eval/`, `serving/`, `orchestration/`, `infra/`

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 7 entries: landing = raw-table state + `THROUGH`;
      the three incremental models + partition columns; `LOOKBACK_DAYS` +
      data-derived horizon + the identity; `partition_overwrite` completed
      (supersede pointer on the Phase 2 "no caller" note); `status` off the
      golden; `FULL=yes`
- [ ] `docs/PHASES.md` — Phase 7 "Delivered" paragraph; Done-when as landed
- [ ] `CLAUDE.md` — Current status; Commands (`load` `THROUGH`, `dbt-build`
      `FULL`, the "tables are recreated" wording, the incremental note);
      Event-model facts (provisional/final real, `prompt_date` upstream); Repo
      map (`loader/`); Open BACKLOG rows: **9**
- [ ] `docs/ARCHITECTURE.md` — §3.1 loader row (`THROUGH`); §8 Gotchas only if a
      stack surprise lands (custom-strategy registration, `timestamp_diff` over
      dates, or a date-boundary smear)
- [ ] `BACKLOG.md` — `partition_overwrite` struck (`DONE Phase 7`); the `order
      by` tie-break row re-deferred unchanged; count 10 → 9
- [ ] Spec amendments — none (no later spec exists)
- [ ] `docs/RESULTS.md` / `docs/AB_DESIGN.md` / `docs/METRICS.md` — none (no
      block regenerates; a drift here would be a Phase 7 bug, not an edit)
- [ ] README — none (no README in the repo)

## Threat model (REQUIRED)

`load` gains `THROUGH`, `dbt-build` gains `FULL`, in the settled shape (one
Python process, `PROFILE` `[a-z0-9_]+`, every path derived; `$(call _Q,$(value
VAR))`; both `unexport`ed). `THROUGH` is validated `[0-9-]+` (an upload date;
empty = the whole set) and never becomes a path — it filters file names already
under `fixtures/<p>/raw/`. `FULL=yes` counts only from the command line
(`$(origin)`); it recreates the incremental tables (not the db file), so a
stray env `FULL=yes` is ignored and a command-line one is visible in `git diff`
of the built schema. No delete of a file, no cloud, no user path input.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` gate | Pinned by |
|---|---|---|---|---|---|---|
| `make load PROFILE=<p> [THROUGH=<date>]` | `THROUGH=` → whole set (no filter) | refused (`THROUGH: refused — [0-9-]+`), never a path | one literal, refused | reaches Python, validated the same | n/a — `THROUGH` is not a confirm | `tests/test_makefile.py::test_load_passes_through_as_one_literal`; `tests/test_loader.py::test_load_refuses_bad_through`, `::test_through_loads_only_files_on_or_before` |
| `make dbt-build PROFILE=<p> [FULL=yes]` | `FULL=` → normal incremental | n/a — not a path | one literal, refused | `FULL=yes` from env ignored (`$(origin)` ≠ command line) | `FULL=yes` honoured only from the command line | `tests/test_makefile.py::test_dbt_build_full_refresh_from_command_line_only`; `tests/test_loader.py::test_full_refresh_only_on_command_line_yes` |

## Review & stack risk

- **code-reviewer** (triggered — `loader/`, `dbt/**`, `Makefile`, `tests/`): the
  incremental config on exactly the three event-level models; the horizon
  data-derived (no clock); `timestamp_diff` day arithmetic (no `date − int`,
  no sixth macro); `partition_overwrite` delete-and-insert, dispatched, BigQuery
  raises; `status` out of the golden export; the dedupe `qualify` unchanged
  under incrementality; `THROUGH`/`FULL` validated; no generator/fixture/raw-
  schema diff; every downstream pin reproduced, never adjusted.
- **security-reviewer** (triggered — a new `$(origin)`-gated destructive-ish
  target `FULL` and a new variable `THROUGH`): the `$(origin)` gate on `FULL`,
  `THROUGH` never a path, `_Q` quoting, `unexport`, no secret path.
- **functionality-tester** (triggered): the DONE command; the two-landing
  convergence/idempotence/final/dedupe/planted-partition/Tokyo tests; each
  mutation line KILLED and the exclusions reasoned; `make seed PROFILE=tiny`
  still `manifest match`; every Phase 3–6 gate byte-identical.
- **coherence-auditor** at exit (mandatory, whole repo): CLAUDE.md Event-model
  facts and Commands updated; §3.1 loader row; PHASES "Delivered"; DECISIONS
  Phase 7 + the Phase 2 supersede pointer; BACKLOG `partition_overwrite` struck
  and the count 9; that the finished phase supports Phase 8 (a landing the
  Airflow DAG can order).
- Stack risk (first hour, STOP on any surprise, §8): (1) custom incremental
  strategy registration on dbt-core 1.12 / dbt-duckdb 1.11 — that
  `get_incremental_partition_overwrite_sql` is picked up and `is_incremental()`
  routes through it; (2) `timestamp_diff('day', …)` over date-typed args on
  DuckDB; (3) the local-`event_date` vs UTC-`server_upload_time` date boundary
  staying inside the identity's margin; (4) `tests/test_dbt_conventions.py`'s
  macro count seeing the strategy macro — it must count `adapter.dispatch`
  macros, not every macro, so the seam stays five.

## Out of scope (deferred, recorded)

- A frozen `medium` and a second full medium two-landing build — the convergence
  matrix runs on tiny (in-process, cheap); medium gets one full-build downstream
  check via the existing `make … PROFILE=medium` gates. A medium two-landing
  test is a BACKLOG row if a survivor class ever needs it.
- The `order by` window-tie-break mutation operator — re-deferred (no new
  window-function tie-break this phase); BACKLOG row unchanged.
- The BigQuery `partition_overwrite` body and a BigQuery incremental build —
  Phase 9 (the body raises until then).
- The write-back reading `final` labels and the Airflow ordering of the landing
  — Phase 8.
