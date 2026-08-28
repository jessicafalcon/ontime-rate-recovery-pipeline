# Phase 8b — Airflow DAG, `test-int-airflow`, backfill≡union (PROPOSED)

Contract for the `phase-8b-airflow-dag` branch (sub-phase 8b, the second half of
the Phase 8 ⭐ checkpoint). Source: `docs/PHASES.md` Phase 8, split 8a/8b —
Done-when clause 1's **DAG side** (`make pipeline` ≡ a triggered DAG run) and
clause 2 (**backfill ≡ union**). Depends on Phase 8a merged (PR #10, `68bff04`).

**Status: PROPOSED — do not start until approved.** One new package on the
allowlist: `apache-airflow` **via Docker only** (Phase 8) — it never enters the
venv / `uv.lock`; the DAG is exercised behind `OTR_INT`, which only the
`test-int-*` targets export, so CI never runs it. No other new dependency: the
DAG is BashOperators over existing `make` targets, and `make dbt-build` gains a
`THROUGH` that threads into the loader it already calls.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what main (`68bff04`, PR #10, includes 8a) actually
is, and the carry-overs due this sub-phase. Items marked **design change** were
**approved 2026-08-27** in the reconciliation pass; the rest are facts the spec
pins. Read off main: `serving/cli.py` (`pipeline` = `dbt build → eval →
write-back`, one process, three steps), `loader/cli.py` (`dbt_build` calls
`load(profile)` with no `THROUGH`), `loader/load.py` (`event_files(through)`,
one DuckDB file per profile), and §3.1 as written.

**0. 8a merged; Phase 9a stays parked.** 8a (write-back + `make pipeline`) is on
main as PR #10. Phase 9a (`phase-9-gcp-foundation`, `infra/`, `tf-*`) remains set
aside — its surface is disjoint from 8b's (`orchestration/`, `loader/`,
`Makefile`) and it reconciles against a main including all of Phase 8 when it
resumes. The committed BACKLOG baseline is **11** open rows (8a opened the
`model_version` string-ordering row); 8b closes none and opens none (item 8).
Not a design change — a repo-state note, recorded so the audit trail is intact.

**1. The DAG is BashOperators over `make` targets, no logic — design change.**
Invariant first: *the ordered task commands of the DAG are exactly `make
pipeline`'s ordered steps* (`dbt build → eval → write-back`), the build
parameterised by the interval's `THROUGH`. Mechanism: `orchestration/dags/
pipeline_dag.py` wires one `BashOperator` per step, ordered `build >> eval >>
writeback`; the `data_interval → THROUGH` mapping is a **literal Jinja token in
the command string** — `make dbt-build PROFILE=<p> THROUGH={{ data_interval_end
| ds }}` — which **Airflow** renders, so we compute nothing (holds "Airflow
contains no logic", DECISIONS in-force). The ordered `(task_id, command)` list
lives in an **Airflow-free manifest** `orchestration/tasks.py` that both the DAG
file and the offline structure test import (single source of truth; the DAG file
is then pure wiring, and the test needs no `import airflow` — `apache-airflow` is
not in the venv). `orchestration/` is a pipeline dir, so
`tests/test_truth_isolation.py` covers it automatically (auto-derived
`PIPELINE_DIRS`, Phase 0) and it never names `truth`. The Docker image is `FROM
apache-airflow`, `uv sync` + the repo mounted, `data/` writable. **Approved.**
Rejected: computing `THROUGH` in a Python callable (logic in the DAG); an
`airflow.decorators` TaskFlow graph (indirection over four `make` lines).

**2. The `dbt-build` re-load clobber, resolved by a THROUGH-aware build —
design change.** `loader/cli.py::dbt_build` calls `load(profile)` with no
`through` (line 105), so a DAG `make load THROUGH=<ds>` landing is immediately
wiped by the build's full reload — the per-interval landing the backfill needs
never survives. Resolution (chosen over a build-only path): **`make dbt-build`
gains an optional `THROUGH` it threads into its internal `load(profile,
through)`.** Invariant: *a per-interval `make dbt-build … THROUGH=<ds>` lands and
builds only files with upload date ≤ `<ds>`.* Backward-compatible: `through`
defaults to `""` → `None` → loads all, so today's `make dbt-build PROFILE=tiny`
and `make pipeline` (which calls `dbt_build(profile, "")`) are byte-for-byte
unchanged and no Phase 3–6 golden moves; the filter itself is Phase 7's existing
`event_files(through)` and `validate_through`. The DAG is then **three** tasks
mirroring `make pipeline`'s three steps exactly, making "DAG ≡ pipeline" a clean
structural equality. **Approved.** Rejected: a build-only `make dbt-build-only`
+ a separate `make load THROUGH=` task (matches a literal four-task sketch but
diverges from `make pipeline`'s three-step shape and adds a make target — more
surface, weaker equivalence).

**3. DuckDB single-writer across separate tasks — prove it, design change.** In
8a the chain is one process; the DAG runs the three steps as **separate
processes** on `data/<p>.duckdb`, each a clean open→close (loader's `connect` /
`finally: con.close()`, dbt, eval, and the write-back all open and close the
file). Overlap is **prevented, not caught by lock errors**: **`max_active_runs=1`**
on the DAG serialises catchup intervals (no two runs touch the file at once) and
`build >> eval >> writeback` linearises the processes within a run.
`test-int-airflow` **demonstrates** the hand-off by driving a full catchup to
all-green — the green run is the proof the sequential lock hand-off holds across
processes. **Approved.** Rejected: relying on DuckDB's file lock to error on
overlap (a race turned into an exception is not a guarantee).

**4. `make pipeline` ≡ the DAG (Done-when clause 1) — fact (two tests).** An
**offline structure test** (`tests/test_dag_structure.py::
test_dag_tasks_are_the_pipeline_steps_in_order`) imports the Airflow-free
`orchestration/tasks.py` manifest and asserts its ordered commands are `make
pipeline`'s three steps in order (the build carrying `THROUGH`); the DAG is built
from the same manifest, so DAG == manifest by construction. A **container test**
(`tests/integration/test_int_airflow.py::test_dag_run_matches_make_pipeline`,
behind `OTR_INT`) asserts a real `airflow dags test` run's `scores_send_time`
**and** `send_schedule` are byte-identical to `make pipeline`'s (pinned
`SEND_SCHEDULE_SHA256_TINY`). The pipeline is the DAG minus the scheduler.

**5. Backfill over three intervals ≡ one union run (Done-when clause 2) — fact.**
The interval→`THROUGH` cut on tiny: **`2026-01-07`**, **`2026-01-12`**
(`LANDING_SPLIT_TINY`), **`2026-01-13`** (`LATE_FILE_TINY` = the union). An
**offline** test at the make / write-back level
(`tests/test_backfill.py::test_three_through_landings_equal_the_union`): three
`THROUGH` landings, each followed by the full chain into one DB, land a final
`send_schedule` byte-identical to a single union run. The **DAG-level** catchup
is the `catchup=True` backfill in `test-int-airflow`. It holds because
scores/features/marts are **table** (fully recomputed each build over the raw ≤
`THROUGH`); stg/attribution **converge** to the union at the final landing
(Phase 7); `computed_as_of = max(client_event_time)` is **monotone** as opens
arrive, so replace-iff-greater overwrites every intermediate row and the union
state wins; and per-row `written_at = computed_as_of` (never a batch stamp)
keeps the union row byte-identical. A new pin
`BACKFILL_THROUGHS_TINY = ("2026-01-07", "2026-01-12", "2026-01-13")` names the
cut (the middle two already exist).

**6. `test-int-airflow` behind `OTR_INT` — design change.** The first
`test-int-*` target; it exports `OTR_INT=1` **in-recipe** so `tests/integration/`
collects (conftest skips it otherwise), spins the container, runs the DAG, and
tears it down (`docker compose down -v`); **CI never runs it**. The container is
**lean**: a single service, `AIRFLOW__CORE__EXECUTOR=SequentialExecutor` + a
SQLite metadata DB, driven by **`airflow dags test`** (a synchronous dag run — no
webserver/scheduler/Postgres) and a small `backfill` for catchup. Each
`BashOperator` still spawns its own subprocess, so the single-writer hand-off in
item 3 is exercised for real. `serving/` and `orchestration/` are the sensitive
surface, so **security-reviewer** runs (item 8's review table). **Approved.**
Rejected: the upstream `apache/airflow` `docker-compose` (webserver + scheduler +
Postgres + Redis — heavy machinery for a synchronous single-DAG test).

**7. Determinism carve-outs — fact.** Asserted: `scores_send_time` and
`send_schedule` (all columns — data-derived, `written_at = computed_as_of`; no
clock reaches either). Excluded (non-deterministic by nature, nothing asserted
reads them): Airflow **run ids**, **task-instance timings**, and dbt/Airflow
**logs** — the standing determinism-policy carve-out, restated for the scheduler.

**8. Drift to correct at exit — facts.** `CLAUDE.md`: Repo map (`orchestration/`
from "(Phase 8b)" to present — `dags/pipeline_dag.py`, `tasks.py`, the
`docker-compose`); Commands (`test-int-airflow` out of "Later phases add" into
live Commands; `make dbt-build … [THROUGH=<date>]` documented); allowlist note
(`apache-airflow` via Docker only, not in `uv.lock`); Current status (→ Phase 8b;
Phase 8 checkpoint closed); Open BACKLOG rows: **11** (unchanged). `docs/PHASES.md`:
Phase 8 "Delivered" (8b portion; the ⭐ checkpoint closed). `DECISIONS.md`: Phase
8b appendix (the DAG shape + `THROUGH` templating, the THROUGH-aware build, the
single-writer guarantee, the lean `test-int-airflow`, backfill≡union basis);
"Airflow contains no logic" already in force (8a), now realised. `docs/
ARCHITECTURE.md`: §3.1's Airflow row already reads "may NOT contain logic"; §8
Gotchas gains an entry only if a Docker/Airflow surprise lands live. **BACKLOG:**
no row closes and none opens; two open rows are **re-checked and re-deferred with
triggers unchanged** — `THROUGH` validated by shape not calendar (8b now feeds
`THROUGH` from `{{ data_interval_end | ds }}`, but it still only string-compares
to file names — never a path or a SQL predicate — so the trigger, "`THROUGH`
reaches a path or a query", is **not** pulled) and `model_version` string
ordering (trigger is Phase 10's first version bump).

Design changes above — items 1, 2, 3, 6 — **approved 2026-08-27**. The contract
sections (Why, DONE, Done-when, Evidence, Invariants + mutations, Pinned
decisions, Scope, Record updates, Threat model, Review & stack, Out of scope)
land in the next commit and are the binding spec.
