# Phase 8a — Write-back and `make pipeline` (PROPOSED)

Contract for the `phase-8-orchestration` branch (sub-phase 8a). Source:
`docs/PHASES.md` Phase 8, split 8a/8b (Done-when clause 1 minus the scheduler).
Depends on Phase 7 merged (PR #9, `e766e27`).

**Status: PROPOSED — do not start until approved.** No new dependencies: the
write-back is `serving/` Python over DuckDB via the already-installed `duckdb`
adapter; `make pipeline` chains existing loader/dbt/eval entry points plus the
new write-back. Airflow and its Docker image are **8b**, not 8a — nothing new
enters `uv.lock` here.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what main (`e766e27`, PR #9) actually is, and the
carry-overs due this sub-phase. Items marked **design change** were **approved
2026-08-26** in the reconciliation pass; the rest are facts the spec pins.
Numbers/columns are read off main: `make dbt-build PROFILE=tiny` then the score
table's nine columns, the ten `fixtures/tiny/raw/` landing files, and §2.9/§3.1
as written.

**0. Working tree and phase order — corrected before this commit.** Phase 9a
(`phase-9-gcp-foundation`, GCP/Terraform) was started **before** this checkpoint,
out of `docs/PHASES.md` order, and left uncommitted records on main's working
tree (`BACKLOG.md` edited to "DONE Phase 9a"; an untracked `docs/DEPLOYMENT.md`).
Both were **set aside** (`git stash@{0}`) so 8a starts from clean main; the
committed BACKLOG baseline is **10** open rows. Phase 9a is parked, its surface
(`infra/`, `tf-*`) disjoint from Phase 8's (`serving/`, `orchestration/`); it
reconciles against a main that includes Phase 8 when it resumes. Not a Phase 8
design change — a repo-state correction, recorded here so the audit trail shows
why the branch began dirty.

1. **The write-back is a new write path and package — design change.** Invariant
   first: *for all rows, a replacement happens iff `(model_version,
   computed_as_of)` is strictly greater, on the row's own columns* — data-derived,
   never a caller-supplied marker (the `specs/TEMPLATE.md` worked example, whose
   mutations block already names `serving/writeback.py::should_replace
   invert-guard`). Mechanism: a Python **`should_replace(candidate, existing) ->
   bool`** gates the write (so the sweep's `invert-guard` falsifies the
   invariant); **SQL does the read and a DELETE-winners + INSERT-winners**, the
   repo's only write idiom (`partition_overwrite` is delete-and-insert; there is
   no `ON CONFLICT`/MERGE macro). **No sixth dispatch macro:** the write-back is
   `serving/` Python running DuckDB SQL directly, not a dbt model, so the
   five-macro dialect contract does not apply — Phase 10 swaps the whole statement
   for Spanner's mutation API behind the target flag. Key `user_id`; re-running
   over the same scores is a no-op (§4 invariant 5: every candidate ties its
   existing row → not strictly greater → no winners). `serving/` is a pipeline
   dir, so `tests/test_truth_isolation.py` covers it automatically (not in
   `EXEMPT`) and the write-back never names `truth`. **Approved.**

2. **`send_schedule` DDL, `written_at`, and the serving `tz` — design change.**
   Three pinned sub-decisions, all bearing on the byte-identical Done-when:
   - **Hand-written serving DDL, not generated.** `scripts/gen_dbt_sources.py`
     emits `schema raw` + `create or replace table` (destructive full replace) —
     wrong for an upserted serving table — and there is no pydantic model for
     `send_schedule`; it is a **serving** contract (§2.9), not the event contract
     `generator/models.py` owns. `serving/ddl.sql`: `schema serving`, `create
     table if not exists` (the table must persist across runs for idempotence),
     `user_id` primary key, the nine §2.9 columns; a test pins the columns against
     §2.9. Rejected: a `SendSchedule` pydantic model + generator emit (machinery
     for one table with a different lifecycle and no second consumer).
   - **`written_at = computed_as_of`, per row (data-derived), never a wall
     clock.** A wall clock breaks the byte-identical `send_schedule` (Done-when
     clause 1) and the determinism policy ("no clock on the data path"). A
     *batch*-level stamp (`max(computed_as_of)`) breaks **backfill≡union** (8b): a
     user not re-touched by the last interval keeps an older batch stamp ≠ the
     union run's. Only a **per-row function of the winning score row** is stable
     under backfill, and `computed_as_of` is that function — and the only
     timestamp the write-back may read (§3.1 bars raw/staging). All nine columns
     are compared. The redundancy (`written_at ≡ computed_as_of` locally) is
     intentional; a production serving store may stamp a real ingest time in a
     **carved-out** audit column, never asserted. Rejected: a wall-clock
     `written_at` excluded from the compare (then `send_schedule` is only
     eight-of-nine-column identical — a weaker Done-when).
   - **Serving `tz` from the OPEN dim_user row via a new dbt model.** `tz` is not
     on `scores_send_time` (§2.8: the score carries only §2.9's columns; tz is
     sourced at write-back time), and §3.1 bars the write-back from `raw` — where
     `raw.dim_user` lives, with no `stg_dim_user`. So a thin dbt model
     **`dim_user_current`** (`user_id, cohort_id, tz` where `valid_to is null`)
     exposes the open row; the write-back reads **`scores_send_time` +
     `dim_user_current`** (both dbt outputs) and joins on `user_id`. This keeps
     the boundary clean and **amends §3.1**'s write-back `reads` cell to add
     `dim_user_current`. Rejected: adding `tz` to `scores_send_time` (a score
     column §2.8 forbids); loosening §3.1 to read `raw.dim_user` directly (widens
     the write-back's read surface to raw). **Approved.**

3. **`make pipeline` is one validated Python process — design change.** `make
   pipeline PROFILE=<p>` = `python -m serving.cli pipeline <p>`, one `[a-z0-9_]+`
   PROFILE, one process (the settled threat-model shape), calling `loader.load →
   loader.dbt_build → eval score → serving.writeback` as functions in order —
   producing `scores_send_time` and `send_schedule`. In 8b the Airflow DAG runs
   the same four steps as `make` targets; the chain is the DAG minus the
   scheduler. Rejected: a Makefile target that re-invokes `make load`, `make
   dbt-build`, … (loses the one-process path-derivation the threat model relies
   on, and re-invoked `$(MAKE)` would re-expand user variables). **Approved.**

4. **Determinism carve-outs — fact.** Asserted: `scores_send_time` and
   `send_schedule` (all nine columns — `written_at` included, because it is
   data-derived). No clock reaches either table. There is nothing Airflow-shaped
   to carve out in 8a (run ids/timings are 8b); the write-back reads no wall
   clock, no run id, no ordering.

5. **`make writeback` and `make pipeline` take one variable each — fact (threat
   model).** Both take `PROFILE` in the `$(call _Q,$(value PROFILE))` + `unexport`
   shape, validated `[a-z0-9_]+` in one Python process that derives every path.
   **No new destructive or `CONFIRM` target:** the write-back is `create table if
   not exists` + upsert (non-destructive); tests use throwaway DBs; a reset is the
   existing `make drop-db PROFILE=<p> CONFIRM=yes`. New Makefile variables →
   threat-model rows + `tests/test_makefile.py` literal tests.

6. **Drift to correct at exit — facts.** `CLAUDE.md`: Repo map (`serving/` from
   "(Phase 8)" to present; `serving/writeback.py`, `serving/cli.py`,
   `serving/ddl.sql`), Commands (`writeback`, `pipeline` out of "Later phases
   add" into live Commands), Current status (→ Phase 8a; the current text is
   **stale** — it says "Phase 7 in review", but PR #9 merged it), BACKLOG count.
   `docs/ARCHITECTURE.md`: §2.9 (`written_at = computed_as_of` locally,
   data-derived), §3.1 (write-back `reads` gains `dim_user_current`). `DECISIONS.md`
   "Decisions still in force" gains a **write-back contract** line (today it lives
   only in CLAUDE.md Engineering contracts + §2.9): replace-iff-greater on
   `(model_version, computed_as_of)`, key `user_id`, the write-back re-derives no
   score. `docs/PHASES.md`: Phase 8 "Delivered" (8a portion). **BACKLOG:** no row
   closes; **one opens** — `model_version` compares as a string
   (`'v10' < 'v2'` lexically), harmless at `v1` today, trigger Phase 10's first
   version bump → count **10 → 11**. Airflow-allowlist and `test-int-airflow` are
   **8b**, not touched here.

7. **The 8a/8b split — fact.** One Phase 8 spec carries seven pinned decisions,
   over the ≤6 cap. 8a = the write-back + `make pipeline` (this spec): the
   byte-identical chain, no scheduler. 8b = the Airflow DAG + `test-int-airflow` +
   backfill≡union, reconciled against a main that includes 8a. 8a merges first.

Design changes above — items 1, 2, 3 — **approved 2026-08-26**. The sections
below are the contract.

## Teaching notes (first appearance in this project)

- **The idempotent upsert / replace-iff-greater as a serving contract.** A
  serving table is read by an outside consumer (the notification service) between
  writes, so a batch that re-writes it must be safe to run twice and must never
  regress a row to older data. The rule is *upsert* (insert if absent, else
  update in place, keyed on `user_id`) *guarded by a monotonic comparison*: a
  row is overwritten only when the incoming `(model_version, computed_as_of)` is
  **strictly greater** than the stored one. "Strictly" is what makes a re-run a
  no-op — equal data does not replace equal data — and "on the row's own
  columns" (data-derived, not a timestamp the caller passes in) is what makes the
  decision reproducible on any machine. DuckDB has no `MERGE`; the repo's idiom is
  delete-the-winners-then-insert, and the guard is Python so the mutation sweep
  can falsify it.

- **Serving `tz` is the *current* zone, read at write-back time.** The score is
  computed in each event's own local hour (a tz-change user has one histogram),
  but what the notification service needs to turn `08:30 local` into an instant is
  the user's zone *right now* — the open SCD2 `dim_user` row (`valid_to is null`).
  That is a serving lookup, not a property of the score, so it enters
  `send_schedule` via a join at write-back, not as a score column.

- **One chain, two entry points (the make target now, the scheduler in 8b).**
  `load → dbt build → eval → write-back` is a fixed order (each step's inputs are
  the previous step's outputs). `make pipeline` runs it as one local process;
  8b's Airflow DAG runs the *same four steps* as ordered tasks. Because the steps
  are identical, the two entry points must produce byte-identical tables — the
  pipeline is the DAG minus the scheduler. 8a builds and proves the chain; 8b
  wraps it in Airflow and proves the wrapper agrees.

## Why

Phases 1–7 end at `scores_send_time` — the served schedule as a dbt table — but
nothing lands it where a serving system reads it, and nothing runs the whole
local chain end to end. Phase 8a adds the write-back (`send_schedule`, the
DuckDB stand-in for Spanner) and `make pipeline`, the no-scheduler chain, proving
the write-back is idempotent and replace-iff-greater and that the chain
reproduces both output tables byte-for-byte. A fix PR cannot carry it: it adds a
package with a new write path, a serving DDL, a dbt model, and two Makefile
targets. 8b then orders the same chain under Airflow.

## The central constraint

**The write-back adds a serving table WITHOUT re-deriving a score or reading a
clock.** `scores_send_time` is unchanged; `send_schedule` is a pure function of
the served columns (`send_hour_local`/`send_minute_local`/`confidence`/
`model_version`/`computed_as_of`, verbatim) plus the open-row `tz` and
`written_at = computed_as_of`; `make pipeline` reproduces both tables
byte-for-byte and a second write-back is a no-op. A score recomputed in Python, a
wall-clock `written_at`, a sixth dispatch macro, or a moved Phase 3–6 golden is a
STOP.

## DONE command

```
make review-gate SPEC=specs/phase-8a-write-back.md && make dbt-build PROFILE=tiny && make writeback PROFILE=tiny && make writeback PROFILE=tiny && make pipeline PROFILE=tiny && make attribution-golden PROFILE=tiny && make report PROFILE=tiny && make scores-golden PROFILE=tiny && make eval PROFILE=tiny
```

- `make review-gate SPEC=…` — offline suite (replace-iff-greater, new-user
  insert, write-back-twice idempotence, the nine-column DDL vs §2.9, tz from the
  open dim_user row, `written_at = computed_as_of`, non-UTC identity,
  `make pipeline` == step-by-step, the `writeback`/`pipeline` threat-model
  negatives), ruff, check-docs, Evidence ids, Record-updates files.
- `make dbt-build PROFILE=tiny` — the new `dim_user_current` model builds; every
  existing model unchanged; `dbt-build OK: tiny/duckdb`.
- `make writeback PROFILE=tiny` **twice** — on a fresh build the first writes
  `20 written`, the second is a no-op (`0 written`, table byte-identical) —
  idempotence in the DONE command itself. (A repeated local run over an already-
  written `send_schedule` writes `0` both times — still idempotent; `dbt-build`
  does not touch the `serving` schema.)
- `make pipeline PROFILE=tiny` — the full chain; `pipeline OK: tiny`;
  `scores_send_time` and `send_schedule` byte-identical to the step-by-step run.
- `make attribution-golden / report / scores-golden / eval PROFILE=tiny` — every
  Phase 3–6 tiny gate byte-identical (adding `dim_user_current` moves nothing):
  0 differ; ontime_rate 0.609756; accuracy 1.000, MAE 0.816201.

## Done-when

1. **Replace-iff-greater.** For all rows, the write-back overwrites a user's
   `send_schedule` row iff the incoming `(model_version, computed_as_of)` is
   strictly greater than the stored pair; an absent user inserts; a lesser or
   equal pair leaves the row untouched — on the row's own data-derived columns,
   no caller-supplied marker. *Evidence: row 1.*
2. **Idempotent write-back.** For all runs over the same scores, a second
   write-back writes zero rows and leaves `send_schedule` byte-identical (§4
   invariant 5). *Evidence: row 2.*
3. **`send_schedule` shape and determinism.** The nine §2.9 columns, `user_id`
   PK; `tz` is the open `dim_user` row (`valid_to is null`); `written_at =
   computed_as_of` (data-derived, no `now()`); a build under `TZ=Asia/Tokyo` is
   identical. *Evidence: row 3.*
4. **`make pipeline` byte-identical.** `make pipeline PROFILE=tiny` produces
   `scores_send_time` and `send_schedule` byte-identical to a manual
   `load → dbt build → eval → writeback`, and re-derives no score
   (`scores_send_time` equals main's). *Evidence: row 4.*
5. **Boundary and downstream unchanged.** The write-back reads only
   `scores_send_time` + `dim_user_current`, writes only `send_schedule`, and
   `serving/` never names `truth`/`raw`; adding `dim_user_current` leaves every
   Phase 3–6 golden and pin byte-identical. *Evidence: row 5.*
6. **Carry-forward.** No new package in `uv.lock`; the generator and
   `fixtures/tiny/` are untouched; exactly five dispatch macros (no upsert
   macro). *Evidence: row 6.*

(≤ 6. `docs/PHASES.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_writeback.py::test_replace_only_on_strictly_greater` (seed a row, apply candidates with lesser / equal / greater `(model_version, computed_as_of)`; only the strictly-greater one replaces); `::test_new_user_inserts`; `::test_should_replace_is_strict` (unit table over the four orderings) |
| 2 | `tests/test_writeback.py::test_writeback_twice_is_a_noop` (second run writes 0 rows, `send_schedule` hash identical); `make writeback PROFILE=tiny` run twice in the DONE command → `20 written` then `0 written` |
| 3 | `tests/test_writeback.py::test_send_schedule_has_the_nine_columns` (DDL vs §2.9); `::test_tz_is_the_open_dim_user_row`; `::test_written_at_equals_computed_as_of`; `::test_writeback_under_tokyo_is_identical`; `tests/pins.py::SEND_SCHEDULE_ROWS_TINY` (20) |
| 4 | `tests/test_pipeline.py::test_pipeline_send_schedule_matches_pin` (`make pipeline` → `send_schedule` == the pinned hash); `::test_pipeline_equals_standalone_writeback` (the chained write-back == the standalone one after dropping the table); `::test_pipeline_scores_equal_frozen_golden` (scores unchanged — the chain re-derives no score); `make pipeline PROFILE=tiny` → `pipeline OK: tiny` |
| 5 | `tests/test_truth_isolation.py` (now covers `serving/`); `tests/test_writeback.py::test_writeback_reads_only_scores_and_dim_current`; `::test_writeback_refuses_without_a_db` (the `_require_db` guard); `make attribution-golden/report/scores-golden/eval PROFILE=tiny` all `0 differ`/pins; `dbt/models/marts/schema.yml` `dim_user_current` `unique`/`not_null` tests green |
| 6 | `git diff main --stat -- generator/ fixtures/` empty; `uv.lock` unchanged; `tests/test_dbt_conventions.py::test_exactly_five_dispatch_macros`; review-gate `PASS fixtures` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Replace-iff-greater.** For all rows, `send_schedule` is overwritten iff the incoming `(model_version, computed_as_of)` is strictly greater than the stored pair (absent → insert; ≤ → untouched), on the row's own columns. | `test_replace_only_on_strictly_greater`; `test_new_user_inserts`; mutation `serving/writeback.py::should_replace invert-guard` (flips the comparison → a lesser pair replaces / a fresh user is dropped) |
| 2. **Idempotence.** For all runs over the same scores, applying the write-back twice equals once — the second writes zero rows, the table is byte-identical. | `test_writeback_twice_is_a_noop`; mutation `serving/writeback.py::apply_writeback delete-call` (drops the write → `send_schedule` empty/unwritten → both rows red) |
| 3. **Nine-column, data-derived shape.** For all rows, `send_schedule` carries exactly §2.9's nine columns, `user_id` PK, `tz` = the open `dim_user` row, `written_at = computed_as_of`; no clock. | `test_send_schedule_has_the_nine_columns`; `test_tz_is_the_open_dim_user_row`; `test_written_at_equals_computed_as_of`; `test_writeback_under_tokyo_is_identical` |
| 4. **Pipeline equals its steps.** For all profiles, `make pipeline` produces `send_schedule` byte-identical to the standalone write-back over the same build, and `scores_send_time` unchanged (re-derives no score). | `test_pipeline_send_schedule_matches_pin`; `test_pipeline_equals_standalone_writeback`; `test_pipeline_scores_equal_frozen_golden` |
| 5. **Boundary.** For all of `serving/`, it reads only `scores_send_time` + `dim_user_current`, writes only `send_schedule`, and never names `truth`/`raw`. | `tests/test_truth_isolation.py`; `test_writeback_reads_only_scores_and_dim_current`; mutation `serving/cli.py::validate_name invert-guard` (accepts a bad PROFILE / rejects `tiny` → the happy-path write red) |
| 6. **Downstream and carry-forward unchanged.** For all Phase 3–6 outputs, adding `dim_user_current` and the write-back reproduces them byte-for-byte; no new package, generator/fixtures untouched, five dispatch macros. | `make attribution-golden/report/scores-golden/eval PROFILE=tiny`; `test_exactly_five_dispatch_macros`; `git diff main -- generator/ fixtures/` empty; `uv.lock` unchanged |

Rules — `send_schedule`'s column list, the `tz` join predicate (`valid_to is
null`) and `written_at = computed_as_of` are SQL/DDL expressions no mutation
operator addresses (the SQL operators act on `case` arms only); they are pinned
by tests (the nine-column, tz, written_at and non-UTC tests), not the sweep. The
`dim_user_current` model is one CTE with no `case`, so it has no SQL mutation
line; its uniqueness/not-null are dbt tests. Every Python invariant gets a
mutation line; `_require_db` (round 1, finding 1) is a robustness precondition
rather than a core data invariant — it refuses when the build has not run, pinned
by `test_writeback_refuses_without_a_db` and its `invert-guard` line:

```mutations
serving/writeback.py::should_replace     invert-guard
serving/writeback.py::apply_writeback    delete-call
serving/cli.py::validate_name            invert-guard
serving/cli.py::_require_db              invert-guard
```

Equivalent-mutant / refused exclusions, named up front and verified once at
implementation on a scratch copy (the Phase 6/7 pattern — a killing exclusion is
promoted into the block, a refusal is recorded):

- `serving/writeback.py::apply_writeback swap-sort-key` — REFUSED. The write
  order is unobservable: `send_schedule` is a DuckDB table with no inherent row
  order and every comparison sorts by `user_id`, so the order winners are
  deleted/inserted cannot change any asserted output (mirrors Phase 7's
  `event_files swap-sort-key` refusal).
- `serving/writeback.py::should_replace constant-return:False` — REDUNDANT with
  `invert-guard` (both drop a fresh insert and a strictly-greater replace); the
  block keeps `invert-guard`, the stronger single line.
- `serving/writeback.py::should_replace swap-sort-key` — REFUSED (the guard is a
  boolean comparison, no sort key).

## Pinned decisions (do not re-litigate)

- **The write-back is `should_replace` (Python guard) + a SQL DELETE-winners /
  INSERT-winners, keyed `user_id` (reconciliation item 1)** — satisfies
  invariants 1, 2. The guard is Python so `invert-guard` falsifies replace-iff-
  greater; the write is the repo's delete-and-insert idiom. Rejected: an
  `ON CONFLICT`/MERGE dispatch macro (a sixth macro the five-macro contract
  forbids; the write-back is `serving/` Python, not a dbt model — Phase 10 swaps
  the statement for Spanner behind the target flag).
- **`serving/ddl.sql` is hand-written: `schema serving`, `create table if not
  exists`, `user_id` primary key, the nine §2.9 columns (item 2)** — satisfies
  invariant 3. Rejected: generating it from `generator/models.py` (its emitter is
  `schema raw` + `create or replace`, wrong for an upserted serving table; no
  pydantic model exists for a serving contract).
- **`written_at = computed_as_of` (per row, data-derived); serving `tz` from a
  new `dim_user_current` dbt model (open row); §3.1 write-back `reads` amended to
  add it (item 2)** — satisfies invariants 3, 5. A per-row data-derived
  `written_at` is what keeps `send_schedule` byte-identical under a re-run and
  (8b) under backfill; the model keeps the write-back off `raw`. Rejected: a
  wall-clock `written_at` (breaks byte-identical, a clock on the data path); a
  batch `max(computed_as_of)` stamp (breaks 8b's backfill≡union); a `tz` column
  on `scores_send_time` (§2.8 forbids).
- **`make pipeline PROFILE=<p>` = `python -m serving.cli pipeline <p>`, one
  validated process chaining `load → dbt build → eval → writeback` (item 3)** —
  satisfies invariant 4; the byte-identical chain 8b's DAG mirrors. Rejected: a
  Makefile target re-invoking `make load`/`make dbt-build`/… (loses the
  one-process path-derivation; re-invoked `$(MAKE)` re-expands user variables).

## Scope (files)

- `serving/__init__.py`, `serving/writeback.py` (`should_replace`,
  `apply_writeback`, `ensure_table`, `write_back(profile)`), `serving/cli.py`
  (`validate_name`, `writeback`, `pipeline`), `serving/ddl.sql` (the nine-column
  serving DDL)
- `dbt/models/marts/dim_user_current.sql` (open dim_user row: `user_id`,
  `cohort_id`, `tz`) + `dbt/models/marts/schema.yml` (`unique`/`not_null` tests,
  description linking §2.9)
- `Makefile` (`writeback`, `pipeline`; both `unexport PROFILE`, `$(call
  _Q,$(value PROFILE))`)
- `tests/test_writeback.py` (new), `tests/test_pipeline.py` (new),
  `tests/test_makefile.py` (`writeback`/`pipeline` literal tests),
  `tests/pins.py` (`SEND_SCHEDULE_ROWS_TINY = 20`, a `send_schedule` content
  hash), `tests/test_dbt_conventions.py` (five macros — unchanged, must stay
  green with the new model)
- Records: `DECISIONS.md`, `docs/PHASES.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`
  (§2.9, §3.1), `BACKLOG.md`
- Untouched by contract: `generator/`, `fixtures/`, `pyproject.toml`, `uv.lock`,
  `orchestration/` (8b), `infra/` (parked 9a), `dbt/models/{staging,attribution,
  features,scores}/`, `dbt/macros/`

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — "Decisions still in force" gains the **write-back
      contract** (replace-iff-greater on `(model_version, computed_as_of)`, key
      `user_id`, re-derives no score) and **"Airflow contains no logic"** (the
      latter also relied on by 8b); Phase 8a appendix entries: the serving DDL,
      `written_at = computed_as_of`, `dim_user_current` + §3.1 amendment, `make
      pipeline`
- [ ] `docs/PHASES.md` — Phase 8 "Delivered" (8a portion: write-back + `make
      pipeline`; 8b still open)
- [ ] `CLAUDE.md` — Current status (→ Phase 8a; the stale "Phase 7 in review"
      corrected); Commands (`writeback`, `pipeline` out of "Later phases add");
      Repo map (`serving/` present-tense; `dim_user_current` mart); Engineering
      contracts (write-back line already present — cross-check); Open BACKLOG
      rows: **11**
- [ ] `docs/ARCHITECTURE.md` — §2.9 (`written_at = computed_as_of` locally,
      data-derived); §3.1 (write-back `reads` gains `dim_user_current`); §8
      Gotchas only if a stack surprise lands
- [ ] `BACKLOG.md` — **open one row**: `model_version` string ordering
      (`'v10' < 'v2'`), trigger Phase 10's first version bump; count 10 → 11
- [ ] Spec amendments — none (8b's spec does not exist yet; it will reconcile
      against a main including 8a)
- [ ] docs/RESULTS.md, docs/AB_DESIGN.md, docs/METRICS.md — none
      (send_schedule is defined in §2.9; dim_user_current is a passthrough of the
      open dim_user row, described in schema.yml; no block regenerates)
- [ ] README — none (no README in the repo)

## Threat model (REQUIRED)

`writeback` and `pipeline` each take `PROFILE` in the settled shape (one Python
process, `PROFILE` `[a-z0-9_]+`, every path derived; `$(call _Q,$(value
PROFILE))`; `unexport`ed). No delete of a file, no cloud, no user path input, no
`CONFIRM` (the write-back is `create table if not exists` + upsert — a reset is
the existing `make drop-db … CONFIRM=yes`). Residual `MAKEFLAGS` is the standing
"mistakes, not a hostile environment" carve-out.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make writeback PROFILE=<p>` | refused (`profile: refused — [a-z0-9_]+`) | refused, never a path | one literal, refused | reaches Python, validated the same | n/a — no CONFIRM | `tests/test_makefile.py::test_writeback_and_pipeline_pass_profile_as_one_literal`; `tests/test_writeback.py::test_cli_refuses_bad_profile` |
| `make pipeline PROFILE=<p>` | refused | refused, never a path | one literal, refused | validated the same | n/a — no CONFIRM | `tests/test_makefile.py::test_writeback_and_pipeline_pass_profile_as_one_literal`; `tests/test_pipeline.py::test_cli_refuses_bad_profile` |

## Review & stack risk

- **code-reviewer** (triggered — `serving/`, `dbt/**`, `Makefile`, `tests/`): the
  write-back reads only `scores_send_time` + `dim_user_current` and re-derives no
  score; `should_replace` strict-greater on data-derived columns; `written_at =
  computed_as_of` (no clock); `tz` from the open row; no sixth macro; the serving
  DDL hand-written; `dim_user_current` a clean open-row CTE; `make pipeline` one
  validated process.
- **security-reviewer** (MANDATORY — `serving/` is a new write path, a sensitive
  surface): `serving/` names no `truth`; `PROFILE` `_Q`-quoted, `unexport`ed,
  validated; no secret path, no committed data; the write-back cannot be steered
  to write outside `send_schedule`; no unguarded destructive target.
- **functionality-tester** (triggered): the DONE command; replace-iff-greater /
  new-user / idempotence / tz / written_at / non-UTC / pipeline-equals-steps;
  each mutation line KILLED and the exclusions reasoned; every Phase 3–6 gate
  byte-identical.
- **coherence-auditor** at exit (mandatory, whole repo): CLAUDE.md Repo map /
  Commands / Current status; §2.9 / §3.1; DECISIONS in-force write-back line;
  PHASES "Delivered" (8a); BACKLOG count 11; that 8a supports 8b (a chain the DAG
  can order).
- Stack risk (first hour, STOP on any surprise, §8): (1) DuckDB `create table if
  not exists` + `primary key` + a DELETE-then-INSERT upsert converging to a no-op
  on a re-run (confirm `should_replace` false on a tie leaves the table
  byte-identical); (2) the write-back and `dim_user_current` reading from the same
  `data/<p>.duckdb` the dbt build wrote (one file, one connection lifecycle);
  (3) `written_at = computed_as_of` surviving the round-trip through a `timestamp`
  column identically (no truncation) across two runs.

## Out of scope (deferred, recorded)

- The Airflow DAG, `docker-compose`, `make test-int-airflow`, and backfill≡union —
  **8b** (this sub-phase is the chain minus the scheduler).
- The Spanner write-back target and the `TARGET` flag — **Phase 10** (§3.3); 8a is
  DuckDB only.
- A frozen `send_schedule` golden under `fixtures/tiny/expected/` — **not taken**:
  `send_schedule` is pinned by a content hash + row count in `tests/pins.py` and
  by the self-comparisons (pipeline-equals-steps, write-back-twice), so no
  `fixtures/tiny/` re-freeze is needed (the Phase 7 "keep it out of the golden"
  pattern). Revisit if a cross-commit reference file is ever wanted.
- A `medium` write-back / pipeline run — the tiny chain proves correctness
  (in-process, cheap); `medium` is a BACKLOG row if a survivor class needs it.
