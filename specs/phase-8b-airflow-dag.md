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
`model_version` string-ordering row); 8b closes none, and Amendments 1–2 open two
(item 8).
Not a design change — a repo-state note, recorded so the audit trail is intact.

**1. The DAG is BashOperators over `make` targets, no logic — design change.**
Invariant first: *the ordered task commands of the DAG are exactly `make
pipeline`'s ordered writing steps* (`dbt build → write-back`; `eval` is a
union-only gate — Amendment 1), the build parameterised by the interval's
`THROUGH`. Mechanism: `orchestration/dags/pipeline_dag.py` wires one
`BashOperator` per step, ordered `dbt_build >> writeback`; the `data_interval →
THROUGH` mapping is a **literal Jinja token in the command string** — `make
dbt-build PROFILE=<p> THROUGH={{ data_interval_end
| ds }}` — which **Airflow** renders, so we compute nothing (holds "Airflow
contains no logic", DECISIONS in-force). The ordered `(task_id, command)` list
lives in an **Airflow-free manifest** `orchestration/tasks.py` that both the DAG
file and the offline structure test import (single source of truth; the DAG file
is then pure wiring, and the test needs no `import airflow` — `apache-airflow` is
not in the venv). `orchestration/` is a pipeline dir, so
`tests/test_truth_isolation.py` covers it automatically (auto-derived
`PIPELINE_DIRS`, Phase 0) and it never names `truth`. The Docker image is `FROM
apache-airflow`, `uv sync` (deps into a separate venv) + the repo **COPYd**,
`data/` writable (the landed shape; no bind mount — Amendment 2). **Approved.**
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
`event_files(through)` and `validate_through`. The DAG then mirrors `make
pipeline`'s steps (three at reconciliation; **Amendment 1** later dropped `eval`
→ two writing steps), making "DAG ≡ pipeline" a clean structural equality.
**Approved.** Rejected: a build-only `make dbt-build-only` + a separate `make
load THROUGH=` task (matches a literal four-task sketch but diverges from `make
pipeline`'s chain shape and adds a make target — more surface, weaker
equivalence).

**3. DuckDB single-writer across separate tasks — prove it, design change.** In
8a the chain is one process; the DAG runs its tasks (two writing steps after
Amendment 1) as **separate processes** on `data/<p>.duckdb`, each a clean
open→close (loader's `connect` /
`finally: con.close()`, dbt and the write-back both open and close the
file). Overlap is **prevented, not caught by lock errors**: **`max_active_runs=1`**
on the DAG serialises catchup intervals (no two runs touch the file at once) and
`dbt_build >> writeback` linearises the processes within a run.
`test-int-airflow` **demonstrates** the hand-off by driving a full catchup to
all-green — the green run is the proof the sequential lock hand-off holds across
processes. **Approved.** Rejected: relying on DuckDB's file lock to error on
overlap (a race turned into an exception is not a guarantee).

