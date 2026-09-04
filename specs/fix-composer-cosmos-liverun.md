# fix/composer-cosmos-liverun — the cloud runtime, run live (PROPOSED)

Contract for the `fix/composer-cosmos-liverun` branch (**7b** of the split
ROADMAP item 7 — 7a the runtime built and plan-clean, 7b the supervised live
run; the Phase 11 → 12 precedent). Source: `docs/ROADMAP.md` item 7, the 7a spec
`specs/fix-composer-cosmos-runtime.md` § "Out of scope (deferred to 7b)", and
BACKLOG row **"The make-based DAG cannot run on Composer (Option A leaves the
scheduled cloud run unproven)"** — the row 7b CLOSES. Depends on **7a merged**
(PR #34, 2026-09-04): the Cosmos + KubernetesPodOperator DAG (`ontime_cloud`),
the `serving/`+`landing/` Artifact-Registry image, the freshness carve-out +
email callback, and the composer-module `.tf` all exist and are plan-clean.

**Status: PROPOSED — do not start until approved.** 7b applies real cloud
resources and runs one scheduled DAG on real BigQuery + Spanner (≈ $30,
ask-first at every apply / build / run step). It moves NO pin, fixture, golden,
model, or `.tf` SEMANTICS — it is the live proof plus the records that report
it. There is no code change by design; a structure tweak, if the live run
surfaces one, is a STOP-and-report (ARCHITECTURE §8), not a silent repair.

## Reconciliation against main (this branch's first commit)

Main as it actually is (`bf10b63`, PR #34): Phases 0–13 closed, ROADMAP items
1–6, 7a and 8 landed. The cloud runtime is the Cosmos + KPO DAG
`orchestration/dags/composer_dag.py` (`ontime_cloud`): the two landings
(`bq_load`, `spanner_load`) and the write-back run as `KubernetesPodOperator`
pods over ONE `serving/`+`landing/` image; Cosmos (`DbtTaskGroup`,
`ExecutionMode.VIRTUALENV`, `LoadMode.DBT_MANIFEST`) renders every dbt model as
its own task over the UNCHANGED project, with `dbt source freshness` at the head
(a determinism carve-out — ARCHITECTURE §4). The composer module
(`infra/modules/composer/`, `enable_composer=false` default) creates the
environment, the Artifact Registry repo (`ontime`) + a repo-scoped
`artifactregistry.reader` grant to the SA, `software_config.pypi_packages`
(`astronomer-cosmos` + `apache-airflow-providers-cncf-kubernetes`) and
`env_variables`, and uploads the Cosmos DAG + `composer_tasks.py` +
`failure_email.py` + the `dbt/` tree (`for_each` fileset) + the precompiled
`dbt/target/manifest.json`. Two `make` targets exist: `composer-dbt-manifest`
(offline `dbt parse`) and `build-serving-image PROJECT=<id> CONFIRM=yes` (the AR
push — the recipe landed in 7a; the push runs HERE). `make test-int-airflow` and
the make-based `pipeline_dag.py`/`tasks.py` are unchanged (the local Docker
proof; no longer uploaded — one pipeline-shaped DAG per bucket).

The GCP stack right now: the free-tier layer is UP and the `enable_ci_wif=true`
WIF layer PERSISTS (from `fix/ci-bigquery-parity`, 2026-09-04 — the first
stays-up apply, WIF is free); `operator_principal=user:<operator>` is applied
(the resource-scoped `serviceAccountTokenCreator` grant). **Nothing billable is
up** — no Spanner, no Composer (`gcloud spanner instances list` /
`gcloud composer environments list` → `Listed 0 items.` at entry). State is the
GCS remote backend (ROADMAP item 2). Terraform runs on **operator ADC, never the
SA** (§8, the git-account trap).

**What 7b does (the live run + the records):** apply
`enable_composer=true,enable_spanner=true` — carrying EVERY persisted toggle so
no persisted resource is re-proposed for destroy — build and push the image,
run ONE green scheduled `ontime_cloud` run that EXECUTES on the worker (Cosmos
one-task-per-model + the freshness gate + the three KPO pods, all success),
verify the served `send_schedule` is byte-parity with the frozen DuckDB truth,
tear it all down the same session, and fill the RESULTS live block + the
DEPLOYMENT dated lines + the closed BACKLOG rows.

BACKLOG rows this branch dispositions:

- **"The make-based DAG cannot run on Composer (Option A …)"** — **CLOSED here**:
  the `ontime_cloud` DAG executes on the Composer worker (Cosmos + KPO), the
  thing Option A could only parse. Struck with `DONE — fix/composer-cosmos-liverun`.
- **"The offline DAG structure test cannot pin DAG↔task attachment"** — the live
  run is the attachment + one-task-per-model proof (the Airflow UI shows every
  model as its own task under the `dbt` group, wired below the freshness gate and
  the landings, above the write-back). The OFFLINE stub residual stays (the
  offline test still cannot model attachment); the CI-Docker half is re-deferred
  with its trigger unchanged.
- **Spanner bills-from-creation + the budget kill-switch** — 7b's apply triggers
  both; the guardrail is the same-session toggle-flip teardown and the
  `Listed 0 items.` read-back (Done-when 5). Re-deferred, unchanged trigger.
- **"`operator_principal` lives in Terraform state, not tracked config"** — every
  7b apply carries `operator_principal` (and `enable_ci_wif`/`github_repository`)
  in `VARS`, so no persisted resource is re-proposed for destroy. Re-deferred
  (the tracked-resource fix is still its own future branch); the live evidence
  that omitting it re-proposes a destroy was already captured on the 7a `tf-plan`.

## Why

The repo lists exactly one thing as unfinished (README, INSIGHT, ROADMAP): a
scheduled cloud run that actually *executes*. Phase 12 proved the make-based DAG
only *parses* on Composer; 7a authored the Cosmos + KPO runtime and proved it
plans clean. 7b is the supervised session that removes the asterisk: one green
scheduled run whose served schedule is byte-identical to the frozen DuckDB
truth, then teardown. Splitting the ≈ $30 live surface out of 7a's build review
was the whole point of the 7a/7b cut (the Phase 11 → 12 discipline); 7b is that
run, not a rebuild.

## The central constraint

**The served `send_schedule` the Composer-executed DAG writes is byte-identical
to the frozen DuckDB truth, and nothing offline moves.** The `ontime_cloud` run
lands tiny on BigQuery, builds the UNCHANGED dbt project via Cosmos, and writes
the Spanner `send_schedule`; the read-back is `SEND_SCHEDULE_ROWS_TINY` (20) rows
hashing to `SEND_SCHEDULE_SHA256_TINY` — the SAME cross-store parity Phase 12's
Docker rehearsal proved, now produced by the runtime executing ON the worker. No
pin, fixture, golden, model file, macro, or `.tf` SEMANTIC changes; the offline
suite is byte-for-byte the standing one, so `make test` is green before, during,
and after the cloud session.

## DONE command

```
make test && make lint && make tf-validate && make review-gate SPEC=specs/fix-composer-cosmos-liverun.md
```

plus the ask-first **live runbook** (Done-when 1–5), each step authorized
individually — the phase's own live gate, exactly as Phase 12's supervised run
was (a manual runbook, not an offline command).

- `make test` — the standing offline suite, UNCHANGED (the composer-DAG stub
  tests, the callback test, the image-content test, the `uv.lock` guard, the
  three goldens 0-differ, the send-time/accuracy/holdout pins, the `.tf`
  `MANIFEST.sha256` match). Proves the central constraint: nothing offline moved.
- `make lint` — ruff check + format, read-only.
- `make tf-validate` — offline Terraform validate + fmt-check (no cloud).
- `make review-gate SPEC=…` — the offline gate + every Evidence test id / make
  target exists + every Record-updates file is in the diff.

The live runbook (the apply, the image push, the scheduled run, the parity
read-back, the teardown) is NOT part of the offline DONE command; its outputs are
pasted into the Evidence table (item 2 of "Before reporting DONE"), the RESULTS
live block, and the DEPLOYMENT dated lines.

## Done-when

1. **The apply carries every persisted toggle and plans clean.** `make tf-apply
   PROJECT=<project_id> CONFIRM=yes VARS='enable_composer=true,enable_spanner=true,
   enable_ci_wif=true,github_repository=<owner>/<repo>,operator_principal=<operator>'`
   (operator ADC), preceded by `make composer-dbt-manifest`, adds the composer
   module + the spanner module resources with **0 to change, 0 to destroy** on the
   persisted layer (WIF / operator / free-tier already up → 0 adds there); no
   persisted resource is re-proposed for destroy. *Evidence: row 1.*
2. **The image is built and pushed; the pods run it.** `make build-serving-image
   PROJECT=<project_id> CONFIRM=yes` pushes `<region>-docker.pkg.dev/<project_id>/
   ontime/serving:latest` to the AR repo the apply created, with NO credential
   baked; the three KPO pods in the live run pull and run it. *Evidence: rows 2, 3.*
3. **ONE green scheduled `ontime_cloud` run EXECUTES on the worker.** A real
   triggered/backfill run (not `dags test` — it must run on Composer workers)
   goes to `DagRun … state=success`; the Airflow UI shows **every dbt model as
   its own task** under the `dbt` group, the **`dbt source freshness` gate** at
   the group head (it PASSES — the ~240-day-old fixture is inside `error_after:
   3650 day`), and the **three KPO pods** (`bq_load`, `spanner_load`, `writeback`)
   all success. *Evidence: row 3.*
4. **The served schedule is byte-parity with the frozen truth.** The Spanner
   `send_schedule` read-back is `SEND_SCHEDULE_ROWS_TINY` (20) rows hashing to
   `SEND_SCHEDULE_SHA256_TINY`; the `writeback` pod logs `20 users, 20 written`
   and a re-run writes `0` (idempotent). *Evidence: row 4.*
5. **Same-session toggle-flip teardown leaves nothing billable.** `make tf-apply
   PROJECT=<project_id> CONFIRM=yes VARS='…enable_composer=false,enable_spanner=false,
   enable_ci_wif=true,github_repository=<owner>/<repo>,operator_principal=<operator>'
   ALLOW_DESTROY=yes` destroys exactly the composer + spanner resources; `gcloud
   spanner instances list` and `gcloud composer environments list` both →
   `Listed 0 items.`, `bq ls` → `raw`, `ontime` (free-tier intact). *Evidence:
   row 5.*
6. **The records report the run; nothing offline moved.** `docs/RESULTS.md` gains
   a `fix/composer-cosmos-liverun` live block (run log + `send_schedule`
   count/hash), `docs/DEPLOYMENT.md` gains the dated apply/run/teardown lines, the
   README "not yet on a scheduled cloud run" line is retired to a proven-run line,
   the make-based-DAG BACKLOG row is struck, and DECISIONS/ROADMAP/CLAUDE are
   updated; the offline diff moves no pin/fixture/golden/model/`.tf`-semantic.
   *Evidence: row 6.*

(6 items. `docs/ROADMAP.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `make composer-dbt-manifest` OK; the pasted `make tf-apply … VARS='enable_composer=true,enable_spanner=true,enable_ci_wif=true,github_repository=<owner>/<repo>,operator_principal=<operator>'` output "Apply complete! Resources: N added, 0 changed, 0 destroyed" (recorded in `docs/DEPLOYMENT.md`, ask-first, operator ADC) |
| 2 | the pasted `make build-serving-image PROJECT=<project_id> CONFIRM=yes` output pushing `<region>-docker.pkg.dev/<project_id>/ontime/serving:latest`; `tests/test_serving_image.py` (image content unchanged — no credential); `tests/test_makefile.py::test_build_serving_image_gates` |
| 3 | the pasted run log: `DagRun … state=success`; the Airflow UI / `dags list-runs` + `tasks states-for-dag-run` showing one task per model under `dbt`, the `dbt source freshness` gate success, the three KPO pods success (recorded in the `docs/RESULTS.md` live block) |
| 4 | the `writeback` pod log `writeback OK: <project_id>.ontime → spanner, 20 users, 20 written` (re-run `0 written`); the Spanner read-back `20 rows == SEND_SCHEDULE_ROWS_TINY`, hash `== SEND_SCHEDULE_SHA256_TINY` (recorded in the live block) |
| 5 | the pasted teardown `Apply complete! … destroyed` + `gcloud spanner instances list` → `Listed 0 items.` + `gcloud composer environments list` → `Listed 0 items.` + `bq ls` → `raw`, `ontime` (recorded in `docs/DEPLOYMENT.md`; the BACKLOG standing check) |
| 6 | `make test` green (nothing offline moved); `make check-docs`; coherence-auditor at exit (the RESULTS block, the DEPLOYMENT dated lines, the retired README line, the struck BACKLOG row, the records consistent); `make review-gate SPEC=…` (Record-updates in the diff) |

The same table, filled with the actual run's output, is item 2 of "Before
reporting DONE".

## Invariants (REQUIRED)

7b is a live-run + records branch — it adds NO new upholding Python (the Cosmos +
KPO runtime and its two pure helpers landed in 7a and do not change). So its
invariants split into two kinds, and there is **no `mutations` block**: the
offline invariants are the standing suite's (unchanged code → nothing new to
mutate; the Phase 12 live-run precedent), and the live invariants are proven by
the supervised runbook step named beside each, not by an offline mutation.

| Invariant ("for all …, … holds") | Falsified by (scenario test / live observation) |
|---|---|
| For all offline runs, no pin, fixture, golden, model file, macro, or `.tf` SEMANTIC moves — `make test` is green before, during and after the cloud session. | the standing suite: `make attribution-golden`/`report`/`scores-golden` 0-differ, the send-time/accuracy/holdout pins in `tests/pins.py`, `tests/test_composer_dag.py`, `tests/test_serving_image.py`, `tests/test_infra.py::test_tf_tree_matches_manifest`, `tests/test_deps.py` — any offline change reds one |
| For all scheduled `ontime_cloud` runs, the DAG EXECUTES on the Composer worker: every dbt model renders as its own task under the `dbt` group, the source-freshness gate heads it, the three KPO pods run the pushed image, and the run reaches `state=success`. | the live run (Done-when 3): a task that fails on the worker, a missing per-model task, freshness below a model, or a KPO pod that cannot pull/run the image, fails the run — captured in the RESULTS live block |
| For all served rows the run writes, the Spanner `send_schedule` is byte-parity with the frozen DuckDB truth — 20 rows hashing to `SEND_SCHEDULE_SHA256_TINY`, a re-run writing 0. | the live read-back (Done-when 4): a row count ≠ 20, a hash ≠ the pin, or a non-idempotent re-run, fails the parity check |
| For all apply/teardown steps, every persisted toggle is carried so no persisted resource is re-proposed for destroy, and the session ends with `Listed 0 items.` on both meters. | the pasted plan/apply/teardown (Done-when 1, 5): a plan that destroys a persisted resource (WIF/operator) without the toggle, or a meter not empty at exit, fails |

The offline invariance is guarded by the UNCHANGED standing suite; the three
live invariants are proven by the supervised run and its pasted output. No
`path.py::function` in this branch's diff is new, so `make mutate` has no target
(the DONE command omits it) — exactly a records + live-run branch.

## Pinned decisions (do not re-litigate)

- **1 — One supervised session: apply → image push → one scheduled run → parity
  read-back → toggle-flip teardown, all ask-first.** The Phase 12 / Spanner /
  Composer runbook shape (`docs/DEPLOYMENT.md`), now with the run GREEN on the
  worker (Cosmos + KPO) rather than Option A's parse-only. Satisfies the live
  invariants. Rejected: leaving the stack up between sessions (Composer ~$300+/mo,
  Spanner ~$65/mo — the bills-from-creation BACKLOG row); a persistent demo.
- **2 — Every apply carries `enable_ci_wif=true,github_repository=<owner>/<repo>,
  operator_principal=<operator>` beside the two live toggles.** WIF persists and
  `operator_principal` lives in state with no tracked default, so an apply that
  omits them re-proposes DESTROYING them (proven on the 7a `tf-plan`); the
  plan-first apply's `SAFE_ACTIONS` would REFUSE that destroy without
  `ALLOW_DESTROY=yes` (fail-safe, not silent), but the fix is to carry the
  toggles. Satisfies the apply invariant. Rejected: a bare apply (loses the
  operator grant / re-proposes the WIF teardown).
- **3 — A REAL scheduled/backfill run, not `dags test`.** `dags test` runs tasks
  in the scheduler process; the KPO pods must launch on the GKE workers and Cosmos
  must dispatch one task per model, which is the whole point of the live proof
  (the attachment + one-task-per-model + pods-run evidence the offline stub
  cannot give). Run it by unpausing `ontime_cloud` and triggering one run (or a
  one-interval backfill) whose landing THROUGH lands all of tiny. Satisfies the
  execution invariant. Rejected: `dags test` (no worker execution — the Phase 12
  limit restated); a full `@daily` catch-up (`catchup=False` by design).
- **4 — The run is self-contained; no manual pre-landing.** The image bundles
  `fixtures/tiny/raw/` + `dims/` + `MANIFEST.sha256` (never `truth/`), so the
  `bq_load` and `spanner_load` KPO pods land raw + the Spanner dims themselves at
  run; the `writeback` pod reads BigQuery `ontime` and writes Spanner. A manual
  `make spanner-load` is redundant (the DAG's KPO step lands the dims). Satisfies
  the parity invariant. Rejected: a manual landing before the run (the DAG's own
  steps are the proof that the pods work).
- **5 — Nothing offline changes; a live surprise is a STOP.** No pin, fixture,
  golden, model, macro, or `.tf` SEMANTIC moves (the central constraint). If the
  live run reveals a defect in the 7a runtime (a Cosmos/KPO field the pinned
  versions reject, a freshness render surprise), that is a STOP-and-report + an
  ARCHITECTURE §8 gotcha, then a scoped fix — never a silent repair mid-run.
  Satisfies the offline invariant. Rejected: patching the runtime inside 7b to
  make a run go green (that would be a design change owed a 7a-style amendment).

## Amendments (live-run findings, 2026-09-04)

The first supervised run surfaced two defects in the 7a runtime (decision 5's
STOP-and-report path). Each is recorded here and in ARCHITECTURE §8.

- **Amendment 1 (build recipe, finding 1).** `build-serving-image` ran a bare
  `docker build` with no `--platform`, so on an Apple-Silicon operator laptop it
  shipped an `arm64` image the Composer/GKE `amd64` nodes could not pull
  (`ImagePullBackOff`, the KPO pods wedged). Restores the invariant *the pushed
  image runs on the Composer nodes regardless of the build host*: pin
  `--platform linux/amd64` as a fixed declared target (Boundary contract),
  asserted on the pure `serving_build_command` helper. A build-recipe correctness
  fix (mechanism-level, not a data/write-path change), verified live (the fixed
  image ran the `bq_load`/`spanner_load` pods green).

- **Amendment 2 (execution model, finding 2 — amends 7a pinned decision 2).**
  Cosmos `ExecutionMode.VIRTUALENV` built a FRESH `dbt-bigquery[pandas]` venv
  per task (~10 min each, fragile under memory pressure), too slow/fragile to
  green ~13 dbt tasks on the SMALL environment (the venv-install process was
  killed mid-build; on retry it completed in ~9–12 min but no full-green run was
  reached). This restores the invariant *the scheduled run completes green on the
  target environment* by INSTALLING THE VENV ONCE PER WORKER AND REUSING IT: the
  builder passes `ExecutionConfig(virtualenv_dir=…)` (a persistent worker-local
  path, `composer_tasks.DBT_VENV_DIR`), so Cosmos builds the venv once and reuses
  it across every model/test/source task (~a handful of installs, not ~13). The
  execution MODE is unchanged (still VIRTUALENV — dbt-bigquery stays out of the
  Composer image / `uv.lock`); only the reuse is added. Rejected: bumping the
  environment size (masks the per-task waste, more spend); `ExecutionMode.
  KUBERNETES` over a baked dbt image (the robust fallback recorded below if the
  single per-worker install still proves too fragile on SMALL — a larger change,
  a new dbt image). The offline shape test pins `virtualenv_dir`
  (`test_cosmos_group_renders_the_unchanged_project`); its LIVE effect (one
  install per worker, a green run) is proven by the next supervised run.

  Fallback, if the single per-worker install is still killed on SMALL: either an
  env-size bump or `ExecutionMode.KUBERNETES` over a dbt image (reusing the AR
  machinery) — its own decision, recorded here, not built now.

## Scope (files)

New:
- `specs/fix-composer-cosmos-liverun.md` — this spec (committed alone first).

Changed — code (the two live-run fixes, Amendments 1–2 — no `.tf`/model/pin
semantics, no fixture):
- `pipeline/cli.py` + `tests/test_serving_image.py` — Amendment 1, the
  `--platform linux/amd64` pin on the serving-image build.
- `orchestration/dags/composer_dag.py` + `orchestration/composer_tasks.py` +
  `tests/test_composer_dag.py` — Amendment 2, the persistent-venv reuse
  (`virtualenv_dir`); the dbt project, macros, and `profiles.yml` are UNCHANGED
  (a runner tweak, not new logic).

Changed — records:
- `docs/RESULTS.md` — a `fix/composer-cosmos-liverun` live block (run log +
  `send_schedule` count/hash), beside the Phase 12 block.
- `docs/DEPLOYMENT.md` — the dated apply / run / teardown lines under the Composer
  and Spanner sections; the `ontime_cloud` runbook (the run is GREEN on the worker
  now, superseding Option A's parse-only note as the live result).
- `README.md` — the "not yet on a scheduled cloud run" line retired to a
  proven-run line (the one live claim edited only AFTER the run is green).
- `docs/ROADMAP.md` — item 7b marked landed.
- `DECISIONS.md` — the `fix/composer-cosmos-liverun` entry (the live run, the
  persisted-toggle apply, any §8 surprise).
- `CLAUDE.md` — Current status; Open BACKLOG rows count; the orchestration Repo
  map / status line updated (the cloud run is proven).
- `BACKLOG.md` — the make-based-DAG row struck (`DONE — fix/composer-cosmos-liverun`);
  the attachment / Spanner / kill-switch / operator_principal rows re-dispositioned
  with the live exit.
- `docs/ARCHITECTURE.md` §8 — the two live-run gotchas (the amd64 image build;
  the Cosmos VIRTUALENV per-task install cost + the venv-reuse fix).

Unchanged (verify): `dbt/**` models, macros, `profiles.yml`, `serving/`,
`landing/`, `infra/**/*.tf` SEMANTICS + `MANIFEST.sha256`, `tests/pins.py`,
`fixtures/`, `orchestration/failure_email.py`. No `make` target added or changed;
no golden, pin, model, or `.tf` SEMANTIC moved (the two fixes are a build-recipe
platform pin and a Cosmos runner tweak).

## Record updates (REQUIRED)

- [ ] `docs/RESULTS.md` — the live block (run log + count/hash)
- [ ] `docs/DEPLOYMENT.md` — the dated apply / run / teardown lines + the
      `ontime_cloud` runbook
- [ ] `README.md` — the scheduled-cloud-run line retired (post-run)
- [ ] `docs/ROADMAP.md` — item 7b landed
- [ ] `DECISIONS.md` — the `fix/composer-cosmos-liverun` entry
- [ ] `CLAUDE.md` — Current status; BACKLOG count; the orchestration status line
- [ ] `BACKLOG.md` — the make-based-DAG row struck; the attachment / Spanner /
      kill-switch / operator_principal rows re-dispositioned
- [ ] `docs/ARCHITECTURE.md` — §8 the two live-run gotchas (amd64 build; Cosmos
      venv per-task cost + reuse fix)
- [ ] docs/PHASES.md — none (post-13 fix branch; the log points to ROADMAP)
- [ ] docs/METRICS.md — none (no metric changes)
- [ ] Spec amendments — none (7b is the last ROADMAP item; nothing later to
      reconcile)

## Threat model (REQUIRED)

None — no new Makefile target takes a variable, deletes, touches cloud, or reads
input. 7b RUNS existing gated targets (`tf-apply`/`tf-destroy` — plan-first,
`$(origin CONFIRM)` + `ALLOW_DESTROY`, cloud-env allowlist, `ENV_ALLOW` child;
`build-serving-image` — `$(origin CONFIRM)`, PROJECT validated before any
docker/registry call, no credential baked; `composer-dbt-manifest` — offline, no
variable). Their threat models were pinned by their own specs
(`tests/test_makefile.py`, `tests/test_infra.py`) and are unchanged here. The
one operational hazard is the **git-account trap**: `tf-*` must run on the
OPERATOR ADC, never the impersonated SA — the runbook verifies the login account
before every `tf-*` (§8). The cloud-env allowlist (`infra.cli.CLOUD_ENV_ALLOW`)
is untouched (no `infra/cli.py` edit).

## Review & stack risk

- **code-reviewer** (triggered — the diff touches `docs/`, `README.md`,
  `DECISIONS.md`, `BACKLOG.md`, `CLAUDE.md`, `docs/ROADMAP.md`; NO `.py`/`.sql`/
  `.tf`): confirms the diff is records-only — no pin/fixture/golden/model/macro/
  `.tf`-semantic moved, no `make` target changed; the retired README line is a
  faithful proven-run statement.
- **security-reviewer** (MANDATORY — the branch touches cloud-cost/destructive
  operations via the runbook and records their outputs): no credential in the
  image / pod / a log / a record (refusals print names, never values); the SA is
  least-privilege (AR `reader` + `composer.worker` — unchanged); ADC/SA
  discipline (operator for `tf-*`, WI SA for pods); no live project id / account /
  slug in a record (the redaction contract — `<project_id>`/`<operator>`/
  `<owner>/<repo>` placeholders; `check-docs` check 5).
- **functionality-tester** (triggered): the offline DONE command green (nothing
  moved); the runbook's pasted evidence matches the Evidence table (the run
  reached success, the parity hash held, the meters emptied).
- **coherence-auditor** at exit (MANDATORY — a `fix/` exit): the make-based-DAG
  BACKLOG row is struck and the ROADMAP/CLAUDE/DECISIONS/RESULTS/DEPLOYMENT tell
  one consistent story; the README no longer says the cloud run is unfinished;
  `Listed 0 items.` on both meters at exit; the Record-updates list matches the
  diff; no live claim was written before the run proved it.
- Stack risk (verify live, STOP + §8 on any surprise): Composer 3 accepts the
  pinned `pypi_packages` (`astronomer-cosmos`, the k8s provider); Cosmos
  `DBT_MANIFEST` + `VIRTUALENV` actually renders one task per model and installs
  `dbt-bigquery==1.9.1` per-run; `SourceRenderingBehavior.ALL` renders the
  freshness gate upstream; the KPO pods authenticate by the WI SA with no baked
  credential; the Composer API bootstrap can fail transiently on the transitive
  `compute` enable (§8 — enable `compute.googleapis.com composer.googleapis.com`
  by hand FIRST); the environment create takes 25–40+ min.

## The live runbook (ask-first at every step)

Each step is authorized individually; STOP for approval before the first cloud
call. Operator ADC for every `tf-*` (verify `gcloud config list account` — the
operator, not the SA, not the git-only account).

0. **Entry meters + login.** `gcloud spanner instances list` /
   `gcloud composer environments list` → `Listed 0 items.` (confirm nothing up);
   confirm the operator ADC account.
1. **Bootstrap the Composer API deps by hand once** (§8): `gcloud services enable
   compute.googleapis.com composer.googleapis.com --project=<project_id>` (avoids
   the transient `Error code 13` on the first apply's transitive `compute` enable).
2. **Precompile the dbt manifest** (before the apply — Terraform's upload source
   must exist): `make composer-dbt-manifest`.
3. **Apply** (operator ADC, carry every persisted toggle): `make tf-apply
   PROJECT=<project_id> CONFIRM=yes VARS='enable_composer=true,enable_spanner=true,
   enable_ci_wif=true,github_repository=<owner>/<repo>,operator_principal=<operator>'`.
   Expect `0 to change, 0 to destroy`; the environment create takes 25–40+ min.
4. **Build + push the image** (the AR repo now exists): `make build-serving-image
   PROJECT=<project_id> CONFIRM=yes`.
5. **Unpause + trigger one run** (a real run on the workers, not `dags test`);
   pick a logical date / interval whose landing THROUGH lands all of tiny, e.g.
   via `gcloud composer environments run ontime --location <region> dags unpause -- ontime_cloud`
   then `… dags trigger -- ontime_cloud` (or a one-interval backfill). Watch the
   Airflow UI: the `dbt source freshness` gate, one task per model under `dbt`,
   the three KPO pods — all success; `DagRun state=success`.
6. **Verify parity.** The `writeback` pod log `20 users, 20 written` (a re-run
   `0`); the Spanner `send_schedule` read-back → 20 rows, hash
   `== SEND_SCHEDULE_SHA256_TINY` (the `make test-int-spanner` read helper or a
   direct Spanner query).
7. **Teardown, same session** (operator ADC, carry the persisted toggles, flip
   the two live ones off): `make tf-apply PROJECT=<project_id> CONFIRM=yes
   VARS='enable_composer=false,enable_spanner=false,enable_ci_wif=true,
   github_repository=<owner>/<repo>,operator_principal=<operator>' ALLOW_DESTROY=yes`.
   Then `gcloud spanner instances list` / `gcloud composer environments list` →
   `Listed 0 items.`, `bq ls` → `raw`, `ontime`.
8. **Record** the dated lines + the RESULTS block + the retired README line;
   strike the BACKLOG row; the exit coherence audit.

## Out of scope (deferred to a later branch or recorded)

- The CI-Docker half of the DAG↔task attachment pin — BACKLOG row, re-deferred
  (7b's live run proves attachment; the offline stub still cannot).
- Making `operator_principal` a tracked, non-toggle resource so a bare apply
  preserves it — its own future `infra/` branch (BACKLOG row).
- The budget kill-switch — not built (same-session teardown is the guardrail).
- Composer-on-a-schedule beyond one supervised run, finer-than-day partitioning,
  append-landing on Spanner dims — BACKLOG if a case appears.
- A `large`-profile incremental live re-run (the prune byte-reduction number) —
  its own ask-first branch (BACKLOG row).
