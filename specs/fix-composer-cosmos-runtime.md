# fix/composer-cosmos-runtime — the cloud runtime, built and plan-clean (PROPOSED)

Contract for the `fix/composer-cosmos-runtime` branch (**7a** of the split
ROADMAP item 7 — density: item 7 landed as two PRs, 7a the runtime built and
plan-clean, 7b the supervised live run, exactly the Phase 11 → Phase 12
precedent; recorded in DECISIONS and `docs/ROADMAP.md`). Source: `docs/ROADMAP.md`
item 7 and BACKLOG row **"The make-based DAG cannot run on Composer (Option A
leaves the scheduled cloud run unproven)"**. Depends on Phase 12 merged (the
make-based DAG parses on Composer but does not execute there) and
`fix/append-landing` (the BigQuery per-partition landing the image invokes).

**Status: PROPOSED — do not start until approved.** This is a design change (a
new DAG task model, a new container image, a new package, a new run path), so it
carries a SPEC, committed alone first; STOP for approval before implementing.

**7a builds and proves offline + plan-clean; 7b applies and runs live.** 7a
authors the Cosmos + KPO DAG, the `serving/`+`landing/` image, the source-
freshness carve-out + email callback, and the `infra/` changes (Artifact Registry
repo, the SA pull grant, `pypi_packages`, the new DAG-bucket uploads), and proves
them with the offline suite + `tf-validate` + an ask-first `tf-plan` that shows
exactly the new resources. **Nothing is applied in 7a** (no Composer, no Spanner,
no image push — the Phase 11 posture). 7b (`fix/composer-cosmos-liverun`, spec
finalized after this merges) does the ask-first apply / image push / one green
scheduled run / same-session teardown, fills the RESULTS + DEPLOYMENT dated
lines, and closes the make-based-DAG BACKLOG row.

**Dependency add (STOP-and-ask, CLAUDE.md Conventions / allowlist).** Two
packages enter the **Cloud Composer environment only**, via the Terraform
`software_config.pypi_packages` field on `google_composer_environment` — never
`uv.lock`, never the repo venv, never a pipeline import under `make test`:

- `astronomer-cosmos` — renders the dbt project as Airflow tasks (`DbtTaskGroup`).
- `apache-airflow-providers-cncf-kubernetes` — `KubernetesPodOperator`.

(`apache-airflow-providers-google` ships pre-installed on Composer; dbt-bigquery
is installed per-run by Cosmos in an isolated virtualenv, `operator_args.
py_requirements` — not in the Composer image, not in `uv.lock`.) The offline
suite imports NEITHER — the DAG module is loaded under stubs (the Phase 8b/12
`test_dag_structure.py` pattern), and a lock guard asserts both are absent from
`uv.lock`. **If the pinned `hashicorp/google ~> 6.0` provider, Composer 3's
managed Airflow, or Cosmos lacks a field this spec assumes (the `pypi_packages`
shape, `LoadMode.DBT_MANIFEST`, `ExecutionMode.VIRTUALENV`, the
`composer-user-workloads` KPO namespace, source-freshness rendering), that is a
STOP-and-report, not a workaround (ARCHITECTURE §8). Whether Cosmos actually
renders our project and whether the KPO pods run is 7b's live concern — 7a proves
the module PLANS and the shape loads, exactly as plan-only Phase 11 did.**

## Reconciliation against main (this branch's first commit)

Main as it actually is (`5664eef`): Phases 0–13 closed, ROADMAP items 1–6 and 8
landed. The orchestration layer is Phase 8b/12's **make-based** DAG
(`orchestration/dags/pipeline_dag.py`: two `BashOperator`s shelling `make
dbt-build`/`make writeback` with `cwd=REPO`, dual-path import, env-driven cloud
target in `orchestration/tasks.py`). Phase 12 proved it **parses** on managed
Airflow but its tasks **fail on a Composer worker** — `make: No rule to make
target 'dbt-build'` at `cwd=/home/airflow` (no repo / `make` / dbt venv;
ARCHITECTURE §8, DEPLOYMENT dated line 2026-09-01); the green DATA run was the
local Docker-Airflow → real-BigQuery+Spanner rehearsal (`docs/RESULTS.md § Phase
12`). The composer module (`infra/modules/composer/`) creates the environment (as
the pipeline SA, one `roles/composer.worker` grant) and uploads the make-based
DAG; count-gated behind `enable_composer` (default false). `make dbt-build
TARGET=bigquery` (Phase 9b), `make bq-load` / `make spanner-load`, and `make
writeback TARGET=spanner` (Phase 10) exist and are proven live. The generated
`dbt/models/staging/sources.yml` declares **"No freshness config (it reads the
clock)"** — a deliberate determinism choice (see The central constraint). The GCP
stack: free-tier layer UP, **nothing billable up**. Terraform runs on operator
ADC, never the SA (§8).

