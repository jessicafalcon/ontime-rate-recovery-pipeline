# Phase 12 — Live run and teardown (demo day) (PROPOSED)

Contract for the `phase-12-live-run` branch. Source: docs/PHASES.md Phase 12.
Depends on Phase 11 (PR #17, merged to main `b7f20dc`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
Composer/Spanner are provisioned by the already-pinned `hashicorp/google ~> 6.0`
provider; the DAG (`orchestration/dags/pipeline_dag.py`) and its task manifest
(`orchestration/tasks.py`) were authored in Phase 8b; the BigQuery build and the
Spanner write-back exist (Phases 9b, 10). `apache-airflow` stays Docker-only and
out of `uv.lock` (CLAUDE.md allowlist), unchanged. If `google_composer_environment`
(v6 provider) or the managed Airflow runtime turns out to lack a field this spec
assumes — the DAG-bucket layout, the environment variables it honours — that is a
STOP-and-report, not a workaround (ARCHITECTURE §8).

## Reconciliation against main (first commit on the branch)

Main as it actually is (`b7f20dc`): Phase 11 merged (PR #17) — the composer
module BODY is filled and proven plan-clean (the environment runs as the pipeline
SA, one `roles/composer.worker` grant, the DAG-bucket upload of the committed
Phase 8b DAG), count-gated behind `enable_composer` (default false); the spanner
module is filled (Phase 10), count-gated behind `enable_spanner` (default false).
The plan-first apply gate (`SAFE_ACTIONS`), the cloud-env allowlist
(`CLOUD_ENV_ALLOW` / `in_cloud_namespace` / `REDIRECTION_NAMES`), the env
allowlist (`ENV_ALLOW`) all live in `infra/cli.py`. The GCP stack on
`ontime-rate-recovery` (confirmed at phase entry): the free-tier layer (two
datasets, bucket, SA + grants, budget) is UP; **nothing billable is up** —
`gcloud spanner instances list` → `Listed 0 items.`, `composer.googleapis.com`
is `SERVICE_DISABLED` (no environment can exist). Terraform runs on operator
ADC (`tukanbuild@gmail.com`), never the impersonated SA (§8). The
`ontime-pipeline` SA is live and in state (no undelete detour until the next
full `tf-destroy`).

**What already exists vs what Phase 12 does.**

- **Exists:** the composer module (plan-clean, nothing applied); the spanner
  module (count-gated); `make dbt-build TARGET=bigquery` (Phase 9b) and
  `make writeback TARGET=spanner` (Phase 10), both proven live; the local
  Docker Airflow DAG (`make test-int-airflow`, TARGET=duckdb); the send-schedule
  cross-store parity pin (`SEND_SCHEDULE_SHA256_TINY`, `SEND_SCHEDULE_ROWS_TINY`
  = 20). `docs/DEPLOYMENT.md` carries the Composer + Spanner apply/run/teardown
  runbooks with dated-line placeholders for the Phase 12 apply.
- **Does NOT exist / is broken for a live run:** the committed DAG cannot run on
  Composer as laid out — (1) it imports `from orchestration.tasks import TASKS`,
  a package path that will not resolve in a flat `dags/` bucket (row 47); and
  (2) more deeply, its tasks are `BashOperator`s shelling out to `make` with
  `cwd=REPO` (`Path(__file__).resolve().parents[2]`) — **Composer workers have no
  repo checkout, no `make`, no `uv`/dbt venv**, and `parents[2]` of the Composer
  DAG path is `/home/airflow`, which has no Makefile. The DAG also targets
  `duckdb`/local only (`orchestration/tasks.py::TARGET = "duckdb"`), so it cannot
  build on BigQuery or write to Spanner without a cloud target seam.

**Phase 12, therefore (Option A — the developer's call at phase entry):** make
the DAG *parse* on Composer (row 47 import fix), give it an env-driven cloud
target so a Docker-Airflow run can build on BigQuery and write to Spanner, then:
rehearse the local Docker-Airflow → real-BigQuery+Spanner path FIRST (the
zero-Composer green run — this is the send_schedule evidence), apply Composer
(+ Spanner) ask-first, trigger ONE live DAG run and confirm the DAG imports
without error in the Airflow UI (Composer's contribution is that the module
applies and the DAG parses live — its make-based tasks are not executed on the
worker, §8 gotcha), capture the green run log + `send_schedule` row count/hash
into `docs/RESULTS.md`, `terraform destroy`, and prove the Composer meter
stopped and total spend < $25. **Option B — bake the repo + toolchain into a
custom Composer image so the make-based DAG executes on the worker — is rejected:
it blows the ≤6-decision cap and the $25 budget's spirit and fights the managed-
Airflow model (Pinned decision 3, DECISIONS Phase 12).**

BACKLOG rows that name Phase 12 as a trigger, dispositioned (phase-entry review;
the developer decided each before this spec was written):

- **Row 47 — the flat Composer DAG upload does not satisfy `from
  orchestration.tasks import TASKS`.** DUE, **done here** — the prerequisite for
  any live DAG parse (Pinned decision 1).
- **Row 16 — Terraform state is a local, unversioned `infra/terraform.tfstate`.**
  Cost half: **not triggered** — every Phase 12 apply is torn down in the same
  session (Pinned decision 4), so no apply is left up. Confidentiality half:
  **RE-DEFERRED** — introducing a GCS remote backend (`init -migrate-state`)
  immediately before the highest-stakes live apply/destroy of the project adds a
  failure mode to the exact teardown that must be reliable, and the file holds
  metadata, not credentials (Phase 10 round 5 #12: no key/token/password
  resource exists). New trigger: *the repo goes public, the project is reused, or
  a stack is meant to persist beyond one session.*
- **Row 37 — the offline DAG structure test cannot pin DAG↔task attachment.**
  RE-DEFERRED — Phase 12 adds no Docker to CI; the container
  `test_int_airflow.py` IS the killer and the local rehearsal exercises it. New
  trigger unchanged: *CI gains Docker.* (Phase 12 does edit `tasks.py`; the new
  cloud-target tests run under the same offline stub, which still cannot model
  attachment — the residual is unchanged.)
- **Row 44 — the cloud-env redirection gate is not fully closed.** RE-DEFERRED —
  the residual (exotic proxy spellings on a local operator env; the gate REFUSES,
  never scrubs) is not on the demo path, and Phase 12 makes no `infra/cli.py`
  cloud-env / `ENV_ALLOW` edit. New trigger sharpened: *the next `infra/cli.py`
  cloud-env or `ENV_ALLOW` edit.*
- **Row 17 — the optional budget kill-switch (Pub/Sub → Function hard stop).**
  **NOT BUILT** — the hard stop guards a *long-lived* apply; Phase 12 is
  same-session apply-and-teardown with ask-first confirmation at every step, so
  the disciplined teardown + the `Listed 0 items.` exit check is the guardrail.
  New trigger: *a Composer/Spanner apply intended to persist beyond one session.*
- **Row 15 — Spanner never-leave-it-up.** KEPT OPEN — confirmed `Listed 0
  items.` at entry; re-confirmed at exit (the row's standing every-phase-exit
  check).
- **Row 30 — the CI WIF parity job** (its trigger names "Phase 12 demo-day
  prep"). RE-DEFERRED — orthogonal to the live run; the laptop `test-int-*` runs
  are the parity proof. Trigger unchanged.

## Why

Every prior phase kept the meter off: the cloud modules are written and
plan-clean but nothing paid has run end-to-end as a scheduled DAG. Phase 12 is
the one deliberate, supervised session that proves the whole path against real
GCP — BigQuery build, Spanner write-back, Composer-managed Airflow — and then
proves the teardown leaves nothing billable. It is a demo-day capability, not a
fix PR: it exercises the apply/run/teardown discipline the whole project was
built to make safe, and it surfaces the one thing plan-only Phase 11 could not —
whether the committed DAG actually parses and schedules on managed Airflow.

## The central constraint

**Nothing paid is left running, and the served `send_schedule` from the live run
is byte-identical to the frozen DuckDB truth.** Composer and Spanner are applied
and torn down in the same session (`Listed 0 items.` / empty environment list at
exit); the live run's `send_schedule` has `SEND_SCHEDULE_ROWS_TINY` (20) rows and
hashes to `SEND_SCHEDULE_SHA256_TINY` — the cross-store parity that makes "it ran
on the cloud" mean the same answer, not just a green light.

## DONE command

```
make test && make lint && make review-gate SPEC=specs/phase-12-live-run.md
```

- `make test` — the offline suite (578+), including the new DAG parse/render
  tests (Invariants 1–2) and the unchanged cross-store parity pins.
- `make lint` — ruff check + format, read-only.
- `make review-gate SPEC=…` — the offline gate + every Evidence test id / make
  target exists and every Record-updates file is in the diff.

The **live** half of Done-when is a manual, ask-first runbook (each apply/run/
teardown is authorized individually) and cannot be one command; its proof is the
Evidence table's captured output — the green DAG run log, the `send_schedule`
count/hash in `docs/RESULTS.md`, the DEPLOYMENT dated lines, and the
meter-stopped / `Listed 0 items.` exit checks.

## Done-when

1. **The DAG parses in both layouts.** `orchestration/dags/pipeline_dag.py`
   imports `TASKS` without error under the package layout (offline / Docker,
   `orchestration` on `sys.path`) AND under the flat `dags/` bucket layout
   (Composer). *Evidence: row 1.*
2. **The DAG can target the cloud without gaining logic.** With the cloud-target
   env unset the rendered task commands are byte-identical to the committed local
   list (TARGET=duckdb); with it set they are `make dbt-build TARGET=bigquery …
   PROJECT=<id> CONFIRM=yes` and `make writeback TARGET=spanner … PROJECT=<id>
   CONFIRM=yes` — `make` targets only, no non-`make` token. *Evidence: row 2.*
3. **The local Docker-Airflow → real-BigQuery+Spanner rehearsal is green, and its
   `send_schedule` matches the frozen truth.** One DAG run through Docker Airflow
   against the real project builds on BigQuery and writes the Spanner
   `send_schedule`; the served table has `SEND_SCHEDULE_ROWS_TINY` (20) rows and
   hashes to `SEND_SCHEDULE_SHA256_TINY`. *Evidence: row 3.*
4. **Composer applies, the DAG parses live, and one DAG run is triggered.**
   `make tf-apply … VARS='enable_composer=true[,enable_spanner=true]' CONFIRM=yes`
   creates exactly the module's resources; the `pipeline` DAG appears in the
   Airflow UI with no import error; one run is triggered. *Evidence: row 4.*
5. **Teardown leaves nothing billable.** After `make tf-apply …
   VARS='enable_composer=false[,enable_spanner=false]' CONFIRM=yes
   ALLOW_DESTROY=yes` (or a full `tf-destroy`), `gcloud spanner instances list` →
   `Listed 0 items.` and `gcloud composer environments list` → empty; total spend
   for the session < $25. *Evidence: row 5.*
6. **The run is captured; determinism carve-outs stay unasserted.** A Phase 12
   live-run block in `docs/RESULTS.md` records the green run and the
   `send_schedule` count/hash; DAG run ids, task timings, and BigQuery/Spanner
   job ids are non-deterministic by nature and nothing asserts them. *Evidence:
   row 6.*

(6 items. `docs/PHASES.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_dag_structure.py::test_dag_imports_in_flat_bucket_layout` (imports the DAG with only a flat `dags/`-style path on `sys.path`) + the existing package-path import in the same module |
| 2 | `tests/test_dag_structure.py::test_tasks_default_is_local_duckdb` (env unset → the committed list byte-for-byte) and `::test_tasks_render_cloud_target_from_env` (env set → the two cloud `make` commands, `make`-only) |
| 3 | The rehearsal run: Docker Airflow DAG green; the OK line `writeback OK: <id>.ontime → spanner, 20 users, …`; the Spanner read-back hashes to `SEND_SCHEDULE_SHA256_TINY` (the assertion `tests/integration/test_int_spanner.py` already pins). Captured in `docs/RESULTS.md` |
| 4 | `make tf-plan … VARS='enable_composer=true'` shows exactly the module's resources; the apply's `Apply complete! N added`; a screenshot/paste of the Airflow UI showing `pipeline` with no import error; the triggered run id (recorded, not asserted) |
| 5 | `gcloud spanner instances list` → `Listed 0 items.`; `gcloud composer environments list --locations=us-central1` → empty; `bq ls` = two datasets; the session's billing figure < $25 — all pasted into `docs/DEPLOYMENT.md` dated lines |
| 6 | The `<!-- phase-12-live-run -->` block in `docs/RESULTS.md` (run log + `send_schedule` count/hash); no test reads a run id / timing / job id |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all import contexts (package `orchestration` on `sys.path`; flat `dags/` bucket with only `dags/` on path), `pipeline_dag` imports `TASKS` without error. | `tests/test_dag_structure.py::test_dag_imports_in_flat_bucket_layout` — import the DAG with a `sys.path` that has only the flat `dags/` dir, not the repo root |
| For all cloud-target env settings, every rendered DAG task command is a `make` target and nothing else; with the env unset the rendered list equals the committed local list byte-for-byte. | `tests/test_dag_structure.py::test_tasks_default_is_local_duckdb`, `::test_tasks_render_cloud_target_from_env` — assert the unset default and the set-cloud rendering; a leaked non-`make` token or a changed default fails |
| For all Phase 12 cloud sessions, nothing paid remains after teardown. | Live/runbook invariant (not offline-testable): the exit checks `gcloud spanner instances list` → `Listed 0 items.` and `gcloud composer environments list` → empty, pasted into `docs/DEPLOYMENT.md`; BACKLOG row 15's standing check |
| For all live runs, the served `send_schedule` has `SEND_SCHEDULE_ROWS_TINY` rows and hashes to `SEND_SCHEDULE_SHA256_TINY` (cross-store parity — the write-back is unchanged from Phase 10). | `tests/integration/test_int_spanner.py` (already pins the hash on the idempotent second write); the live run re-uses that assertion and the RESULTS block records the observed count |

```mutations
orchestration/tasks.py::build_tasks        constant-return:[]
orchestration/tasks.py::build_tasks        invert-guard
```

(The two offline invariants above are upheld by the one new function
`orchestration/tasks.py::build_tasks`, which renders the ordered task list from
the cloud-target config; the mutation lines neuter it. Invariants 3–4 are live
and pinned by the runbook / the unchanged Phase 10 parity assertion, not by a new
Python mutation — no write path changes.)

## Pinned decisions (do not re-litigate)

- **The DAG import is dual-path.** `try: from orchestration.tasks import TASKS
  except ImportError: from tasks import TASKS` (`ModuleNotFoundError` ⊂
  `ImportError`; the flat-bucket test blocks the package via a `None` sys.modules
  entry, which raises a plain `ImportError`) — resolves under the
  package layout (offline / Docker, where `tests/test_dag_structure.py` imports
  it as `orchestration.dags.pipeline_dag`) and under the flat Composer `dags/`
  bucket (only `dags/` on path). Rejected: shipping an `orchestration/` package
  into the DAG bucket (Composer scans only `dags/` for DAGs; a nested package is
  more moving parts than a two-line import guard). Satisfies invariant 1. Closes
  BACKLOG row 47.
- **The DAG's cloud target is env-driven config, defaulting to the committed
  local behaviour.** `orchestration/tasks.py::build_tasks(target, project)` reads
  `OTR_DAG_TARGET` / `OTR_DAG_PROJECT` at parse time; unset → `TARGET=duckdb`,
  no PROJECT, rendering byte-identical to today's `TASKS` (so
  `test-int-airflow` and the offline structure test are unchanged); set →
  `dbt-build TARGET=bigquery … PROJECT=<id> CONFIRM=yes` and `writeback
  TARGET=spanner … PROJECT=<id> CONFIRM=yes`. This is config, not logic (the
  commands stay `make` targets — `orchestration/tasks.py`'s own comment already
  anticipated "Composer sets bigquery here and adds PROJECT"). Rejected:
  hard-coding a second cloud task list (duplicates the ordering; drifts).
  Satisfies invariant 2.
- **Option A: Composer proves the module applies and the DAG parses live; the
  green data run is the local Docker-Airflow → real-BigQuery+Spanner rehearsal.**
  The make-based DAG cannot execute on Composer workers (no repo / `make` / venv
  there — §8 gotcha to record), so Composer's Done-when contribution is the apply
  + a no-import-error DAG in the Airflow UI + one triggered run + the destroy; the
  `send_schedule` evidence comes from the rehearsal, which has the toolchain.
  Rejected: Option B (custom Composer image with the repo + toolchain baked in) —
  blows the decision cap and the $25 budget and fights managed Airflow. The
  rehearsal's mechanism is a committed cloud OVERRIDE compose file
  (`orchestration/docker-compose.cloud.yml`, a second `-f`), used only for the
  ask-first rehearsal: it sets `OTR_DAG_TARGET=bigquery`/`OTR_DAG_PROJECT` (so
  `build_tasks` renders the cloud commands, decision 2) and MOUNTS the
  impersonated-SA ADC read-only from the host's gcloud dir — never a keyfile,
  never baked (Credential standard). `make test-int-airflow` runs the BASE file
  alone and stays offline.
- **Same-session apply/teardown, ask-first every step, operator ADC for
  Terraform.** Every `tf-*` runs on operator ADC (`tukanbuild@gmail.com`), never
  the impersonated SA (§8, the ADC-picks-the-git-account trap — verify the login
  account); dbt-build and write-back run as the SA. EVERY applied toggle is
  carried in `VARS` (an omitted toggle plans the teardown, which `tf-apply`
  refuses without `ALLOW_DESTROY=yes` — Amendment F/N1); the scoped teardown is
  the toggle-flip with `ALLOW_DESTROY=yes`; every `CONFIRM` / `$(origin)` gate is
  unchanged. Satisfies invariant 3. (Row 16 cost-half not triggered; row 17
  kill-switch not built — same-session discipline is the guardrail.)
- **The pinned evidence is the `send_schedule` count + hash; determinism
  carve-outs stay unasserted.** `SEND_SCHEDULE_ROWS_TINY` (20) and
  `SEND_SCHEDULE_SHA256_TINY` are the live run's regression pin; DAG run ids, task
  timings, and BigQuery/Spanner job ids are non-deterministic by nature and
  nothing asserts them (Determinism policy carve-out). Satisfies invariant 4.
- **Total spend < $25; the Composer meter is proven stopped.** Composer is up for
  one run (~1 hr window, ~$0.42/h floor) and Spanner ~$0.09/h, both torn down the
  same session; the exit proof is `gcloud composer environments list` empty +
  `Listed 0 items.` + the session billing figure. No budget kill-switch is built
  (row 17).

## Scope (files)

- `orchestration/dags/pipeline_dag.py` — dual-path import (decision 1).
- `orchestration/tasks.py` — `build_tasks(target, project)` reading
  `OTR_DAG_TARGET` / `OTR_DAG_PROJECT`; unset default byte-identical to today
  (decision 2).
- `tests/test_dag_structure.py` — the flat-bucket import test + the two
  cloud-target render tests (invariants 1–2).
- `orchestration/docker-compose.cloud.yml` — the ask-first rehearsal override
  (cloud-target env + ADC read-only mount; base file untouched).
- `tests/test_orchestration_compose.py` — the override's offline shape pin (base
  stays cloud-free; override targets bigquery, requires a project, mounts ADC
  `:ro`, no keyfile).
- `docs/RESULTS.md` — the Phase 12 live-run block (run log + send_schedule
  count/hash).
- `docs/DEPLOYMENT.md` — the Composer dated apply/run/teardown lines filled; the
  Composer §8 gotcha (make-based DAG does not execute on the worker; the
  rehearsal is the green run).
- `docs/ARCHITECTURE.md` §8 — the same gotcha.
- `DECISIONS.md`, `docs/PHASES.md`, `CLAUDE.md`, `BACKLOG.md` — records.

No `infra/**.tf` change is expected (the modules are already written and frozen);
if the live plan forces a `.tf` edit that is a STOP + its own reconciliation, and
a re-freeze in the same commit.

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 12 entry (Option A over B; env-driven DAG target;
      row dispositions 16/17/37/44/47; the make-on-Composer gotcha)
- [ ] `docs/PHASES.md` — Phase 12 row: Done-when as landed; "Delivered" paragraph
- [ ] `CLAUDE.md` — Current status; the DAG cloud-target env knobs; BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §8 Gotchas (make-based DAG on Composer)
- [ ] `BACKLOG.md` — row 47 closed (DONE Phase 12); rows 16/17/37/44/30
      re-deferred with new triggers; row 15 re-confirmed
- [ ] Spec amendments — none (Phase 12 is the last build phase; Phase 13 is docs)
- [ ] `docs/RESULTS.md` — the Phase 12 live-run block
- [ ] `docs/DEPLOYMENT.md` — the Composer dated lines + the §8 gotcha
- [ ] docs/METRICS.md — none (no metric changes)
- [ ] README — none

## Threat model (REQUIRED)

No NEW Makefile target is added: `tf-apply` / `tf-plan` / `writeback` /
`dbt-build` / `test-int-*` already carry their `PROJECT` / `CONFIRM` / `VARS` /
`ALLOW_DESTROY` validation and `$(origin)` gating (Phases 9a–11, pinned in
`tests/test_infra.py` / `tests/test_makefile.py`). Phase 12 adds only two
**environment variables read by the DAG** at parse time — not Makefile
variables, and not on any destructive path:

| Input | empty / unset | `../x` | `"; ` | env-exported (the only source) | Pinned by |
|---|---|---|---|---|---|
| `OTR_DAG_TARGET` | unset → `duckdb` (the committed default) | not a path — used only to pick `duckdb`/`bigquery`; any other value renders no cloud command (the render is a closed `if target == "bigquery"`, else local) | passes through into a single-quoted `make TARGET=…` token; the value never reaches a shell unquoted, and `make` rejects an unknown TARGET | env is the intended source (Airflow/Composer config); it selects a target, it is never a path or a SQL predicate | `tests/test_dag_structure.py::test_tasks_render_cloud_target_from_env` |
| `OTR_DAG_PROJECT` | unset → no PROJECT (local build) | validated by the same GCP project-id shape check the `make` targets already apply (`pipeline/cli.py` / `infra/cli.py`) BEFORE any client — an invalid id is refused there, not in the DAG | single-quoted `PROJECT='…'`; refused by the project-id validator downstream | env is the intended source; it reaches `make` as a single token and is validated by the existing gate | same test + the existing `test_project_id_validation` |

The DAG never runs `make` on the Composer worker (Option A), so on Composer these
vars only affect what the DAG *renders*, never what executes there; on the Docker
rehearsal they render the cloud `make` commands, which carry their own validation.

The rehearsal override `orchestration/docker-compose.cloud.yml` is not a make
target and is never used by `make test-int-airflow` (base file only). It sets the
two env vars above and mounts the host's gcloud ADC dir READ-ONLY (`:ro`) at the
container's default ADC path — no keyfile, no `GOOGLE_APPLICATION_CREDENTIALS`, no
credential in the repo or the image (Credential standard); the ADC lives outside
the repo and is mounted, never committed. `OTR_DAG_PROJECT` uses compose's `:?`
required-variable guard, so a rehearsal with no project errors before anything
starts. Pinned by `tests/test_orchestration_compose.py` (base cloud-free; override
targets bigquery, requires a project, mounts `:ro`, no keyfile).

## Review & stack risk

- **code-reviewer** (triggered — `orchestration/`, `tests/` in Scope): the
  dual-path import, the env-driven `build_tasks` staying config-not-logic
  (`make` targets only), the unset default byte-identical to today, determinism
  carve-outs unasserted.
- **security-reviewer** (triggered — Scope touches `orchestration/` (a
  container/DAG surface, incl. the new compose override) and the live run drives
  cloud-cost / destructive `tf-apply`/`tf-destroy`): the two new env vars are
  validated / single-quoted and never reach a shell or a path unguarded; the
  rehearsal override mounts ADC read-only and bakes no keyfile (no credential in
  the repo, image, or a log); operator ADC vs SA discipline; ALLOW_DESTROY on
  teardown.
- **functionality-tester** (triggered): the DONE command; the flat-bucket import
  test; the cloud-render tests; the mutation block (`build_tasks` neutered → the
  structure tests go red).
- **coherence-auditor** at exit (MANDATORY — phase exit): the "make-based DAG runs
  on Composer" assumption is gone from every record; the RESULTS block and the
  DEPLOYMENT dated lines are filled; the Record-updates list matches the diff;
  `Listed 0 items.` / empty environment list confirmed.
- Stack risk: Composer env creation can fail or take 25–40+ min (rehearse the
  Docker fallback FIRST); the ADC-picks-the-git-account trap on every `tf-*`
  (verify the login account — §8, DEPLOYMENT step 5); whether the DAG truly
  parses on managed Airflow (the one thing plan-only Phase 11 could not prove) —
  a parse error in the UI is a STOP + a §8 finding, not a live workaround.

## Out of scope (deferred, recorded)

- The GCS remote state backend / confidentiality half of the tfstate migration —
  BACKLOG row 16, re-deferred (new trigger above).
- The offline DAG↔task attachment pin — BACKLOG row 37, re-deferred.
- The cloud-env redirection gate close — BACKLOG row 44, re-deferred.
- The budget kill-switch (Pub/Sub → Function) — BACKLOG row 17, not built.
- The CI WIF parity job — BACKLOG row 30, re-deferred.
- Making the make-based DAG execute on Composer (Option B) — rejected
  (DECISIONS Phase 12); a custom-image DAG runtime is a future phase if ever
  wanted.