**4. `make pipeline` ≡ the DAG (Done-when clause 1) — fact (two tests).** An
**offline structure test** (`tests/test_dag_structure.py::
test_dag_tasks_are_the_pipeline_writing_steps_in_order`) imports the Airflow-free
`orchestration/tasks.py` manifest and asserts its ordered commands are `make
pipeline`'s writing steps in order (`dbt build → write-back`, the build carrying
`THROUGH`; eval excluded — Amendment 1); the DAG is built from the same manifest,
so DAG == manifest by construction. A **container test**
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
`send_schedule` byte-identical to a single union run. The **DAG-level** proof is
the explicit three-interval backfill in `test-int-airflow` (`airflow dags test`
at each `THROUGH`; `catchup=False`, Amendment 2). It holds because
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
Phase 8 checkpoint closed); Open BACKLOG rows: **13** (this reconciliation opened
none; Amendments 1–2 later opened two — build-only split and the `computed_as_of`
discriminator gap). `docs/PHASES.md`:
Phase 8 "Delivered" (8b portion; the ⭐ checkpoint closed). `DECISIONS.md`: Phase
8b appendix (the DAG shape + `THROUGH` templating, the THROUGH-aware build, the
single-writer guarantee, the lean `test-int-airflow`, backfill≡union basis);
"Airflow contains no logic" already in force (8a), now realised. `docs/
ARCHITECTURE.md`: §3.1's Airflow row already reads "may NOT contain logic"; §8
Gotchas gains an entry only if a Docker/Airflow surprise lands live. **BACKLOG:**
no row closes; Amendments 1–2 open two (build-only split; `computed_as_of`
discriminator gap); two existing rows are **re-checked and re-deferred with
triggers unchanged** — `THROUGH` validated by shape not calendar (8b now feeds
`THROUGH` from `{{ data_interval_end | ds }}`, but it still only string-compares
to file names — never a path or a SQL predicate — so the trigger, "`THROUGH`
reaches a path or a query", is **not** pulled) and `model_version` string
ordering (trigger is Phase 10's first version bump).

Design changes above — items 1, 2, 3, 6 — **approved 2026-08-27**. The sections
below are the contract.

## Amendment 1 — eval leaves the per-interval DAG (2026-08-28)

Design change (who-runs-what), **approved 2026-08-28**, committed alone before
implementation. `make eval` asserts the **full-data** MAE/accuracy pins and reads
`truth/`, so it fails on every partial backfill interval (verified on
`THROUGH=2026-01-07`: accuracy 0.871, MAE 0.928762) and — being upstream of
write-back — would skip that interval's write and redden the run. It is **removed
from the DAG**: the per-interval chain is **`dbt-build
THROUGH={{ data_interval_end | ds }}` >> `writeback`** (Option A retained — the
THROUGH-aware build keeps landing and build in one task, so no interval is built
against a landing it did not load). `eval` stays a **union-only validation gate**
in `make pipeline` and CI; it writes neither `scores_send_time` nor
`send_schedule`, so the DAG's two output tables remain **byte-identical** to `make
pipeline`'s. This reverses the earlier "the DAG steps ARE `make pipeline`'s three
steps": the DAG steps are `make pipeline`'s two **writing** steps. Invariant
restored (sharpens invariants 4–5): *for every interval i, the DAG's
`send_schedule` after i equals `make pipeline` over a landing ≤ THROUGH_i; after
the last interval it equals the union* — `test_backfill.py` proves the offline
half, `test-int-airflow` proves it across process boundaries. Records fixed by
this amendment: the ARCHITECTURE diagram + CLAUDE.md's copy (`AIRFLOW … dbt build
→ write-back`), `docs/PHASES.md` Phase 8 goal, the `Makefile` pipeline comment, a
DECISIONS Phase 8b entry, and a BACKLOG row (below). **Deferral recorded:**
splitting load from build (a build-only target + a separate `load` task) is the
**Phase 9** shape — there the load is GCS→BigQuery and `dbt_build(TARGET=bigquery)`
calling the DuckDB `load()` (`loader/cli.py`) is already wrong, so the split earns
its keep; doing it now is speculative churn against an implemented, committed
shape (BACKLOG, trigger Phase 9). The pinned decisions, Done-when, invariants and
Evidence below are updated to this amendment.

## Amendment 2 — review round 1 design findings (2026-08-28)

Three findings from review round 1, **approved 2026-08-28**, committed alone
before their fixes.

- **The DAG must never auto-catch-up-to-now (finding #5).** As first landed,
  `catchup=True` + a past `start_date` + `DAGS_ARE_PAUSED_AT_CREATION=False`
  meant any scheduler start would backfill every day since 2026-01-06 (hundreds
  of runs, growing) — the catchup-to-`now` pinned decision 5 explicitly rejects,
  latent only because 8b runs no scheduler (`airflow dags test` drives explicit
  dates). Fix: **`catchup=False`** on the DAG and drop the
  `DAGS_ARE_PAUSED_AT_CREATION=False` override (the safe default is paused).
  Backfill≡union is unchanged — it is proven by explicit `airflow dags test
  <THROUGH>` / `airflow dags backfill -s -e`, which ignore `catchup`; the
  property never depended on auto-catchup. "catchup for backfill" reframes to
  "backfill via explicit `dags test`/`backfill` over a range." Touches
  `pipeline_dag.py`, `Dockerfile`, and the ARCHITECTURE/CLAUDE diagram lines.
- **Invariant 5's precondition: a score change must advance `computed_as_of`
  (finding #4).** `backfill ≡ union` (invariant 5) relies on the write-back's
  replace-iff-greater over `(model_version, computed_as_of)`, where
  `computed_as_of = max(client_event_time)` of the opens in the window. This is a
  valid discriminator **only when every score change also advances that max** —
  which holds for the backfill's monotone-superset landings adding forward-dated
  opens (each `THROUGH` grows the horizon). A landing that changes scores via
  **only back-dated opens** (or a max that recedes as opens leave the 30-day
  window) would tie `computed_as_of` and leave the stale serving row — `backfill
  ≠ union`. This is a limitation of the **8a/Phase-5 discriminator**, unreachable
  on tiny (verified: `THROUGH=2026-01-12` scores already equal the union, so the
  late file changes nothing) and out of 8b's scope to fix (it would change the
  8a write-back contract — a separate concern). Recorded as a **BACKLOG row**
  (below); no code change here. Invariant 5 is stated with this precondition.
- **Manual vs scheduled interval semantics (finding #6).** The stack-risk note
  said `THROUGH = L+1` and DECISIONS said `L`; both are half-right. Settled by
  observation: `airflow dags test D` renders `data_interval_end = D`, so
  **THROUGH = D** — what every 8b test uses (args ∈ `BACKFILL_THROUGHS_TINY`). A
  *scheduled* `@daily` run owning `[D, D+1)` renders `data_interval_end = D+1`,
  so THROUGH = D+1 — the same template over a different interval, relevant at
  Phase 11 (Composer). No code change; the records are reconciled to state both.

## Teaching notes (first appearance in this project)

- **Airflow data intervals.** A scheduled DAG run stands for a logical time
  window `[data_interval_start, data_interval_end)` — the slice of data that run
  *owns* — not "the moment it executes". A task reads that window through
  templating (`{{ data_interval_end | ds }}` renders the window's end as a
  `YYYY-MM-DD` string). We use it to turn each run into a `THROUGH` landing cut
  with **no wall clock** on the data path — Airflow supplies the date, the loader
  filters files by it.
- **Catchup.** When a DAG's `start_date` is in the past, `catchup=True` would
  make Airflow schedule a run for **every** interval from `start_date` to now, in
  order — replaying history one window at a time. That auto-catchup-to-`now` is a
  foot-gun for a past `start_date` (hundreds of runs), so this DAG sets
  **`catchup=False`** (Amendment 2) and a backfill is invoked **explicitly** over
  a bounded range — `airflow dags test <date>` (one interval) or `airflow dags
  backfill -s -e` — which ignore `catchup`. The tests drive explicit dates, so
  the run set is fixed and deterministic.
- **Backfill.** Re-running historical intervals on demand, each with its own data
  interval. Done-when clause 2 is a backfill: three intervals must land the same
  `send_schedule` as one run over the union of their data.
- **Scheduler-ordered chain vs `make pipeline`.** `make pipeline` runs `dbt build
  → eval → write-back` as **one** local process in a fixed order. The DAG runs the
  **writing** steps (`dbt_build >> writeback`) as separately-scheduled tasks
  ordered by dependencies, data-interval-aware and re-runnable, with retries and a
  run history. `eval` is a union-only validation gate that reads truth and writes
  no table (Amendment 1), so it stays in `make pipeline`/CI and the DAG's two
  output tables still match `make pipeline`'s byte-for-byte — the scheduler adds
  ordering and bookkeeping, never logic.

## Why

Phase 8a proved the chain as one process (`make pipeline`) lands byte-identical,
idempotent `scores_send_time` and `send_schedule`, but nothing **orders** it the
way a scheduler would, and nothing proves a data-interval-aware backfill
converges to a single run. Phase 8b wraps that same chain in a Docker-local
Airflow DAG and proves the wrapper agrees (DAG ≡ `make pipeline`) and that a
backfill over three intervals equals one union run — closing the Phase 8 ⭐
checkpoint. A fix PR cannot carry it: it adds a package (Airflow, via Docker), a
new `orchestration/` package with a `docker-compose`, an integration target, and
a build-path change (`make dbt-build` gains `THROUGH`).

## The central constraint

**The DAG orders the existing chain and adds no logic; its output equals `make
pipeline`'s byte-for-byte, and a backfill equals the union.** `scores_send_time`
and `send_schedule` are unchanged from 8a; the DAG is BashOperators over `make`
targets with the interval rendered to `THROUGH` by Airflow; `make dbt-build`'s
new `THROUGH` only *narrows* the landing (unset ⇒ loads all, so every Phase 3–6
golden is untouched). Any Python computing the interval, any drift of the DAG's
steps from `make pipeline`'s, a batch (non-per-row) `written_at`, a new package
in `uv.lock`, or a moved Phase 3–8a golden is a STOP.

## DONE command

```
make review-gate SPEC=specs/phase-8b-airflow-dag.md && make dbt-build PROFILE=tiny FULL=yes && make attribution-golden PROFILE=tiny && make report PROFILE=tiny && make scores-golden PROFILE=tiny && make eval PROFILE=tiny && make pipeline PROFILE=tiny && make test-int-airflow
```

- `make review-gate SPEC=…` — offline suite (the DAG structure test == `make
  pipeline`'s steps; the THROUGH-aware build lands only ≤ the cut, and unset
  loads all; backfill≡union at the make/write-back level; `apache-airflow` absent
  from `uv.lock`; `orchestration/` names no `truth`), ruff, check-docs, Evidence
  ids, Record-updates files. The THROUGH-aware build and backfill≡union are
  proven here **offline in tmp DBs** (`tests/test_through_build.py`,
  `tests/test_backfill.py`) so they never disturb `data/<p>.duckdb`.
- `make dbt-build PROFILE=tiny FULL=yes` — the union baseline (a full refresh, so
  the golden gates read a converged DB regardless of prior landing state);
  `dbt-build OK: tiny/duckdb`.
- `make attribution-golden / report / scores-golden / eval PROFILE=tiny` — every
  Phase 3–6 gate byte-identical (the `THROUGH` plumbing did not disturb the
  default build): `0 differ`; `ontime_rate 0.609756`; accuracy `1.000`, MAE
  `0.816201`.
- `make pipeline PROFILE=tiny` — the reference chain; `pipeline OK: tiny`.
- `make test-int-airflow` — spins the lean Airflow container on a **fresh** DB,
  drives the DAG at explicit dates (`airflow dags test <THROUGH>`), asserts the
  run's `scores_send_time` and `send_schedule` byte-identical to `make
  pipeline`'s and the three-interval catchup equal to the union
  (`SEND_SCHEDULE_SHA256_TINY`), tears the container down; exports `OTR_INT=1`
  in-recipe so `tests/integration/` collects.

## Done-when

1. **The DAG has no logic; its steps are `make pipeline`'s writing steps.** For
   all tasks, the DAG's ordered commands are `make pipeline`'s two writing steps
   (`dbt build → write-back`) in order, the build parameterised by `THROUGH` via
   Airflow templating; `eval` is excluded (a union-only gate that reads truth and
   writes no table — Amendment 1); no Python computes the interval and every
   operator is a `BashOperator`. *Evidence: row 1.*
2. **THROUGH-aware build.** For all `<ds>`, `make dbt-build … THROUGH=<ds>` lands
   and builds only files with upload date ≤ `<ds>`; `THROUGH` unset loads all and
   leaves every Phase 3–6 golden byte-identical. *Evidence: row 2.*
3. **DAG ≡ `make pipeline`.** A triggered DAG run produces `scores_send_time` and
   `send_schedule` byte-identical to `make pipeline`'s (pinned hash). *Evidence:
   row 3.*
4. **Backfill ≡ union.** A backfill over the three intervals (`THROUGH`
   `2026-01-07`, `2026-01-12`, `2026-01-13`) lands a `send_schedule`
   byte-identical to one union run, and re-running an interval is a no-op.
   *Evidence: row 4.*
5. **Single-writer, no clock.** For all catchup runs, `data/<p>.duckdb` is
   written by one process at a time (`max_active_runs=1` + linear task order);
   nothing asserted reads an Airflow run id, task timing, or log; a build under a
   non-UTC host is identical. *Evidence: row 5.*
6. **Carry-forward.** `apache-airflow` is Docker-only (absent from `uv.lock`);
   `orchestration/` names no `truth`; the generator, `fixtures/tiny/`, `dbt/`, the
   serving **logic** (`serving/writeback.py`/`ddl.sql`) and every Phase 3–8a output
   are unchanged (only `serving/cli.py`'s docstring updated, Amendment 1); exactly
   five dispatch macros. *Evidence: row 6.*

(≤ 6. `docs/PHASES.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_dag_structure.py::test_dag_tasks_are_the_pipeline_writing_steps_in_order` (manifest ordered commands == `dbt build → write-back`, `PROFILE=tiny`, eval excluded — Amendment 1); `::test_dag_config_pins_the_safe_scheduling` (AST-parses `pipeline_dag.py`: `catchup is False`, `max_active_runs==1`, `start_date` a `datetime`); `::test_dag_uses_only_one_bash_operator_over_tasks` (exactly one operator ctor, BashOperator, `cwd` wired, no `PythonOperator`/`python_callable`); `::test_dag_declares_dependencies`; `::test_through_token_is_data_interval_end`; `tests/integration/test_int_airflow.py::test_dag_edges_and_operators` (the real DAG object, container) |
| 2 | `tests/test_through_build.py::test_dbt_build_through_lands_only_files_le_cut` (after `loader.dbt_build(..., through=<ds>)`, `max(server_upload_time)` in `raw.events` ≤ `<ds>` and the file count matches the subset); `::test_dbt_build_no_through_loads_all`; `make dbt-build PROFILE=tiny THROUGH=2026-01-07` → `landing ≤ 2026-01-07`; `make attribution-golden / report / scores-golden / eval PROFILE=tiny` all `0 differ`/pins after the full build |
| 3 | `tests/integration/test_int_airflow.py::test_dag_run_matches_make_pipeline` (container `airflow dags test pipeline 2026-01-13` — dag_id `pipeline`, THROUGH = the arg date; **both** `scores_send_time` (vs the frozen golden) and `send_schedule` (== `SEND_SCHEDULE_SHA256_TINY`) byte-identical to `make pipeline`) |
| 4 | `tests/test_backfill.py::test_three_through_landings_equal_the_union` (offline: the make/write-back chain over `BACKFILL_THROUGHS_TINY` into one DB == a union run, `SEND_SCHEDULE_SHA256_TINY`); `::test_backfill_interval_twice_is_a_noop`; `tests/integration/test_int_airflow.py::test_catchup_backfill_equals_union` (container, three explicit logical dates) |
| 5 | `tests/test_dag_structure.py::test_dag_config_pins_the_safe_scheduling` (`max_active_runs==1` and `catchup is False` by parsed value); `tests/integration/test_int_airflow.py::test_catchup_runs_green` (the all-green three-interval run demonstrates the single-writer hand-off across processes); non-UTC identity carried by `tests/test_incremental.py::test_build_under_tokyo_is_identical` (the build is unchanged) |
| 6 | `tests/test_airflow_docker_only.py::test_apache_airflow_not_in_uv_lock`; `tests/test_truth_isolation.py` (now covers `orchestration/`); `git diff main -- generator/ fixtures/ dbt/ serving/writeback.py serving/ddl.sql` empty (only `serving/cli.py`'s docstring updated for Amendment 1); `tests/test_dbt_conventions.py::test_exactly_five_dispatch_macros` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **DAG == pipeline's writing steps, in order, no logic.** For all tasks, the DAG's ordered commands are `make pipeline`'s two writing steps (`dbt build → write-back`), wired `dbt_build >> writeback`, every operator a `BashOperator`, the interval supplied only by Airflow templating; `eval` is excluded (Amendment 1). | `test_dag_tasks_are_the_pipeline_writing_steps_in_order`; `test_dag_config_pins_the_safe_scheduling`; `test_dag_uses_only_one_bash_operator_over_tasks`; `test_dag_declares_dependencies`; `test_through_token_is_data_interval_end`; `tests/integration/test_int_airflow.py::test_dag_edges_and_operators` (the real DAG object's task set + `dbt_build → writeback` edge + BashOperator-only, in the container) — declarative wiring pinned by AST parsing + the container object, no Python guard to mutate |
| 2. **THROUGH-aware build.** For all `<ds>`, a build with `THROUGH=<ds>` sees only files uploaded ≤ `<ds>`; unset sees all. | `test_dbt_build_through_lands_only_files_le_cut`; `test_dbt_build_no_through_loads_all`; mutation `loader/load.py::event_files invert-guard` (flips the `through is None` guard → a per-interval build sees all files / an unset build errors) |
| 3. **Interval→THROUGH validation.** For all `THROUGH` reaching the build, an ill-formed value is refused before any landing. | `tests/test_makefile.py::test_dbt_build_passes_through_as_one_literal`; `test_through_build.py::test_build_refuses_bad_through`; mutation `loader/cli.py::validate_through invert-guard` (a valid date dies / a malformed one passes) |
| 4. **DAG ≡ pipeline at runtime.** For a triggered run, `scores_send_time` and `send_schedule` are byte-identical to `make pipeline`'s. | `test_dag_run_matches_make_pipeline` (container) |
| 5. **Backfill ≡ union** (precondition: each score change advances `computed_as_of` — Amendment 2). For the three-interval backfill (intervals spaced ≤ `lookback_days`; landings monotone supersets adding forward-dated opens), the final `send_schedule` equals a union run and a re-run of an interval is a no-op. | `test_three_through_landings_equal_the_union`; `test_backfill_interval_twice_is_a_noop`; `test_catchup_backfill_equals_union` (container) |
| 6. **Single-writer, no clock.** For all catchup runs, `data/<p>.duckdb` is written by one process at a time; nothing asserted reads a run id/timing/log; a non-UTC host build is identical. | `test_dag_config_pins_the_safe_scheduling` (`max_active_runs==1`); `test_catchup_runs_green`; `test_build_under_tokyo_is_identical` |
| 7. **Carry-forward.** `apache-airflow` is absent from `uv.lock`; `orchestration/` names no `truth`; generator/`fixtures`/`dbt` and the serving **logic** (`writeback.py`/`ddl.sql`) unchanged (only `serving/cli.py`'s docstring updated, Amendment 1); five dispatch macros. | `test_apache_airflow_not_in_uv_lock`; `tests/test_truth_isolation.py`; `git diff main -- generator/ fixtures/ dbt/ serving/writeback.py serving/ddl.sql`; `test_exactly_five_dispatch_macros` |

Rules — the DAG file is declarative wiring with no Python guard (invariant 1 is
pinned by the structure test, per the template rule that a property upheld only
in non-mutable code names its test). The two mutable Python guards are on the
build path: `event_files` (invariant 2, the file filter now reached from the
build) and `validate_through` (invariant 3, the interval→cut validation now fed
by the DAG) — both Phase 7 functions, newly load-bearing for 8b's build/backfill
path. The through-*threading* itself (`dbt_build` passing `through` to `load`)
is not expressible by the four operators; it is pinned by the direct test
(`test_dbt_build_through_lands_only_files_le_cut`).

```mutations
loader/load.py::event_files       invert-guard
loader/cli.py::validate_through   invert-guard
```

Equivalent-mutant / refused exclusions, named up front and verified once at
implementation on a scratch copy (the Phase 6/7 pattern):

- `loader/load.py::event_files swap-sort-key` — REFUSED. A landing is a **set**
  recreated each load and `raw.events` has no inherent row order; every
  downstream comparison sorts by its own key, so the file order winners are
  inserted in is unobservable. (Already refused in Phase 7 for the same reason.)
- `loader/cli.py::validate_through constant-return:<v>` — REDUNDANT with
  `invert-guard` (both defeat the refusal); the block keeps the stronger line.

## Pinned decisions (do not re-litigate)

- **The DAG is BashOperators over `make` targets, ordered `>>` (`dbt_build >>
  writeback` — the two writing steps; `eval` is a union-only gate, Amendment 1),
  `THROUGH` via `{{ data_interval_end | ds }}`; the ordered commands live in the
  Airflow-free `orchestration/tasks.py` manifest the DAG and the offline structure
  test share (reconciliation item 1)** — satisfies invariants 1, 4. Airflow
  renders the interval so the DAG holds no logic; the shared manifest makes "DAG
  == pipeline" a fast offline structure test, not a container-only claim.
  Rejected: a Python callable computing `THROUGH` (logic in the DAG); AST-parsing
  the DAG file for the command list (more fragile than one shared manifest).
- **`make dbt-build` gains `THROUGH`, threaded into its internal `load(profile,
  through)`; default `""` ⇒ loads all (item 2)** — satisfies invariant 2.
  Resolves the re-load clobber with Phase 7's existing `event_files` filter and
  is backward-compatible, so no Phase 3–6 golden moves and `make pipeline`
  (`dbt_build(profile, "")`) is unchanged. Rejected: a build-only target + a
  separate `make load THROUGH=` task (a longer DAG that diverges from `make
  pipeline`'s chain — more surface, weaker equivalence).
- **Single-writer by construction: `max_active_runs=1` + `dbt_build >>
  writeback`; `test-int-airflow` demonstrates the catchup hand-off (item 3)** —
  satisfies invariant 6. Overlap is prevented, not caught by a DuckDB lock error;
  each `BashOperator` is a separate subprocess opening and closing the file in
  turn. Rejected: relying on the file lock to error on overlap (a race turned
  into an exception is not a guarantee).
- **Backfill≡union leans on Phase 7 convergence + replace-iff-greater + per-row
  `written_at = computed_as_of`; the cut is `2026-01-07`/`2026-01-12`/`2026-01-13`,
  and the intervals are spaced ≤ `lookback_days` (item 5)** — satisfies invariant
  5. scores/marts are table (recomputed each build), stg/attribution converge to
  the union at the final landing, and `computed_as_of = max(client_event_time)`
  is monotone as opens arrive, so the union interval's rows win the
  replace-iff-greater; a batch `written_at` would break it (rejected in 8a).
  **The convergence precondition is a consecutive-interval gap ≤ `lookback_days`**
  — a wider gap lets a partition be finalized while its late events sit in the
  skipped landings (verified: `07 → full(13)`, a 6-day gap > `lookback_days` 5,
  diverges; `07 → 12 → 13`, max gap 5 = `lookback_days`, converges exactly — the
  Phase 7 `<=` reprocess-window boundary is what makes gap = `lookback_days`
  work). The DAG's `@daily` schedule gives gap 1, always safe; the offline
  3-interval cut sits at the boundary on purpose. Rejected: a fresh union DB per
  backfill (would not prove the incremental + upsert path converges); intervals
  spaced > `lookback_days` (diverge — not a valid backfill of an incremental
  model).
- **`test-int-airflow` is a lean single-service container (`SequentialExecutor` +
  SQLite, `airflow dags test`/`backfill` at explicit logical dates) behind
  `OTR_INT`, exported in-recipe; CI never runs it (item 6)** — satisfies
  invariants 4, 5, 6 at runtime. `airflow dags test <date>` executes a dag run
  synchronously with no scheduler/webserver and a fixed logical date (no wall
  clock); BashOperators still fork subprocesses, so the single-writer hand-off is
  exercised. Rejected: the upstream `apache/airflow` compose (webserver +
  scheduler + Postgres + Redis for a synchronous single-DAG test); catchup-to-now
  (months of runs, and a wall-clock dependence).

## Scope (files)

- `orchestration/__init__.py`, `orchestration/tasks.py` (the ordered
  `(task_id, command)` manifest + `PROFILE = "tiny"`),
  `orchestration/dags/pipeline_dag.py` (BashOperators from the manifest;
  `max_active_runs=1`, `catchup=False` — Amendment 2, `start_date`, `schedule`),
  `orchestration/Dockerfile` + `orchestration/docker-compose.yml` (the lean
  Airflow image; `SequentialExecutor` + SQLite; repo COPYd, `data/` writable)
- `loader/cli.py` (`dbt_build` gains `--through`, threaded to `load`), `Makefile`
  (`dbt-build` forwards `THROUGH`; new `test-int-airflow` target exporting
  `OTR_INT=1`)
- `tests/test_dag_structure.py` (new, offline), `tests/test_through_build.py`
  (new, offline), `tests/test_backfill.py` (new, offline),
  `tests/integration/__init__.py` + `tests/integration/test_int_airflow.py` (new,
  `OTR_INT`), `tests/test_airflow_docker_only.py` (new),
  `tests/test_makefile.py` (`dbt-build` `THROUGH` + `test-int-airflow` literal
  tests), `tests/pins.py` (`BACKFILL_THROUGHS_TINY`)
- Records: `CLAUDE.md`, `docs/PHASES.md`, `DECISIONS.md`
- Untouched by contract: `generator/`, `fixtures/`, `serving/`, `dbt/`, `eval/`,
  `pyproject.toml`, `uv.lock` (no venv package), `infra/` (parked 9a),
  `docs/ARCHITECTURE.md` §3.1 (Airflow row already reads "may NOT contain logic")

## Record updates (REQUIRED)

- [ ] `CLAUDE.md` — Current status (→ Phase 8b; the Phase 8 ⭐ checkpoint closed);
      Commands (`test-int-airflow`; `make dbt-build … [THROUGH=<date>]`); Repo map
      (`orchestration/` present — `dags/pipeline_dag.py`, `tasks.py`, the
      `docker-compose`); Conventions/allowlist (apache-airflow via Docker only,
      not in `uv.lock`); Architecture diagram AIRFLOW row (`dbt build →
      write-back`, eval out — Amendment 1; `catchup=False` — Amendment 2); Open
      BACKLOG rows: **13** (two open)
- [ ] `docs/PHASES.md` — Phase 8 "Delivered" (8b portion; the ⭐ checkpoint
      closed); Phase 8 goal line (the DAG chains `dbt build → write-back`, eval a
      union gate — Amendment 1)
- [ ] `DECISIONS.md` — Phase 8b appendix (eval out of the DAG — truth isolation +
      union-only pins; `THROUGH` on `dbt-build`, not a separate load task — one
      landing owner per run, the split is the Phase 9 shape; DAG shape +
      templating; single-writer guarantee; lean `test-int-airflow`; backfill≡union
      basis)
- [ ] `BACKLOG.md` — **open two rows**: split load from `dbt build` (a build-only
      target), trigger Phase 9 (Amendment 1); and `computed_as_of` not a complete
      score-change discriminator (8a/5 gap, Amendment 2), trigger a
      score-changing-back-dated-opens backfill / Phase 10; count **11 → 13**. The
      `THROUGH`-validated-by-shape and
      `model_version`-string-ordering rows re-checked at 8b exit and re-deferred
      with triggers unchanged (8b feeds `THROUGH` from `{{ data_interval_end |
      ds }}` yet it still only string-compares to file names, never a path)
- [ ] `docs/ARCHITECTURE.md` — the diagram AIRFLOW row (`load → dbt build →
      write-back`, eval out of the DAG — Amendment 1); §3.1 Airflow row already
      reads "may NOT contain logic"; §8 Gotchas only if a Docker surprise lands
- [ ] Spec amendments — none (no later spec is invalidated; Phase 9+ reconcile
      against a main including all of Phase 8 when they start)
- [ ] docs/RESULTS.md, docs/AB_DESIGN.md, docs/METRICS.md, README — none

## Threat model (REQUIRED)

`make dbt-build` newly forwards `THROUGH` (an existing Phase 7 variable, already
`[0-9-]`-shaped, `validate_through`-checked, and only ever a file-name filter —
never a path or a SQL predicate). Note (finding #16): an environment `THROUGH`
does reach the recipe and silently narrows a plain `make dbt-build` — this is the
stated `$(value)` residual (make imports env vars into its table), *asymmetric*
with `$(origin)`-gated `CONFIRM`/`FULL`, but harmless: `THROUGH` cannot escape to
a path and a wrong landing fails loudly at the next gate (a golden/pin diff), so
it does not warrant an `$(origin)` gate. `make test-int-airflow` takes **no**
variable
(it operates on `tiny` by definition; the DAG's `PROFILE=tiny` is a literal in
the manifest) and is non-destructive to tracked files: it writes only the
gitignored `data/tiny.duckdb` (recreated by `make load`) and tears down an
ephemeral Docker stack (`docker compose down -v` removes the stack's own
volumes, nothing under the repo). No cloud, no `CONFIRM` (Docker teardown of an
ephemeral local stack is not a tracked-data delete; a DB reset stays `make
drop-db … CONFIRM=yes`). Residual `MAKEFLAGS` is the standing "mistakes, not a
hostile environment" carve-out.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make dbt-build PROFILE=<p> THROUGH=<d>` | `THROUGH=` ⇒ no cut, loads all (backward-compatible) | refused (`validate_through`, never a path) | one literal, refused | reaches Python, validated the same | n/a — `CONFIRM` still gates a cloud `TARGET` only | `tests/test_makefile.py::test_dbt_build_passes_through_as_one_literal`; `tests/test_through_build.py::test_build_refuses_bad_through` |
| `make test-int-airflow` | no variable — `tiny` by definition | — | — | — | n/a — no `CONFIRM` (non-destructive to tracked files) | `tests/test_makefile.py::test_test_int_airflow_takes_no_variable_and_exports_otr_int` |

## Review & stack risk

- **code-reviewer** (triggered — `loader/`, `Makefile`, `orchestration/`,
  `tests/`): the DAG holds no logic and is BashOperators only; `THROUGH` threads
  correctly and defaults to all; the manifest is the single source of truth; no
  Phase 3–8a golden moves; five dispatch macros; `written_at` still per-row.
- **security-reviewer** (MANDATORY — `orchestration/` is a new surface with a
  `docker-compose` and a container-spinning target): no secrets in the image or
  compose; the DAG cannot be steered off `tiny`/`send_schedule`; `down -v`
  removes only the ephemeral stack; `OTR_INT` gating means CI never spins Docker;
  no committed `data/`/credentials.
- **functionality-tester** (triggered): the DONE command; DAG structure == `make
  pipeline`; the THROUGH-aware build lands only ≤ the cut; backfill≡union offline;
  the container DAG == pipeline and catchup == union; both mutation lines KILLED
  and the exclusions reasoned; every Phase 3–8a gate byte-identical.
- **coherence-auditor** at exit (mandatory, whole repo): CLAUDE.md Repo
  map / Commands / Current status; PHASES "Delivered" (8b; ⭐ closed); DECISIONS
  8b appendix; BACKLOG count 13; that Phase 8 as a whole now supports Phase 9 (a
  chain a scheduler orders, ready for the Composer module).
- Stack risk (first hour, STOP on any surprise, §8): (1) the Airflow image builds
  and `airflow dags test pipeline <date>` (dag_id `pipeline`) runs a full dag run
  synchronously under `SequentialExecutor` + SQLite with no scheduler/webserver;
  (2) `{{ data_interval_end | ds }}` renders to `YYYY-MM-DD` inside a BashOperator
  command — the semantics settled by observation (Amendment 2): `airflow dags
  test D` gives `data_interval_end = D`, so **THROUGH = D** (what the tests use,
  args ∈ `BACKFILL_THROUGHS_TINY`); a *scheduled* `@daily` run owning `[D, D+1)`
  gives `data_interval_end = D+1`, so THROUGH = D+1 — same template, different
  interval (Phase 11/Composer); (3) the **COPYd** repo's `make`/`uv`/`dbt` run
  inside the container and write `data/tiny.duckdb` (no bind mount); the host
  reads a **copy extracted with `docker compose cp`** after the run, then
  `down -v`; (4) the DuckDB file lock releases cleanly between the subprocess
  tasks.

## Out of scope (deferred, recorded)

- **Split load from `dbt build` (a build-only target + a separate `load` task)** —
  **Phase 9** (BACKLOG row). Option A (THROUGH on `dbt-build`) keeps landing and
  build in one task, which is all 8b needs; the split earns its keep only at the
  BigQuery target, where the load is GCS→BigQuery and `dbt_build(TARGET=bigquery)`
  calling the DuckDB `load()` (`loader/cli.py`) is already wrong.
- **`eval` inside the DAG** — **not taken** (Amendment 1). `eval` asserts
  full-data pins and reads truth, so it is a union-only gate in `make pipeline`/CI,
  never a per-interval DAG task.
- **Cloud Composer** (the DAG applied to a managed Airflow) — **Phase 11**
  (`enable_composer` Terraform module, applied once on demo day). 8b is
  Docker-local only.
- **A `medium` DAG / backfill run** — the `tiny` chain proves correctness
  (in-process, cheap); `medium` is a BACKLOG row if a survivor class needs it.
- **`THROUGH` calendar validation** — the existing BACKLOG row (`THROUGH` is
  shape-checked, not calendar-checked); still harmless in 8b (it only filters
  file names), trigger unchanged (`THROUGH` reaching a path or a SQL predicate).
- **`model_version` parsed ordering** — the existing BACKLOG row; trigger is
  Phase 10's first version bump.
- **An empty landing** (a `THROUGH` before the first upload date → empty `raw`) —
  **not taken** (round 2 #24). The DAG never produces one (`start_date`
  2026-01-06 → the first `THROUGH` is 2026-01-07, which lands 4 files); reachable
  only by `airflow dags backfill -s <before 2026-01-04>`. A guard/test lands if a
  caller ever needs an empty-raw build.