**What 7a does (supersedes Option A, plan-only).** Author the runtime that
actually executes on a worker, and prove it plans:

- **Cosmos** (`DbtTaskGroup`, `ExecutionMode.VIRTUALENV`, `LoadMode.
  DBT_MANIFEST`) renders **every dbt model as its own task**, running the
  UNCHANGED `dbt/` project (same macros, same `profiles.yml` bigquery target)
  from a precompiled manifest in the DAG bucket.
- The three **non-dbt** steps (BigQuery landing, Spanner dims landing, Spanner
  write-back) run as **`KubernetesPodOperator`** pods over ONE small
  `serving/`+`landing/` image in Artifact Registry, authenticating by the
  environment's Workload-Identity SA (no credential in the pod or image).
- **Source freshness** heads the graph (the one clock reader, a determinism
  carve-out — never a pipeline input, never pinned); an `on_failure_callback`
  **emails** on any task failure (ROADMAP item 7's two freshness clauses).

The committed make-based DAG (`pipeline_dag.py`/`tasks.py`) and the local
Docker-Airflow path (`make test-int-airflow`, TARGET=duckdb) are UNCHANGED — they
remain the offline/local orchestration proof; the new Cosmos DAG is the cloud
runtime. One DAG bucket cannot hold two `pipeline` DAGs, so the composer module
uploads the Cosmos DAG, not the make-based one (decision 3).

BACKLOG rows this branch dispositions:

- **"The make-based DAG cannot run on Composer (Option A …)"** — NOT yet closed
  (the live proof is 7b); 7a builds the runtime and proves the plan. Re-worded to
  name 7a/7b.
- **"The offline DAG structure test cannot pin DAG↔task attachment"** — trigger
  "a live Composer run or CI-Docker": untouched here (7a is offline/plan-only);
  the live attachment proof is 7b. Re-deferred.
- **Spanner never-leave-it-up + the budget kill-switch** — 7a applies nothing, so
  neither is triggered; confirmed `Listed 0 items.` at entry and exit (the
  standing check). Both re-deferred to 7b's live apply (same-session teardown).

## Why

The repo proves correctness at scale, a cost table, and a non-circular offline
evaluation, but the one thing it still lists as unfinished (README, INSIGHT,
ROADMAP) is a scheduled cloud run that actually *executes*. Phase 12's Option A
was honest about its limit: the make-based DAG parses on Composer but its tasks
cannot run on a toolchain-less worker. 7a removes that asterisk in code — the
batch path is authored as a real managed-Airflow DAG (Cosmos + KPO) and proven to
plan clean — so 7b's supervised session is a run, not a rebuild. Splitting keeps
each PR under the decision cap and keeps the ≈$30 live surface out of the build
review (the Phase 11 → 12 discipline).

## The central constraint

**The dbt work is byte-identical to the DuckDB build, `uv.lock` gains nothing,
and the plan is clean.** The `dbt/` project (models, macros, `profiles.yml`, the
generated `sources.yml` body) does not change how it COMPUTES — Cosmos is a new
*runner*, not new logic — so every golden (`attribution`, `ontime_rate_daily`,
`scores_send_time`) stays 0-differ and the send-time / accuracy / holdout pins
hold. `uv.lock` never gains `astronomer-cosmos` or the k8s provider (they are
Composer-only `pypi_packages`). `tf-validate` passes and `tf-plan
VARS='enable_composer=true'` adds exactly the module's new resources (`0 to
change, 0 to destroy` on the free-tier layer). **The one deliberate widening:**
source freshness reads the wall clock, so it is added as a determinism *carve-out*
(never an input to any model, never a pinned value — beside Airflow run ids and
job ids in ARCHITECTURE §4), and the generated `sources.yml`'s "No freshness
config (it reads the clock)" note is replaced by the carve-out statement, the
freshness config emitted by `gen_dbt_sources.py`. Thresholds are set so the
frozen 2026-01 fixture passes a freshness check (a synthetic-data accommodation,
documented — production tightens them); the email mechanism is proven by an
OFFLINE unit test of the callback (a green run legitimately emails nothing), and
its live firing-on-failure is a 7b observation, not a 7a red test.

## DONE command

```
make test && make lint && make tf-validate && make review-gate SPEC=specs/fix-composer-cosmos-runtime.md
```

- `make test` — the offline suite: the stub-loaded Cosmos-DAG shape tests
  (invariants 1–3, 5), the callback unit test (invariant 3), the `uv.lock` guard
  (invariant 5), the image-content test (invariant 1), the unchanged goldens and
  send-time/accuracy/holdout pins (the central constraint), the regenerated
  `sources.yml` `--check` (`test_dbt_sources.py`), and the `.tf` static checks +
  `MANIFEST.sha256` match (`test_infra.py`).
- `make lint` — ruff check + format, read-only.
- `make tf-validate` — offline Terraform validate + fmt-check (no cloud).
- `make review-gate SPEC=…` — the offline gate + every Evidence test id / make
  target exists + every Record-updates file is in the diff.

The ask-first `tf-plan VARS='enable_composer=true'` (reads GCP APIs via operator
ADC, creates nothing) is Evidence row 5, not part of the offline DONE command.

## Done-when

1. **The three non-dbt steps are pods over one committed image.** The BigQuery
   landing, the Spanner dims landing, and the Spanner write-back each render as a
   `KubernetesPodOperator` over the `serving/`+`landing/` Artifact-Registry image,
   with the fixed Composer-3 namespace + `config_file` and NO credential in the
   pod spec or image (no `GOOGLE_APPLICATION_CREDENTIALS`, no keyfile, no mounted
   secret). *Evidence: rows 1, 6.*
2. **Every dbt model is its own Cosmos task over the UNCHANGED project.** The
   builder passes the committed `dbt/` + `profiles.yml` bigquery target to Cosmos
   (`ExecutionMode.VIRTUALENV`, `LoadMode.DBT_MANIFEST`); the three goldens are
   0-differ and the send-time/accuracy/holdout pins hold (no model file changed).
   *Evidence: rows 2, 7.*
3. **Source freshness heads the graph as a carve-out; a failure emails.**
   Freshness renders upstream of every model, is the only wall-clock reader, its
   verdict is never a model input nor a pin (recorded in §4), the
   `on_failure_callback` sends exactly one email on a failure context, and the
   regenerated `sources.yml` carries the carve-out freshness config. *Evidence:
   rows 3, 8.*
4. **The offline suite and `uv.lock` stay clean.** The DAG's shape is asserted
   under stubbed cosmos/airflow/k8s (no live import), and `uv.lock` contains
   neither `astronomer-cosmos` nor `apache-airflow-providers-cncf-kubernetes`.
   *Evidence: rows 4, 9.*
5. **The infra change plans clean and is re-frozen.** `tf-validate OK`; `make
   tf-plan PROJECT=<id> VARS='enable_composer=true'` adds exactly the module's new
   resources (Artifact Registry repo, the SA `artifactregistry.reader` grant, the
   `pypi_packages` update, the new DAG-bucket uploads) with `0 to change, 0 to
   destroy` on the existing layer; `make tf-freeze` re-pins `MANIFEST.sha256` in
   the same commit as the `.tf` change. *Evidence: row 5.*
6. **The make-based DAG and the Docker path are unchanged; no fixture moves.**
   `orchestration/dags/pipeline_dag.py` / `tasks.py` and `make test-int-airflow`
   are byte-unchanged; `fixtures/` is untouched (no re-freeze). *Evidence: rows
   6, 7.*

(6 items. `docs/ROADMAP.md` / `docs/PHASES.md` carry the same clauses; the spec
and DECISIONS are authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_composer_dag.py::test_kpo_steps_run_the_serving_image` (image URI shape, `namespace="composer-user-workloads"`, `config_file`, no credential env / mounted secret) |
| 2 | `tests/test_composer_dag.py::test_cosmos_group_renders_the_unchanged_project` (ProjectConfig → `dbt/`, ProfileConfig → the committed `profiles.yml` bigquery target, `ExecutionMode.VIRTUALENV`, `LoadMode.DBT_MANIFEST`) + `make attribution-golden`/`report`/`scores-golden` 0-differ |
| 3 | `tests/test_composer_dag.py::test_freshness_is_upstream_of_models`, `::test_freshness_verdict_is_never_a_pin` + `tests/test_failure_email.py::test_callback_sends_one_email_on_failure` + `tests/test_dbt_sources.py` (regenerated `sources.yml` carries the carve-out freshness config) |
| 4 | `tests/test_composer_dag.py::test_dag_shape_loads_under_stubs` + `tests/test_deps.py::test_composer_only_packages_absent_from_lock` |
| 5 | `make tf-validate` → `tf-validate OK`; `make tf-plan PROJECT=<id> VARS='enable_composer=true'` output "Plan: N to add, 0 to change, 0 to destroy" listing exactly the new resources (pasted, ask-first); `tests/test_infra.py` static checks + `test_tf_tree_matches_manifest` (re-frozen) |
| 6 | `tests/test_serving_image.py::test_image_context_is_serving_and_landing_only` + `::test_image_ships_no_generation_logic_or_truth_writer` (the image COPYs only `serving/`+`landing/`+their imports and, from `generator/`, ONLY `__init__.py`+`manifest.py` — never the generation logic or the truth writer, never `truth/`); `git diff` shows `pipeline_dag.py`/`tasks.py`/`fixtures/` unchanged. (`test_truth_isolation.py` scans only `.py/.sql/.yml`, so it does NOT cover the suffix-less `Dockerfile` — `test_serving_image.py` is the image-content check.) |
| 7 | `tests/test_scores.py` + the send-time/holdout pins in `tests/pins.py` unchanged; the three goldens 0-differ |
| 8 | `tests/test_composer_dag.py::test_freshness_verdict_is_never_a_pin` + the carve-out sentence in `docs/ARCHITECTURE.md` §4 (coherence-auditor) |
| 9 | `tests/test_deps.py::test_composer_only_packages_absent_from_lock` (grep `uv.lock`) |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all non-dbt pipeline steps (BigQuery landing, Spanner dims landing, write-back), the step renders a `KubernetesPodOperator` over the committed `serving/`+`landing/` image with the fixed Composer-3 namespace + `config_file` and NO credential in the pod spec or image. | `tests/test_composer_dag.py::test_kpo_steps_run_the_serving_image`, `tests/test_serving_image.py::test_image_context_is_serving_and_landing_only` — a KPO carrying `GOOGLE_APPLICATION_CREDENTIALS` / a mounted keyfile, or an image context copying `truth/` or generation logic, fails |
| For all dbt models in `dbt/models/**`, the builder hands Cosmos the UNCHANGED project + `profiles.yml`, so every golden is byte-identical (the render into one-task-per-model is 7b's live proof). | `tests/test_composer_dag.py::test_cosmos_group_renders_the_unchanged_project` + `make attribution-golden`/`report`/`scores-golden` 0-differ; any model-SQL change in the diff fails a golden |
| For all runs, source freshness renders upstream of every model, is the only wall-clock reader, its verdict is never a model input nor a pinned value, and the callback sends exactly one email on a failure context. | `tests/test_composer_dag.py::test_freshness_is_upstream_of_models`, `::test_freshness_verdict_is_never_a_pin`, `tests/test_failure_email.py::test_callback_sends_one_email_on_failure` — freshness below a model, a pin reading a freshness result, or a callback sending ≠1 email, fails |
| For all import contexts, the offline suite asserts the DAG's shape with cosmos/airflow/k8s STUBBED (never installed), and `uv.lock` carries neither `astronomer-cosmos` nor the k8s provider. | `tests/test_composer_dag.py::test_dag_shape_loads_under_stubs`, `tests/test_deps.py::test_composer_only_packages_absent_from_lock` — a real cosmos/airflow import in the offline path, or either package in the lock, fails |
| For all plans with `enable_composer=true`, the module adds exactly the new resources with 0 to change and 0 to destroy on the existing layer, and the pinned `.tf` tree matches `MANIFEST.sha256`. | `tests/test_infra.py::test_tf_tree_matches_manifest` (re-frozen) + the ask-first `tf-plan` output (Evidence row 5) — an un-frozen `.tf` edit or a plan that changes/destroys an existing resource fails |

The live halves (Cosmos actually rendering one task per model on the worker; the
pods running; the teardown) are **7b's** — no live/runbook invariant belongs to
7a, which applies nothing. The offline invariants are upheld by the two new pure
helpers `orchestration/composer_tasks.py` (the KPO step manifest + the Cosmos
config the builder assembles) and `orchestration/failure_email.py` (the callback):

```mutations
orchestration/composer_tasks.py::build_kpo_command        constant-return:[]
orchestration/composer_tasks.py::build_kpo_command        invert-guard
orchestration/failure_email.py::pipeline_failure_email    constant-return:None
```

(`build_kpo_command` renders the pod argv from a step name — an ALLOWLIST like
`tasks.py::build_tasks`; neutering it makes a KPO step run nothing or a wrong
command → the shape test reds. `pipeline_failure_email` returning early (`None`)
before its `send()` call → the callback test reds (`sent` stays empty). The
callback is passed as a reference (`on_failure_callback`), never called at
statement level, so `delete-call` has no target — `constant-return:None` is the
neuter that skips the send. The Cosmos-render and plan invariants are
SQL/plan-pinned, not Python-mutated — no data path changes.)

## Pinned decisions (do not re-litigate)

- **1 — One `serving/`+`landing/` image in Artifact Registry, run by
  `KubernetesPodOperator` for the three non-dbt steps.** The toolchain the
  make-based DAG lacked on the worker (§8) ships as a pod: `bq_load`,
  `spanner_load` and `writeback` each invoke the committed module CLI
  (`python -m landing.cli bq-load …` / `spanner-load …`, `python -m serving.cli
  writeback TARGET=spanner …`) inside the image, authenticating by the
  environment's Workload-Identity SA (Composer-3 pods inherit it; no
  `GOOGLE_APPLICATION_CREDENTIALS`, no keyfile — Credential standard). The
  make-level `$(origin CONFIRM)` gate is a *shell*-origin guard with no analog
  inside a single-purpose pod whose argv is baked into the reviewed DAG, so the
  entrypoint is a fixed reviewed command, not user input; the CLIs' own PROJECT
  validation still runs. The Artifact Registry repo + the SA `artifactregistry.
  reader` grant are new `infra/` resources, gated behind `enable_composer` (a
  default apply makes zero). Satisfies invariant 1. Rejected: a `BashOperator`
  shelling `make` on the worker (Option A — no toolchain there); baking the repo
  into the Composer image (Phase 12 Option B — fights managed Airflow).
- **2 — dbt runs as Cosmos, `ExecutionMode.VIRTUALENV`, `LoadMode.
  DBT_MANIFEST`, over the UNCHANGED project.** `DbtTaskGroup` renders one task
  per model (ROADMAP "every model its own task"); VIRTUALENV is Google's
  documented Composer choice (dbt-bigquery installed per-run in an isolated venv,
  never in the Composer image / `uv.lock`); `DBT_MANIFEST` loads from a
  precompiled `manifest.json` in the DAG bucket so the SCHEDULER runs no dbt at
  parse (`DBT_LS` at parse — the Composer anti-pattern — is rejected).
  `ProjectConfig` → the uploaded `dbt/`; `ProfileConfig` reuses the committed
  `profiles.yml` bigquery target (`profiles_yml_filepath`) so macros / `location`
  / `OTR_GCP_PROJECT` are unchanged — a runner, not logic. Satisfies invariant 2.
  Rejected: `ExecutionMode.KUBERNETES` (a second dbt image); `LOCAL` (the
  Composer-2 dep-conflict class).
- **3 — The Cosmos DAG, the dbt project, and its precompiled manifest reach
  Composer through the Terraform-managed DAG bucket; cosmos + the k8s provider
  through `software_config.pypi_packages`.** The composer module uploads the new
  `orchestration/dags/composer_dag.py` (+ its stdlib-only `composer_tasks.py`),
  the `dbt/` tree (`google_storage_bucket_object` `for_each = fileset(...)`), and
  the precompiled `dags/dbt/target/manifest.json`; it stops uploading the
  make-based `pipeline_dag.py`/`tasks.py` (one `pipeline` DAG per bucket). The
  manifest is produced by a new offline `make composer-dbt-manifest` (`dbt parse`
  on the duckdb target — structure only, no cloud), gitignored (a build artifact
  embedding the dbt version; the runbook regenerates it before `tf-plan`/apply —
  Terraform's source path must exist at plan). `pypi_packages` carries
  `astronomer-cosmos` + `apache-airflow-providers-cncf-kubernetes`, pinned. All
  `.tf` changes are re-frozen (`make tf-freeze`) in the same commit. Satisfies
  invariants 2, 5. Rejected: `gsutil rsync` outside Terraform (drift from the
  reviewed tree); committing the manifest (version-brittle noise).
- **4 — Source freshness heads the graph as a determinism carve-out; an
  `on_failure_callback` emails (ROADMAP item 7's two freshness clauses).**
  Freshness renders upstream of every model (Cosmos source rendering) so a stale
  source blocks the models — "freshness first." It reads the wall clock, which the
  determinism policy bans ON THE DATA PATH; freshness is NOT the data path (its
  verdict is never a model input nor a pinned value), so it is an explicit
  carve-out beside Airflow run ids / job ids (ARCHITECTURE §4).
  `gen_dbt_sources.py` emits the freshness config (`loaded_at_field:
  server_upload_time`, thresholds loose enough that the frozen 2026-01 fixture
  passes — a synthetic-data accommodation, documented; production tightens them),
  replacing the "No freshness config" note; `sources.yml` is regenerated and
  `test_dbt_sources.py` re-checks it. The DAG-level `on_failure_callback` calls
  `airflow.utils.email.send_email` (the recipient a Composer Airflow Variable,
  never a hardcoded address — no PII; SMTP a Composer `airflow.cfg` override,
  documented, no secret in the tree). Proven OFFLINE by the callback unit test;
  its live firing is a 7b observation. Satisfies invariant 3. Rejected: a
  separate `BashOperator` freshness task (duplicates the Cosmos venv); tight
  thresholds against `now()` (the frozen fixture would hard-fail — no green run).
- **5 — The offline suite loads the DAG under stubs; `uv.lock` stays clean.**
  `tests/test_composer_dag.py` stubs `cosmos` (`DbtDag`/`DbtTaskGroup`,
  `ProjectConfig`, `ProfileConfig`, `ExecutionConfig`, `RenderConfig`,
  `ExecutionMode`, `LoadMode`), `airflow`, and the k8s provider (recording their
  kwargs — the Phase 8b/12 pattern) and asserts the DAG's wiring, the KPO
  images/args, the Cosmos config values, and freshness-upstream-of-models — the
  shape, not a live render. `orchestration/composer_tasks.py` is stdlib-only (a
  flat Composer bucket has no repo packages — the `tasks.py` lesson) and inlines
  `PROJECT_RE` (pinned equal to `infra.cli.PROJECT_RE`). `test_deps.py` greps
  `uv.lock` for the two package names (absent). Satisfies invariants 4, 5.
  Rejected: an `OTR_INT` test importing real cosmos (it would need cosmos in the
  venv — the thing invariant 5 forbids); no offline shape test (Phase 12's
  review-cap lesson: pin the shape offline).
- **6 — 7a applies nothing; the proof is offline + `tf-plan`-clean + `tf-freeze`.**
  Like plan-only Phase 11: `tf-validate OK`, an ask-first `tf-plan
  VARS='enable_composer=true'` showing exactly the new resources (`0 to change, 0
  to destroy`), and `tf-freeze` re-pinning `MANIFEST.sha256` in the `.tf` commit.
  No Composer/Spanner apply, no image push (those are 7b, cloud-cost, ask-first).
  `make build-serving-image` (the AR push) and `make composer-dbt-manifest` are
  ADDED here but the push is exercised in 7b. Satisfies invariant 5. Rejected:
  folding the live run into 7a (blows the decision cap and puts the ≈$30 surface
  in the build review — the split's whole point).

## Scope (files)

New:
- `orchestration/dags/composer_dag.py` — the Cosmos + KPO DAG (imports cosmos /
  airflow / the k8s provider; runs on Composer / Docker only, stub-loaded
  offline). Dual-path import like `pipeline_dag.py`.
- `orchestration/composer_tasks.py` — stdlib-only: the KPO step manifest
  (`build_kpo_command` allowlist over step name) + the Cosmos config the builder
  assembles; inlines `PROJECT_RE`.
- `orchestration/failure_email.py` — `pipeline_failure_email` (the callback).
- `orchestration/images/serving/Dockerfile` + `.dockerignore` — the
  `serving/`+`landing/` image (deps from `uv.lock` non-dev; no dbt, no `truth/`).
- `tests/test_composer_dag.py`, `tests/test_failure_email.py`,
  `tests/test_serving_image.py`, `tests/test_deps.py`.

Changed:
- `infra/modules/composer/main.tf` (+ `variables.tf`/`outputs.tf` if needed) —
  the Artifact Registry repo + `artifactregistry.reader` grant + `pypi_packages`
  + the dbt-tree / manifest / Cosmos-DAG uploads; stops uploading the make-based
  DAG. `infra/MANIFEST.sha256` re-frozen.
- `scripts/gen_dbt_sources.py` + `dbt/models/staging/sources.yml` (regenerated) —
  the freshness carve-out config (decision 4).
- `Makefile` + `pipeline/cli.py` (or `landing/cli.py`) — `make
  composer-dbt-manifest` (offline) and `make build-serving-image PROJECT=<id>
  CONFIRM=yes` (cloud-cost push to AR; the recipe exists in 7a, the push runs in
  7b).
- `.gitignore` — the generated `dbt/target/manifest.json`.
- `docs/ARCHITECTURE.md` — §4 the freshness carve-out; §6/§8 the Cosmos+KPO
  runtime; the runtime diagram.
- `DECISIONS.md`, `docs/ROADMAP.md`, `CLAUDE.md`, `BACKLOG.md` — records (the 7a/7b
  split; the two new targets; the carve-out; the allowlist note).

Unchanged (verify): `dbt/models/**` SQL, macros, `profiles.yml`;
`orchestration/dags/pipeline_dag.py` / `orchestration/tasks.py`; the send-time /
accuracy / holdout pins; `serving/`, `landing/` module logic; `fixtures/`.

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — the `fix/composer-cosmos-runtime` entry (the 7a/7b split;
      the 6 decisions; Cosmos+KPO supersedes Option A; the freshness carve-out;
      the Composer-only dependency add)
- [ ] `docs/ROADMAP.md` — item 7 re-cut as 7a (this) + 7b (live run), 7a marked
      built; the split recorded (the item-1 two-PR precedent)
- [ ] `CLAUDE.md` — Current status; the two new `make` targets; the orchestration
      Repo-map entry (the Cosmos DAG beside the make-based one); the allowlist
      note (cosmos/k8s Composer-only); the §4 determinism carve-out; Open BACKLOG
      rows count
- [ ] `docs/ARCHITECTURE.md` — §4 carve-out, §6 posture, §8 gotcha, the diagram
- [ ] `BACKLOG.md` — the make-based-DAG row re-worded to 7a/7b (closed in 7b);
      the attachment / Spanner / kill-switch rows re-deferred to 7b with triggers
- [ ] `dbt/models/staging/sources.yml` — regenerated (`make gen-sources`)
- [ ] `infra/MANIFEST.sha256` — re-frozen
- [ ] `Makefile` — the two new targets (`composer-dbt-manifest`,
      `build-serving-image`) + `.PHONY`
- [ ] Spec amendments — 7b's spec is authored after this merges (not here)
- [ ] docs/PHASES.md — none (post-13 fix branch; the log points to ROADMAP)
- [ ] docs/RESULTS.md, docs/DEPLOYMENT.md — none in 7a (7b fills the live block +
      dated lines)
- [ ] docs/METRICS.md, README — none (README's "unfinished" line retires in 7b,
      when the run is proven)

## Threat model (REQUIRED)

New Makefile targets and inputs:

| Target / input | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make build-serving-image PROJECT=<id> CONFIRM=yes` | `PROJECT=` → refuses (validated GCP-id shape before any client) | not a path — PROJECT validated by shape, never joined to a path | single-quoted `$(call _Q,…)` + shape-validated before it reaches `gcloud`/`docker`; a metachar refuses | env-exported `CONFIRM` has `$(origin)=file` → refused (only command line) | `CONFIRM=yes` accepted only from the command line (cloud-cost push) | `tests/test_makefile.py::test_build_serving_image_gates` |
| `make composer-dbt-manifest` | no var — offline `dbt parse` (duckdb target), no cloud, no delete | n/a | n/a | n/a | no CONFIRM (non-destructive, offline) | `tests/test_makefile.py::test_composer_manifest_is_offline` |
| KPO pod argv (`PROJECT` baked in the DAG render) | on the cloud branch, refuses (`composer_tasks.PROJECT_RE`) | validated at render before interpolation (the `tasks.py` lesson) | single-quoted + shape-validated at render, never a shell unguarded | env is the render source; validated at render AND by the module CLI | the pod runs a baked reviewed command, not user input | `tests/test_composer_dag.py::test_kpo_command_refuses_bad_project` |

`build-serving-image` is cloud-cost (push to AR): ask-first, `CONFIRM=yes`
command-line origin, operator/SA discipline; the recipe lands in 7a but the push
runs in 7b. The image bakes NO credential (Workload Identity at run); the pod
spec sets no `GOOGLE_APPLICATION_CREDENTIALS` and mounts no secret (invariant 1).
`pypi_packages` values are pinned literals, not user input. No `tf-apply` runs in
7a. The cloud-env allowlist (`infra.cli.CLOUD_ENV_ALLOW`) is unchanged (no
`infra/cli.py` cloud-env edit). Residual (STATED): `MAKEFLAGS` and a careless
operator env are mistakes, not a controlled-environment threat.

## Review & stack risk

- **code-reviewer** (triggered — `orchestration/`, the Dockerfile, `scripts/`,
  `tests/`, `infra/**.tf`, `Makefile`): Cosmos config is a runner not logic
  (models/macros/profile unchanged); `composer_tasks.py` stdlib-only + allowlist;
  the freshness carve-out never touches a pin; the stub-load pattern; the goldens
  unchanged.
- **security-reviewer** (MANDATORY — `infra/`, a new container image, a
  cloud-cost `build-serving-image` are touched): no credential in the image / pod
  / a log; Workload-Identity SA least privilege (the AR `reader` grant is the
  minimum; `composer.worker` unchanged); the image context excludes `truth/` and
  secrets (`.dockerignore`); ADC/SA discipline; `pypi_packages` pinned; no
  `tf-apply` in 7a.
- **functionality-tester** (triggered): the DONE command; the stub-load shape
  tests; the callback test; the image-content test; the `uv.lock` guard; the
  mutation block; the goldens 0-differ; the ask-first `tf-plan` clean.
- **coherence-auditor** at exit (MANDATORY — a `fix/` exit): the 7a/7b split is
  recorded consistently (ROADMAP, DECISIONS, BACKLOG, CLAUDE status); the
  Cosmos+KPO runtime is described where the make-based limit was; `sources.yml`'s
  "No freshness config" note is gone; the Record-updates list matches the diff;
  `Listed 0 items.` at exit; README/DEPLOYMENT/RESULTS live claims are NOT
  prematurely edited (those are 7b).
- Stack risk (verify in the first hour, STOP + §8 on any surprise): Composer 3
  `pypi_packages` accepts `astronomer-cosmos` + the k8s provider; Cosmos
  `DBT_MANIFEST` + `VIRTUALENV` config shape (`ProjectConfig`/`ProfileConfig`/
  `ExecutionConfig`/`RenderConfig`) on the pinned Cosmos line; source freshness
  renders upstream via `RenderConfig`; the `google_artifact_registry_repository`
  resource + `pypi_packages` on the v6 provider; `dbt parse` produces a manifest
  Cosmos reads. All render/parse questions are offline (a stub or a local `dbt
  parse`); whether they RUN on Composer is 7b.

## Out of scope (deferred to 7b or recorded)

- **7b (`fix/composer-cosmos-liverun`)** — the ask-first apply
  (`enable_composer=true[,enable_spanner=true]`), the image push, ONE green
  scheduled run on real BigQuery + Spanner (executes on the worker — the live
  attachment + task-count proof), the `send_schedule` parity
  (`SEND_SCHEDULE_SHA256_TINY`), the same-session toggle-flip teardown, the
  `docs/RESULTS.md` live block + `docs/DEPLOYMENT.md` dated lines, README's
  "unfinished" line retired, and closing the make-based-DAG BACKLOG row. Its spec
  is finalized AFTER 7a merges (the predecessor-merges rule).
- The CI-Docker half of the DAG↔task attachment pin — BACKLOG row, re-deferred.
- The budget kill-switch — not built (same-session discipline in 7b).
- `KubernetesPodOperator`/`gcp_cloud_run_job` execution for dbt (instead of
  VIRTUALENV) — a future branch only if VIRTUALENV cold-starts prove too slow.
- Making the make-based `pipeline_dag.py` execute on Composer (Option B) —
  permanently rejected; the Cosmos+KPO DAG is the cloud runtime.
