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
