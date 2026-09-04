# CLAUDE.md — On-Time Rate Recovery Pipeline

## What this is

A deterministic batch data pipeline: a seeded generator emits daily-prompt app
events in the Amplitude raw-export shape (three clocks, `insert_id`), dbt on
DuckDB (local) / BigQuery (prod) stages them, attributes every late or missed
response to exactly one of `on_time | upload_fault | delivery_fault |
timing_gap | unattributed`, builds on-time marts, and produces a cohort-
constrained send-time recommendation **as a dbt model**; a Python eval scores
labels and the reachable-window recovery against generator truth and runs a
counterfactual simulation; an idempotent write-back lands the schedule in a
serving table (DuckDB stand-in locally, Spanner on GCP). Airflow orders the
steps; Terraform provisions GCP behind toggles that keep the meter off.

`docs/ARCHITECTURE.md` is the spec. `docs/PHASES.md` is the plan (history,
closed at Phase 13). `docs/ROADMAP.md` is what comes next, in order.
`PROJECT_BRIEF.md` is the origin and the architecture-review log. Read all
four before design decisions.

## Architecture

```
GENERATOR (seeded) ── truth side-file (never a source) ── dim_user seed (tz SCD2)
   │ raw events (Amplitude export shape)
   ▼
RAW  fixtures/<profile>/raw/*.jsonl → DuckDB | BigQuery
   ▼
dbt  staging → attribution → marts → features → scores      (dbt build = the gate)
   ▼
EVAL (reads truth)  label accuracy · reachable-center MAE · counterfactual simulation
   ▼
WRITE-BACK  idempotent upsert → send_schedule (DuckDB stand-in | Spanner)

AIRFLOW orders: dbt build (THROUGH) → write-back    TERRAFORM: BigQuery · GCS · Spanner · Composer · IAM · budgets
  (eval is a union-only gate in make pipeline / CI — reads truth, writes no table)
```

## Repo map

- `specs/` — one spec per phase, from `specs/TEMPLATE.md`. ONE DONE command
  each. `docs/PHASES.md` is the list; specs are the executable contracts. Since
  the Phase 13 close a `fix/` branch that re-freezes a fixture also carries one
  (Workflow rules, "Fix amendments") — listed in `docs/ROADMAP.md`, not PHASES.
- `generator/` — `models.py` (pydantic, schema source of truth), `profiles.py`
  + `profiles/*.json` (every knob a required field, incl. `shards` —
  fix/large-profile), `generate.py` (cause-first, `shards` derived
  `(seed, s·P_SHARD)` streams — one at `shards == 1`, emit order preserved
  within a shard, byte-identical to the old single `Random` at `shards == 1`;
  `SIM_START` fixed), `response.py` (the one response function,
  reused by Phase 6), `dims.py` (SCD2 seed, its own un-sharded stream),
  `writer.py` (canonical JSON/CSV — `JsonlAppender` the streaming form; raw
  events are gzipped hourly via `write_gzip_jsonl` / `GzipJsonlAppender`,
  `filename=""` + `mtime=0` + fixed level so the bytes are reproducible
  (fix/append-landing); refuses `fixtures/`), `manifest.py`, `truth.py` (the ONLY truth writer;
  `TruthStream` the streaming form), `cli.py` (`seed`, `freeze`;
  `write_output` in-memory at `shards == 1`, `write_output_streaming`
  per-shard for `shards > 1`). Truth goes to `<out>/truth/`, never read by the
  pipeline.
- `dbt/` — the dbt project: `models/staging` (Phase 2), `models/attribution`
  (Phase 3), `models/marts` (Phase 4: `ontime_rate_daily`,
  `ontime_retention`; every metric defined once in `docs/METRICS.md`; Phase 8a:
  `dim_user_current` — the open `dim_user` row per user, the write-back's tz),
  `models/features` (Phase 5: `features_user_hour` — organic `app_opened`
  local-hour histogram per user), `models/scores` (Phase 5:
  `scores_send_time` — the send-time model, cohort band + circular
  shrinkage; `docs/METRICS.md` § scores_send_time); `macros/` (the five dispatch
  macros, each with a DuckDB and a BigQuery body since Phase 9b, plus
  `generate_schema_name.sql` — a dbt hook override, not a dispatch macro),
  `tests/` (singular data tests), `profiles.yml` (`duckdb`, `bigquery` targets).
  `models/staging/sources.yml` is GENERATED (`make gen-sources`), never edited.
- `landing/` *(Phase 2; 9b; 10; renamed from `loader/` in `fix/landing-package`;
  append-only in `fix/append-landing`)*
  — raw landing: `load.py` (fixtures → DuckDB
  `raw` schema, types from the generated `ddl.sql`; append-only —
  `raw.events` persists, `partition_overwrite_events` overwrites one upload-date
  partition at a time from gzipped hourly files, `raw.dim_user` a full replace),
  `bq.py` (Phase 9b: the same files → GCS staging → BigQuery `raw`, schema from
  the generated `bq_schema.json`; append-only — a `WRITE_TRUNCATE` load per
  upload-date partition into the DAY-partitioned `raw.events$YYYYMMDD`; every
  cloud call through an injectable `Clients` factory — the offline suite injects
  fakes), `spanner.py`
  (Phase 10: the same dim seed → the Spanner `dim_user` table, contract
  types from `bq_schema.json`, idempotent batch upsert, injectable client),
  `cli.py` (`load`, `bq-load`, `spanner-load`, `drop-db`) and `land` — the
  TARGET landing dispatcher `pipeline/cli.py` calls.
  `require_confirm` is the ONE cloud gate (CONFIRM origin + the cloud-env
  allowlist, both imported from `infra.cli` — where `confirmed()`, the one
  origin predicate the integration fixtures' carried gate shares, and
  `CLOUD_ENV_ALLOW` live). Pipeline code — guarded by
  `test_truth_isolation.py`.
- `pipeline/` *(Phase 10; `fix/landing-package`)* — the pipeline plumbing that
  is not landing: `cli.py` (`dbt-build` — the TARGET-dispatched build that
  lands via `landing.cli.land` then runs `dbt build` — and the integration-test
  launchers `test-int-bigquery`, `test-int-spanner`; `full_refresh_args` /
  `dbt_vars_args` the two build-arg helpers). Imports the validators and the
  landing dispatcher from `landing.cli`; adds nothing to who-writes-what.
  Pipeline code — guarded by `test_truth_isolation.py`.
- `eval/` *(Phase 3+)* — the ONLY code that reads truth: `score.py` (label
  accuracy vs `truth/prompts.jsonl`; Phase 5: reachable-centre MAE and
  coverage vs `truth/users.jsonl`, off the model's own columns — never a
  centre Python derived), `golden.py` (a built table as canonical CSV +
  diff — one `Golden` spec per frozen file: attribution,
  `ontime_rate_daily`, `scores_send_time`), `report.py` (the overall rate
  off the mart), `cli.py` (`golden`, `score`, `report`, `scores-golden`;
  `truth_dir` = `fixtures/<p>/truth` when frozen, else
  `data/out/<p>/truth`, printed `(unfrozen)`; Phase 6: `simulate`,
  `power`; fix/holdout-eval: `holdout`), `simulate.py` (Phase 6: the
  counterfactual simulation — three
  arms under common random numbers through
  `generator.response.open_probability`, reading the SERVED pair and the
  band anchor, never `center_hour_local`), `holdout.py` (fix/holdout-eval,
  ARCHITECTURE §7 report (d): the temporal holdout — the ONE `eval/` module
  that RUNS `dbt build` (each in an isolated subprocess), and it reads NO truth.
  Builds two DuckDB warehouses (served ≤ an upload-date
  cut, full for the held-out opens), then scores the served schedule against
  the RAW organic `app_opened` opens uploaded after the cut — `in_window_share`
  + `mean_nearest_hours` per arm (`recommended` served hour, `cohort` anchor);
  no reachable-window or centre quantity, no clock — the non-circular
  counterpart to the simulation), `power.py` (the A/B power
  table, `math.erf` + bisection), `blocks.py` (the marker-confined writer
  of generated doc blocks), `readme.py` (Phase 13: `first_screen_rows` +
  `render_block` / `render_svg` — the README results block (headline + table +
  note, structural labels from the pins too) and the
  deterministic `docs/img/lift.svg`, from `tests/pins.py` (which the committed
  RESULTS blocks are pinned to); `cli.py readme`). Writes console, `data/out/<p>/expected/`,
  the marked blocks of `docs/RESULTS.md` / `docs/AB_DESIGN.md`, and (Phase 13)
  the `README.md` first-screen block + `docs/img/lift.svg` only — never a
  table, never `fixtures/`.
- `serving/` *(Phase 8a; Spanner target Phase 10)* — the idempotent write-back:
  `writeback.py` (`should_replace` replace-iff-greater on the row's own
  `(model_version, computed_as_of)` with `version_key` numeric version order —
  `v10 > v2`, non-`v<int>` refuses; `candidates_sql(scores, dims)` the ONE
  read with a Golden-style relation override; `winners_of` shared;
  `written_at = computed_as_of`; `COLUMNS` the ONE nine-column tuple every
  writer uses, `row_of` / `candidate_of` by field name on the write AND the
  read, the select list generated from the fields; the DuckDB run is one
  transaction on a single-writer file), `spanner.py` (Phase 10:
  `TARGET=spanner` — reads BigQuery `ontime`, then in ONE Spanner
  read-write transaction (`run_in_transaction`, retried on abort) reads the
  stored pairs and batch-`insert_or_update`s the winners; injectable
  `QueryClient`/`SpannerClient` factories, fakes offline on in-process
  DuckDB executing the same SQL; `disable_builtin_metrics=True`), `cli.py`
  (`writeback [TARGET]`, `pipeline`),
  `ddl.sql` (hand-written 9-column serving DDL, §2.9; the Spanner DDL lives
  in the terraform module, pinned to the same columns).
  Reads `scores_send_time` + `dim_user_current` (tz), writes `send_schedule`;
  never truth/raw. A pipeline dir — guarded by `test_truth_isolation.py`.
- `orchestration/` *(Phase 8b; 12)* — the Airflow DAG (Docker-local), no logic, only
  ordering: `dags/pipeline_dag.py` (BashOperators over `make` targets, `dbt_build
  >> writeback`, `max_active_runs=1`, `catchup=False`, `THROUGH` via `{{
  data_interval_end | ds }}`; Phase 12: a dual-path import — `from
  orchestration.tasks` offline/Docker, falling back to the flat `import tasks` in a
  Composer `dags/` bucket, `except ImportError`), `tasks.py` (the Airflow-free
  ordered-command manifest the DAG and the offline structure test share; Phase 12:
  `build_tasks(target, project)` reads `OTR_DAG_TARGET`/`OTR_DAG_PROJECT` at parse
  time — unset → the local DuckDB default byte-identical to 8b, set → the cloud
  `make` commands), `Dockerfile` + `docker-compose.yml` (lean
  `SequentialExecutor`/SQLite; `apache-airflow` never in `uv.lock`) +
  `docker-compose.cloud.yml` *(Phase 12)* — the ask-first rehearsal override
  (cloud target + ADC read-only mount; never used by `make test-int-airflow`,
  which is base-file-only). A pipeline dir — `test_truth_isolation.py` covers it.
  `eval` is NOT a DAG task (union-only gate — reads truth, asserts full-data pins).
  The make-based DAG parses on Composer but does NOT execute there (no
  repo/`make`/venv on a worker — §8; the Composer run proves parse+apply, the
  green data run is the local Docker → real-BigQuery+Spanner rehearsal).
- `infra/` *(Phase 9a; 10; 11)* — Terraform. `main.tf`/`variables.tf`/`outputs.tf` +
  `modules/{bigquery,gcs,iam,budget}` (unconditional, free/near-free) and
  `modules/{composer,spanner}` `count`-gated behind `enable_*` toggles that
  default false (so is the CI WIF layer inside `iam`: `enable_ci_wif`).
  Phase 11 filled the composer module (written, not applied): the
  `composer.googleapis.com` enablement (kept on at destroy), the smallest
  environment (`ENVIRONMENT_SIZE_SMALL`) running as the pipeline SA
  (`node_config.service_account = var.sa_email`, not a default Compute SA), one
  project-level `roles/composer.worker` grant to that SA (its documented
  minimum — `test_project_level_grant_is_only_bigquery_jobuser` admits it beside
  `bigquery.jobUser`), and the DAG-bucket upload of the committed Phase 8b DAG
  (`google_storage_bucket_object` sourcing `orchestration/dags/pipeline_dag.py`
  + `tasks.py`, never inline); the composer resource-type allowlist filled from
  `set()` to the exact four types (`tests/test_infra.py`); nothing applied
  (plan-only — a default `tf-plan` is Composer-free, `enable_composer=true`
  shows exactly the module's). Phase 10 filled the spanner module: instance
  (100 PU), database with the
  `dim_user` + `send_schedule` DDL inlined (PINNED by
  `tests/test_dbt_sources.py` against the contract renders — `gen-sources`
  never writes a `.tf`; a drift is a paste + `tf-freeze`), `EXTERNAL_QUERY`
  connection + `raw.dim_user_spanner` view (each column cast to the landing
  schema's type), the custom data-plane role `ontimeSpannerDataUser`
  (exact permission set pinned; no `updateDdl` — every predefined writing
  role has it), two database/connection-scoped grants, both to the SA (the
  federated read runs as the querying principal — §8; type + scope pinned;
  the gated modules have their own exact resource allowlists, no data
  sources; `region` validated in the root and every module);
  `deletion_protection = false` — the scoped teardown is the toggle flipped
  back (`tf-apply … VARS='enable_spanner=false' ALLOW_DESTROY=yes`), no
  `MODULE`, no `-target`; an apply that omits `enable_spanner=true` while
  Spanner is up is REFUSED by the plan-first apply (DEPLOYMENT).
  `cli.py` (validates `PROJECT`, gates `tf-apply`/`tf-destroy`/`tf-migrate-state`/`tf-freeze` on
  `CONFIRM=yes $(origin)`; toggles only as a command-line `VARS` → argv
  `-var`, refuses `TF_VAR_*`/`TF_CLI_ARGS*`, auto-loaded tfvars and
  any name in the cloud-env domain (O1/P1/Q: the `GOOGLE_`/`GCLOUD_`/`CLOUDSDK_`/`GCE_METADATA_`/`SPANNER_` prefixes, the `_EMULATOR_HOST` suffix, the prefix-less names the libraries read, and the transport-redirection class `REDIRECTION_NAMES` — an enumerated closed set pinned by a test, the vendor scan a coverage aid) outside `CLOUD_ENV_ALLOW` (the
  one allowlist every cloud command shares — Amendments N2/O1/Q; the plan-first
  apply's action allowlist `SAFE_ACTIONS` is N1/O2); runs terraform under an
  env allowlist (`ENV_ALLOW`, seven names — `fix/tf-vars-argv`)) drives
  `make tf-validate|tf-plan|tf-apply|tf-destroy|tf-migrate-state|tf-freeze`.
  State is a GCS remote backend (`fix/tf-remote-state`: the drafted `backend
  "gcs"` in `main.tf` uncommented as a PARTIAL config — `prefix` only; the
  `<project_id>-tfstate` bucket is bootstrapped by hand and supplied at init as
  `-backend-config=bucket=<project>-tfstate`, so no id in the `.tf`;
  `tf-migrate-state` runs `init -migrate-state` under the shared `tf-*` gates).
  `terraform.tfvars.example` only (never a `*.tfvars`); `.terraform.lock.hcl`
  is tracked (the provider pin); ADC/WIF, never a key. A pipeline dir — guarded
  by `test_truth_isolation.py`; the `.tf` tree is pinned byte-for-byte by
  `MANIFEST.sha256` (`make tf-freeze CONFIRM=yes` its only writer) plus the
  static property checks in `tests/test_infra.py`.
- `fixtures/tiny/` — golden `raw/events_<upload-date>_<HH>.jsonl.gz` + `dims/` +
  `truth/` + `expected/attribution.csv` (Phase 3) + `MANIFEST.sha256`. **READ-ONLY**: the
  review gate FAILs any change without a `Freeze:` line in the phase spec;
  `make freeze` is the only writer.
- `tests/` — pytest, no services, no network (DuckDB in-process counts as
  none). `tests/pins.py` holds every pinned number.
- `scripts/` — the offline guards, none a pytest file: `review_gate.py`
  (`make review-gate`), `mutate.py` (`make mutate`), `check_docs.py`
  (`make check-docs`), `round_tag.py` (review-round boundary tags;
  `make round-reset` clears them at phase start), `review_common.py` (shared
  SPEC validator / section parser / reduced env), `gen_dbt_sources.py`
  (`make gen-sources`: raw DDL + BigQuery load schema + `sources.yml` from
  `generator/models.py`).
- `docs/` — ARCHITECTURE.md (spec), PHASES.md (plan), METRICS.md (Phase 4:
  the single definition of every served metric — grain, numerator,
  denominator, null policy, pinning test), RESULTS.md (Phase 6: the
  counterfactual simulation — one generated block per profile, tiny and
  medium), AB_DESIGN.md (Phase 6: the production experiment; its power
  table is a generated block), DEPLOYMENT.md (Phase 9a: bootstrap, cost
  table, operator permissions, teardown, the optional kill-switch), INSIGHT.md
  (Phase 13: the one-page honest read — tiny's negative simulated lift, the
  simulation's circularity, the A/B as the real test; its hand-typed figures are pinned to
  `tests/pins.py` by `tests/test_insight.py`, `fix/process-doc`), PROCESS.md
  (`fix/process-doc`, ROADMAP item 1b: the one page on how it was built and
  what kept it honest — the gate, the sweep, the frozen fixture, the pins,
  the review loop, the assistant's part), img/lift.svg (Phase 13:
  the generated findings chart, `make readme`), ROADMAP.md (post-13: the ordered
  fix-branch list and the one-week cut, decided 2026-09-01 — a living doc, not
  a `check-docs` plan); all under `docs/`.
- `README.md` *(Phase 13; retold by `fix/front-door`)* — the front door, told
  as a story for a reader who has never seen the repo: the problem (three
  causes of a miss that look the same), what the pipeline does, what it found
  (the `make readme`-generated block — a headline sentence + the metrics table,
  never typed — and the chart, with the caveats in prose), why the numbers can
  be trusted, the cloud-free quickstart, the Mermaid diagram + stack-roles
  table, how it was built, the docs index. No phase language. A living doc —
  `scripts/check_docs.py` link-/target-checks it.
- `DECISIONS.md` — why-not-X log. One entry per non-obvious choice.
- `BACKLOG.md` — deferred findings with revisit triggers. Reviewed at every
  exit (Workflow rules, "Exit cadence"): do due items or re-defer with a new
  trigger, never drop.
- `data/` — gitignored working output (`data/out/<profile>/`, `data/truth/`,
  `*.duckdb`).

## Commands (macOS, uv)

- `make setup` — `uv sync`, `pre-commit install`
- `make test` — pytest, no services, no network; it passes
  `--ignore=tests/integration` (a bare `pytest`, e.g. the run-tests hook, instead
  relies on the `conftest.py` skip that collects `tests/integration/` only under
  `OTR_INT=1`, which only the `test-int-*` targets export)
- `make lint` — ruff via pre-commit (rewrites files; never run inside a gate)
- `make check-docs` — `scripts/check_docs.py`: every relative link/anchor in
  CLAUDE.md, README (tracked since Phase 13, so read), docs/,
  PROJECT_BRIEF, DECISIONS, BACKLOG resolves; every
  `make <target>` the LIVING docs name exists in the Makefile (ARCHITECTURE,
  PHASES and PROJECT_BRIEF are plans — link-checked only; a living doc may name
  a not-yet-built target only as a (doc, target) pair in the exact
  `FUTURE_TARGETS` set, red once the target is built or the doc stops citing it); every trace token in `TRACES` exists in source as an exact token;
  this file's "Open BACKLOG rows: **N**" equals BACKLOG.md's un-struck rows;
  in every tracked record (`RECORD_GLOBS` — markdown, Makefile, CI, compose, the
  dbt profile, the tfvars example — via `git ls-files`) every VALUE POSITION a
  project, repository or account identifier can occupy (`VALUE_POSITIONS`:
  `NAME=value`, `--flag value`, `gs://` buckets, `<x>.ontime` qualifiers,
  addresses) holds a placeholder SHAPE (check 5, `fix/public-release`; every set
  pinned exactly; the file:line and position are printed, never the value; a
  bare id in prose is the security-reviewer's check)
- `make review-gate [SPEC=specs/<f>.md] [BASE=main] [DELETED=a,b]` — the
  offline review gate: `make test` + `ruff check` + `ruff format --check`
  (read-only) + `make check-docs`; with SPEC, every Evidence test id / make
  target exists and every Record-updates file is in `git diff BASE...HEAD`;
  DELETED greps the tracked tree for each removed symbol; BASE must be a plain
  git rev. One line per check, exit 1 on any FAIL, 2 on a refused SPEC/BASE. `/review-round N` runs it first
- `make mutate SPEC=specs/<f>.md` — the mutation sweep: the unmutated suite
  runs first in its own worktree and must be green (a red HEAD is a refusal),
  then each line of the spec's Invariants ```mutations block (`path.py::func op`; ops exactly
  `delete-call`, `constant-return:<v>`, `invert-guard`, `swap-sort-key`; or,
  over one `case … end as <alias>` in a `dbt/models/**.sql` file,
  `path.sql::<alias> drop-arm:<n> | swap-arms:<i>,<j>` — Phase 3) is
  applied to HEAD in a throwaway `git worktree`, the offline suite runs there
  under a reduced env (its in-process `dbt build` is what kills a SQL line),
  verdict `KILLED | SURVIVED | ERROR` per line; exit 1 on any survivor
- `make round-reset` — deletes this checkout's local `review-round-*` tags
  (`scripts/round_tag.py reset`). Run at phase start: round tags are local,
  never pushed, and phase-agnostic, so a new phase's rounds would otherwise
  collide with the prior phase's leftovers. Leaves non-round tags alone
- `make seed PROFILE=<p>` — the generator: validates `[a-z0-9_]+`, writes
  `data/out/<p>/` only, hashes its output and compares to
  `fixtures/<p>/MANIFEST.sha256` when one exists (`seed OK … manifest match`;
  exit 1 on drift) — over the generator's keys only (`raw/`, `dims/`,
  `truth/`; `expected/` is the golden's). Never writes under `fixtures/`
- `make freeze PROFILE=<p> CONFIRM=yes` — the ONLY writer of `fixtures/<p>/`:
  copies `data/out/<p>/` over it and writes the manifest; refuses when
  `data/out/<p>/` lacks a file the current manifest lists (a bare `seed`
  cannot drop `expected/`). `CONFIRM=yes` must have command-line origin
  (`$(origin CONFIRM)`); a re-freeze also needs a DECISIONS entry and a
  `Freeze: fixtures/<p>/MANIFEST.sha256` line in the spec
- `make load PROFILE=<p> [THROUGH=<upload-date>]` — validates `[a-z0-9_]+`, loads
  `fixtures/<p>/{raw/events_*.jsonl.gz,dims/dim_user.csv}` into `data/<p>.duckdb`
  schema `raw`; prints `load: source=…` (falls back to `data/out/<p>/`,
  marked `(unfrozen)`), verifies `MANIFEST.sha256` first when one exists
  (`load DRIFT`, exit 1); types come from the generated `landing/ddl.sql`, never
  inferred. Append-only (fix/append-landing): `raw.events` persists across loads
  and each load overwrites the selected upload-date partitions (delete-then-insert
  per `cast(server_upload_time as date)`); re-landing a date adds 0 net rows,
  `raw.dim_user` is a full replace, and a payload conflict rolls the load back.
  `THROUGH` (an upload date `YYYY-MM-DD`, validated, never a path) lands only the
  files uploaded on or before it — a landing is the raw-table state (Phase 7);
  empty loads them all. THROUGH accumulates FORWARD within a warehouse (a
  `make drop-db` resets); a smaller THROUGH after a larger one does not trim
- `make dbt-build PROFILE=<p> [TARGET=duckdb] [CONFIRM=yes] [FULL=yes] [THROUGH=<upload-date>]` — `load`,
  then `dbt build` (source tests → `stg_events`, `stg_prompts`, `attribution` →
  data, unit and singular tests) against `data/<p>.duckdb`; prints `dbt-build OK:
  <p>/<target>`, exit 1 on any failure. The three event-level models are
  incremental (Phase 7): a first run is a full build, a later run reprocesses
  only partitions inside the `lookback_days` window of the data-derived horizon
  (`max(server_upload_time)`) via the `partition_overwrite` strategy.
  `FULL=yes` (command-line origin, `$(origin)`-gated) passes `--full-refresh`
  (rebuild from scratch). `THROUGH=<upload-date>` (Phase 8b) threads into the
  internal `load` so the build lands only files uploaded on or before it — a
  per-interval build the DAG runs; unset loads all (the default build is
  unchanged). The landing is the TARGET's own (Phase 9b): `duckdb` → `load`,
  `bigquery` → `bq-load` (GCS → BigQuery `raw`), never the other. Any `TARGET`
  other than `duckdb` is a cloud-cost command: refused unless `CONFIRM=yes` has
  command-line origin, and `TARGET=bigquery` also needs `PROJECT=<id>` (validated
  as a GCP project-id, exported to dbt as `OTR_GCP_PROJECT` from inside the
  process — the `bigquery` output has no default; `location: us-central1`);
  every model lands in the `ontime` dataset (`generate_schema_name` collapses on
  `target.type == 'bigquery'` only — DuckDB keeps `main_<folder>`). Run it as
  the SA (`docs/DEPLOYMENT.md`), ask first. dbt telemetry is off
  (`flags.send_anonymous_usage_stats`, `DO_NOT_TRACK`)
- `make bq-load PROFILE=<p> PROJECT=<id> CONFIRM=yes [THROUGH=<upload-date>]`
  *(Phase 9b)* — the BigQuery landing alone (`landing/cli.py bq-load`): the same
  files `load` selects → `gs://<id>-ontime/landing/<p>/{raw,dims}/` → `raw.events`
  / `raw.dim_user` with the schema GENERATED from `generator/models.py`
  (`landing/bq_schema.json`); append-only (fix/append-landing): a `WRITE_TRUNCATE`
  load per upload-date partition into the DAY-partitioned
  `raw.events$YYYYMMDD` (re-landing a date replaces just that partition, 0 net
  rows) plus one for the `raw.dim_user` seed — load jobs only; an empty events
  selection lands a zero-byte `_empty.jsonl` into the base table through it
  (BigQuery rejects a job over zero URIs; §8) — idempotent; prints `bq-load OK:
  <p> — N files[, landing ≤ <THROUGH>], E event rows, D dim rows`. Cloud-cost (cents):
  `CONFIRM=yes` command-line origin; `PROJECT` validated before any client;
  ADC (impersonated SA), never a key. Verifies `MANIFEST.sha256` like `load`
- `make drop-db PROFILE=<p> CONFIRM=yes` — deletes `data/<p>.duckdb` and its
  `.wal` (nothing else); `CONFIRM=yes` must have command-line origin
- `make gen-sources` — re-renders `landing/ddl.sql`, `landing/bq_schema.json`
  (Phase 9b) and `dbt/models/staging/sources.yml` from `generator/models.py`;
  `tests/test_dbt_sources.py` fails on a hand edit
- `make attribution-golden PROFILE=<p> [WRITE=yes]` — the built `attribution`
  table (`prompt_id,user_id,cohort_id,label`, sorted by `(prompt_id, user_id)`) vs
  `fixtures/<p>/expected/attribution.csv`; prints `attribution-golden OK:
  <p>, N rows, 0 differ`, exit 1 on any differing row. `WRITE=yes` (the
  literal only) writes `data/out/<p>/expected/attribution.csv` instead —
  `make freeze` is the only way it reaches `fixtures/`. Needs `dbt-build` first
- `make eval PROFILE=<p>` — label accuracy vs `<p>/truth/prompts.jsonl` and
  (Phase 5) reachable-centre MAE + coverage vs `<p>/truth/users.jsonl`
  (`eval/cli.py score`, the ONLY truth reader; `truth/` is
  `fixtures/<p>/` when frozen, else `data/out/<p>/`, printed `(unfrozen)`
  — `medium` is seeded, never frozen); prints `eval OK: <p>, accuracy 1.000
  (pin 1.000), N prompts` and `eval OK: <p>, mae 0.816201 h (pin 0.816201),
  coverage 0.600000 (pin 0.600000), N users`, exit 1 below
  `tests/pins.py::LABEL_ACCURACY` or off `SEND_TIME_PINS[<p>]` (tiny and
  medium; another profile has no pin and fails)
- `make scores-golden PROFILE=<p> [WRITE=yes]` — the built `scores_send_time`
  table (nine columns, sorted by `(user_id, cohort_id)`) vs
  `fixtures/<p>/expected/scores_send_time.csv`; prints `scores-golden OK:
  <p>, N rows, 0 differ`, exit 1 on any differing row. `WRITE=yes` (the
  literal only) writes `data/out/<p>/expected/scores_send_time.csv` instead
  — `make freeze` is the only way it reaches `fixtures/`. Needs `dbt-build`
  first
- `make report PROFILE=<p> [WRITE=yes]` — the built `ontime_rate_daily` mart
  (ten columns, sorted by `(cohort_id, prompt_date)`) vs
  `fixtures/<p>/expected/ontime_rate_daily.csv` plus the overall rate
  `sum(on_time) / sum(prompts_delivered)` vs `tests/pins.py::ONTIME_RATE`;
  prints `report OK: <p>, N cohort-days, 0 differ, ontime_rate 0.609756 (pin
  0.609756)`, exit 1 on a differing row or a rate off the pin; console only
  (`docs/RESULTS.md` is Phase 6's). `WRITE=yes` (the literal only) writes
  `data/out/<p>/expected/ontime_rate_daily.csv` instead — `make freeze` is the
  only way it reaches `fixtures/`. Needs `dbt-build` first
- `make simulate PROFILE=<p> [WRITE=yes]` — the counterfactual simulation
  (`eval/cli.py simulate`): every prompt re-drawn under `baseline` (its own
  send hour), `cohort` (the band anchor) and `recommended` (the served
  pair) with four common uniforms per prompt from `tests/pins.py::
  SIMULATE_SEED`, beside the `data` row (built `attribution` counts);
  rendered as the `<!-- simulate:begin <p> -->` block of `docs/RESULTS.md`.
  Check mode diffs the committed block byte-for-byte, prints `simulate OK:
  <p>, N prompts, 3 arms, block matches`, exit 1 on drift; `WRITE=yes` (the
  literal only) replaces the bytes between the profile's markers and
  nothing else (a missing pair is a refusal — the writer never creates or
  appends). `truth/` resolves as `eval` does (`(unfrozen)` for medium).
  Needs `dbt-build` first
- `make holdout PROFILE=<p> [WRITE=yes]` *(fix/holdout-eval)* — the temporal
  holdout (`eval/cli.py holdout`, ARCHITECTURE §7 report (d)): serve on data
  landed ≤ the profile's cut (`tests/pins.py::HOLDOUT_CUTS`), then score the
  served schedule against the RAW organic `app_opened` opens uploaded AFTER the
  cut — the non-circular counterpart to the simulation (raw only, never `truth/`,
  never a reachable-window or centre quantity, no clock). Two arms (`recommended`
  the served per-user hour, `cohort` the band anchor), two measures:
  `in_window_share` (opens within ±`HOLDOUT_WINDOW_HOURS` of the served hour) and
  `mean_nearest_hours` (mean circular distance to a user's nearest held-out open);
  rendered as the `<!-- holdout:begin <p> -->` block of `docs/RESULTS.md`, beside
  the simulation. Self-contained: builds two throwaway DuckDB warehouses in a temp
  dir (served ≤ cut, full for the held-out opens) — NO `dbt-build` first (`medium`
  is unfrozen, so `make seed PROFILE=medium` first). Check mode diffs the block
  byte-for-byte, prints `holdout OK: <p>, N users, M held-out opens, block
  matches`, exit 1 on drift; `WRITE=yes` (the literal only) replaces the marked
  bytes and nothing else (a missing pair refuses)
- `make power [WRITE=yes]` — the A/B power table (`eval/cli.py power`):
  users per arm and days to power for `(tiny, medium) × MDE {1, 2, 5} pp`
  at α 0.05 / power 0.8 off the pinned baseline rates, rendered as the
  `<!-- power:begin -->` block of `docs/AB_DESIGN.md`; same check /
  `WRITE=yes` shape; prints `power OK: 6 rows, block matches`
- `make readme [WRITE=yes]` *(Phase 13; `fix/front-door`)* — the README results block
  (`README.md`, marker-confined `<!-- readme:begin -->`) and the findings chart
  (`docs/img/lift.svg`, a wholly generated file), both rendered by `eval/cli.py
  readme` from `tests/pins.py` (which the committed `docs/RESULTS.md` blocks are
  pinned to) — no number typed by hand. Check mode diffs both byte-for-byte (exit 1 on drift);
  `WRITE=yes` (the literal only) rewrites both. No PROFILE; non-destructive
  (only the same generated bytes change); prints `readme OK: first-screen block
  matches, lift.svg matches`
- `make writeback PROFILE=<p> [TARGET=duckdb|spanner] [PROJECT=<id>]
  [CONFIRM=yes]` — the idempotent write-back (`serving/cli.py writeback`):
  upsert `scores_send_time` + the open-`dim_user` tz (`dim_user_current`) into
  `send_schedule`, replacing a user's row only on a strictly greater
  `(model_version, computed_as_of)` (`model_version` compared NUMERICALLY via
  `version_key` — `v10 > v2`, any non-`v<int>` refuses; Phase 10);
  `written_at = computed_as_of`. Prints `writeback OK: <p>, N users, M
  written` (`writeback OK: <project>.ontime → spanner, …` on the Spanner
  target, whose read is the warehouse's, not a PROFILE's build — PROFILE is
  optional there, validated if given) — a re-run over the same scores writes
  `0` (idempotent).
  `TARGET=duckdb` (default): `serving.send_schedule` in `data/<p>.duckdb`
  (the stand-in, §2.9) — no `CONFIRM` (create-if-not-exists + upsert, never
  destructive; a reset is `make drop-db … CONFIRM=yes`); needs `dbt-build`
  first. `TARGET=spanner` (Phase 10): reads the same two relations off
  BigQuery `ontime` (`candidates_sql` relation override) and writes the
  Spanner table via batch `insert_or_update` inside one read-write
  transaction — cloud-cost: `CONFIRM=yes` command-line origin and `PROJECT`
  validated BEFORE any client; needs the spanner-enabled stack and a
  `TARGET=bigquery` build
- `make pipeline PROFILE=<p>` — the local chain with no scheduler (`serving/cli.py
  pipeline`): `dbt build → eval → write-back` in one validated process,
  producing `scores_send_time` and `send_schedule`; prints `pipeline OK: <p>`.
  Phase 8b's Airflow DAG orders the WRITING steps (`dbt build → write-back`) as
  `make` targets; `eval` stays a union-only gate here and in CI
- `make test-int-airflow` — Phase 8b integration (behind `OTR_INT`, CI never runs
  it): spins the Docker-local Airflow (`SequentialExecutor`/SQLite), runs the DAG
  for a union interval and a three-interval backfill, asserts both container tables
  equal `make pipeline`'s (the `send_schedule` hash), tears down. Takes no
  variable (`tiny` by definition); needs Docker (Engine + `docker compose`)
- `make tf-validate` *(Phase 9a)* — offline Terraform check (`infra/cli.py`):
  `terraform -chdir=infra init -backend=false -input=false -lockfile=readonly`
  + `validate` + `fmt -check -recursive`; prints `tf-validate OK`. The init can
  never rewrite the pinned lock: on a platform `.terraform.lock.hcl` lacks a
  hash for, it FAILs (exit 1) until a deliberate `terraform providers lock
  -platform=…` + `tf-freeze` (§8 Gotchas). Downloads the google provider once from
  the registry (a setup step, outside the offline `make test`); no GCP auth, no
  cloud call
- `make tf-plan PROJECT=<id> [VARS='name=value,…']` *(Phase 9a; VARS
  `fix/tf-vars-argv`)* — validates `PROJECT` (a GCP project-id shape) before
  deriving `-var project_id=<id>`, parses each `VARS` item into an argv `-var`
  (`name=scalar` or `name=[n,n]`; malformed, whitespace, `project_id`, or an
  env-origin `VARS` → refused — `$(origin VARS)`, like `CONFIRM`), then
  `terraform -chdir=infra plan` under an ALLOWLISTED environment (`ENV_ALLOW`,
  seven exact names: `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`,
  `CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT` — so no credential, proxy or
  trust-anchor name, `TF_WORKSPACE`, `TF_DATA_DIR` or
  `TF_LOG*` reaches it; P2). Reads GCP APIs (your own ADC —
  never the impersonated SA, §8); shows the diff, creates nothing. EVERY
  `tf-*` REFUSES (exit 2, before terraform) while any `TF_VAR_*` /
  `TF_CLI_ARGS*` is in the environment, and plan/apply/destroy also while an
  auto-loaded `infra/terraform.tfvars` or `*.auto.tfvars{,.json}` exists
  (Amendment T) — the argv is the whole input by construction
- `make tf-apply PROJECT=<id> CONFIRM=yes [VARS=…] [ALLOW_DESTROY=yes]` /
  `make tf-destroy PROJECT=<id> CONFIRM=yes [VARS=…]` (no `ALLOW_DESTROY` —
  destruction is its purpose) *(Phase 9a; plan-first apply Phase 10)* — apply
  PLANS FIRST (`plan -out`), reads the saved plan back (`show -json`) and
  applies it only if every planned action is in `SAFE_ACTIONS = {no-op,
  read, create, update}` (Amendment N1): a destroy or replace needs
  `ALLOW_DESTROY=yes` with command-line origin (`$(origin ALLOW_DESTROY)`;
  the toggle-flip teardown passes it; an apply that merely omitted a
  currently-applied toggle stops with the addresses printed), and a plan it
  cannot read back or one carrying any other verb (`forget`, a future one)
  is refused ALWAYS; the saved plan is what gets applied — no
  `-auto-approve` on apply; an entry with no action refuses too (O2). In the
  environment, any name in the cloud-env domain (O1/P1/Q: the `GOOGLE_`/`GCLOUD_`/`CLOUDSDK_`/`GCE_METADATA_`/`SPANNER_` prefixes, the `_EMULATOR_HOST` suffix, the prefix-less names the libraries read, and the transport-redirection class — an enumerated closed set, the vendor scan a coverage aid) outside `CLOUD_ENV_ALLOW` refuses every project-taking
  `tf-*` (and every other cloud command) loudly, names only (N2/O1). Apply
  creates the free-tier layer: 9 API enablements (free, kept on by destroy),
  two BigQuery datasets, a GCS staging bucket, a least-privilege service
  account with 4 scoped grants, and budget alerts at 50/150 in the billing
  account's currency ($50/$150 on USD; notify only) — `Plan: 18 to add`;
  destroy removes everything in state (nothing billable left). Cloud-cost /
  destructive: `CONFIRM=yes` must have COMMAND-LINE origin (`$(origin
  CONFIRM)`); ask first, every time. Auth ADC/WIF only — never a keyfile.
  `enable_composer`/`enable_spanner` default false, so a default apply makes
  zero Composer/Spanner resources; `enable_ci_wif` defaults false, so it builds
  no WIF pool/provider/binding — CI auth is an explicit opt-in that also needs
  `github_repository` (no default repo is trusted); the provider name is then
  the `workload_identity_provider` output. `operator_principal` (default null)
  adds one grant — `serviceAccountTokenCreator` ON the SA — so that principal
  can impersonate it for manual BigQuery builds
- `make tf-migrate-state PROJECT=<id> CONFIRM=yes` *(`fix/tf-remote-state`)* —
  migrates Terraform state onto the GCS remote backend (`infra/cli.py
  migrate-state` runs `terraform init -migrate-state -input=false
  -lockfile=readonly -force-copy -backend-config=bucket=<project>-tfstate`).
  The versioned `<project_id>-tfstate` bucket is bootstrapped ONCE by hand
  (a bucket cannot create its own backend — `docs/DEPLOYMENT.md` §
  state-backend bootstrap, ask-first as the operator's ADC); the bucket is a
  PARTIAL backend config derived from the validated `PROJECT`, so no id enters
  the tracked `.tf`. Cloud-touching (writes state to GCS): `CONFIRM=yes`
  command-line origin, `PROJECT` validated first, and the same env gates as
  every `tf-*` (cloud-env refusal, no `TF_VAR_*` / auto-`tfvars`, `ENV_ALLOW`
  child env); prints `tf-migrate-state OK: <project>`. Takes no `VARS` (`init`
  has no toggles). Follow with `make tf-freeze CONFIRM=yes` for the `main.tf`
  change. Ask first
- `make tf-freeze CONFIRM=yes` *(Phase 9a)* — the ONLY writer of
  `infra/MANIFEST.sha256`, the content pin over every file Terraform loads
  under `infra/` (`*.tf`, `*.tf.json`) plus `.terraform.lock.hcl`, computed by
  the fixtures' `generator.manifest` (`tests/test_infra.py::
  test_tf_tree_matches_manifest` is red on any edit until this runs;
  `tf-validate`'s init is `-lockfile=readonly`, so it can never rewrite the
  pin); prints `tf-freeze OK: N files pinned in MANIFEST.sha256`. Overwrites a
  committed pin, so `CONFIRM=yes` needs command-line origin, and refuses when a
  pinned file has vanished from disk (delete it from the manifest by hand); the
  manifest hunk lands in the same commit as the `.tf` change
- `make test-int-bigquery PROJECT=<id> CONFIRM=yes [PROFILE=tiny]` *(Phase 9b)*
  — the DuckDB≡BigQuery pin-parity run behind `OTR_INT` (CI never runs it; the
  CI leg needs an explicit `enable_ci_wif = true` apply — BACKLOG, dated):
  `pipeline/cli.py test-int-bigquery` validates `PROJECT`/`PROFILE` and gates
  `CONFIRM` FIRST, then runs `tests/integration/test_int_bigquery.py` with
  `OTR_INT=1` + the validated project: lands tiny, builds on `bigquery`, reads
  the three golden tables back through the same `Golden` specs and diffs them
  against `fixtures/tiny/expected/` byte-for-byte, re-asserts the pins, and
  asserts exactly two datasets exist. Cloud-cost, ask first, as the SA
- `make spanner-load PROFILE=<p> PROJECT=<id> CONFIRM=yes` *(Phase 10)* — the
  Spanner dims landing (`landing/cli.py spanner-load`): the same
  `dims/dim_user.csv` the other landings select → the Spanner `dim_user`
  table (the production dims home BigQuery federates from, §2.3/§3.3),
  columns/types from the generated `landing/bq_schema.json`, one idempotent
  batch `insert_or_update` keyed `(user_id, valid_from)`; prints
  `spanner-load OK: <p> — N dim rows`. Cloud-cost: `CONFIRM=yes` command-line
  origin, `PROJECT` validated before any client, ADC never a key; verifies
  `MANIFEST.sha256` like `load`. Needs an `enable_spanner=true` apply
- `make test-int-spanner PROJECT=<id> CONFIRM=yes [PROFILE=tiny]` *(Phase 10)*
  — the Spanner/federation run behind `OTR_INT` (CI never runs it):
  `pipeline/cli.py test-int-spanner` validates (PROFILE is `tiny` only — the
  pins are tiny's; anything else is a CLI refusal) and gates `CONFIRM`
  FIRST, then runs `tests/integration/test_int_spanner.py`: lands the dims
  in Spanner, builds on `bigquery` with the `dim_user` source swapped to the
  federation view (`dbt_build`'s one validated var seam,
  `dim_user_identifier=dim_user_spanner`), asserts dbt's manifest resolved
  the source to the view, asserts the three goldens byte-for-byte, asserts
  the `raw.dim_user_spanner` view returns
  exactly the seed's rows, then runs the Spanner write-back twice — the
  second writes 0 and the read-back hashes to `SEND_SCHEDULE_SHA256_TINY`
  (cross-store byte parity). Cloud-cost, ask-first, as the SA; needs the
  ask-first `make tf-apply … VARS='enable_spanner=true'` (bills from creation
  — the dated apply/teardown lines in `docs/DEPLOYMENT.md` are filled the same session,
  and the scoped teardown is `VARS='enable_spanner=false'` re-applied)
- Later phases add their targets, each listed here in the same PR.

## Event model facts (from ARCHITECTURE.md §2; update if reality differs)

- Envelope: `insert_id`, `event_type`, `user_id`, `device_id`,
  `client_event_time`, `server_received_time`, `server_upload_time`,
  `event_properties`. Ids are counters, never UUIDs.
- Event types: `prompt_sent`, `prompt_delivered`, `prompt_opened`,
  `capture_started`, `upload_started|failed|completed`, `response_recorded`,
  `app_opened` (organic — the reachability signal).
- Upload delay = `server_received_time − client_event_time`. Skew beyond
  `SKEW_MAX_MIN` → `unattributed`.
- Labels: exactly one of five per prompt×user (= per `prompt_id`);
  precedence in ARCHITECTURE §2.5: `delivery_fault` → skew gate (a client
  clock AHEAD past `skew_max_min`, i.e. `min(upload_delay_seconds) <
  −skew_max_min·60`; a positive delay is never skew) → `on_time` →
  `upload_fault` → `timing_gap` → residual `unattributed`. The three-clock
  signal lives on `capture_started`/`upload_*` (`response_recorded` is
  backend-stamped). `attribution.cohort_id` is the prompt's own
  (`prompt_cohort_id`). `provisional` until `LOOKBACK_DAYS` closes, then
  `final` forever (Phase 7: `status` column on `attribution`, out of the golden;
  incremental partition `prompt_date` = local send date, computed on
  `attribution`). The three incremental models name their overwrite column
  under `meta.overwrite_partition_col` — `partition_by` is a key BOTH adapters
  interpret (dbt-bigquery: its native dict; dbt-duckdb: rejects a dict), so
  the native BigQuery `partition_by` dict is set under `target.type ==
  'bigquery'` only (Phase 9b, §8). Vars `skew_max_min` (5 = `generator/models.py`),
  `delivery_grace_min` (10), `unattributed_max` (0.10), `retention_days` (28),
  the send-time model's `feature_window_days` (30), `max_user_shift_min`
  (120), `shrinkage_pseudo_count` (5), `model_version` (`v1`), the
  incremental `lookback_days` (5 — Phase 7; `lookback_days·24 >
  late_arrival_max_hours` on every profile), `source_prune_margin_days`
  (5 — fix/append-landing: the BigQuery source-scan prune reaches
  `lookback_days + this` days below the horizon; a declared floor pinned per
  profile by `test_source_prune_margin_covers_every_profile`), and `dim_user_identifier`
  (`dim_user` — Phase 10: which relation the `raw.dim_user` SOURCE resolves
  to; the Spanner run overrides it to the federation view
  `dim_user_spanner`) in `dbt_project.yml`.
- Send-time model (Phase 5, `docs/METRICS.md` § scores_send_time):
  `features_user_hour` counts ORGANIC `app_opened` only (responses are
  exposure-biased), per `user_id` on each event's own local hour — a
  tz-change user has one histogram (BACKLOG row closed) — inside
  `(horizon − feature_window_days, horizon]`, `horizon = max(client_event_time)`.
  `scores_send_time`: one row per user (open `dim_user` row); hours are
  angles at bin centres (`h + 0.5`); the cohort prior is the pooled resultant
  (`μ_c`, `R̄_c`); `center_hour_local` = direction of `user vector +
  k·R̄_c·(cos μ_c, sin μ_c)`, `confidence = |combined| / (n + k)` — zero
  opens give `μ_c` and `R̄_c` exactly; `cohort_hour_local` = the hour whose
  `[h, h + window_minutes)` holds the most pooled opens (`h` over opened
  bins), ties → smallest opened hour;
  the served `send_hour_local:send_minute_local` is the centre clamped to
  `±max_user_shift_min` of it. Circular arithmetic is ANSI `floor`/`atan2`
  — no `%` (denylisted), no `mod` on floats, no sixth macro.
  `computed_as_of` = `max(client_event_time)` of the opens in the window.
  tiny: MAE 0.816201 h, coverage 0.6; medium (2,000 users, unfrozen): MAE
  0.352354 h, coverage 0.7345.
- On-time denominator is `prompts_delivered` = prompts with
  `delivered_in_grace`. Never user-days, never `prompts_sent`. Mart grain is
  `(cohort_id, prompt_date)` with `prompt_date` the LOCAL date
  (`cast(sent_at_local as date)`; a Tokyo 08:00 prompt is the previous UTC
  day). `ontime_rate` is NULL only when nothing was delivered; 0 when prompts
  were delivered and none on time. `ontime_retention.retained` is NULL until
  the window closes in the data (every tiny row) — descriptive only (§7).
- `dim_user.tz` is SCD2 (`valid_to` empty = open row); local time is computed
  once, in staging (`stg_events.client_event_time_local`), via `to_local_time`.
- `insert_id` is NOT unique in raw (the export carries duplicates; 44 in
  tiny); it is unique in `stg_events`. `error_code` is JSON `null` on
  `upload_started`/`upload_completed`, SQL NULL once staged.

## Determinism policy (core design principle)

"Could this step give a different answer on a re-run?" If yes, justify it in
DECISIONS.md or fix it.

- Same `SEED` + profile → byte-identical generator output. Counter ids
  (threaded across shards in shard order), a fixed `sim_start`, no UUID, no wall
  clock, emit order = arrival order within a shard (`profile.shards` derived
  `(seed, s·P_SHARD)` streams; one stream, whole-run arrival order, at
  `shards == 1` — DECISIONS fix/large-profile).
- No clock on the data path. dbt models take `run_date` / `as_of` as vars;
  `current_timestamp()` / `now()` in a model is a bug. `computed_as_of` is
  data-derived (`max(client_event_time)` over the inputs).
- Output is a function of content, never of order: every comparison sorts by
  the model's declared key; every tie-break names its key.
- Re-running any incremental model over the same raw converges; running a
  write-back twice is a no-op; a `final` label never changes.
- Truth isolation: `truth/` is never a dbt source, never an input to
  `features`/`scores`. `tests/test_truth_isolation.py` greps every pipeline
  directory (`landing/`, `pipeline/`, `dbt/`, `serving/`, `orchestration/`, `infra/`) for the
  word; in `generator/` only `truth.py` (the writer), `models.py` (record types) and
  `cli.py` (the entry point that calls the writer) may name it — generation
  logic never does.
- Model scoring and simulation are seeded; the generated blocks of
  `docs/RESULTS.md` and `docs/AB_DESIGN.md` regenerate byte-identically
  (`tests/test_simulate.py` / `tests/test_power.py` / `tests/test_holdout.py`
  under `make test` are the CI proof; `make simulate` / `make power` /
  `make holdout` check mode is the local one). The
  simulation uses common random numbers (four uniforms per prompt, one
  seeded stream, `prompt_id` order), so the lift is a function of the
  schedules alone. The holdout reads only warehouse columns (served hour,
  raw organic opens), and each of its two `dbt build`s runs in its own
  subprocess so no in-process adapter state leaks between the two target DBs
  (fix/holdout-eval).
- Non-deterministic by nature and carved out: dbt run ids and timings,
  Airflow run ids, Terraform apply output, BigQuery job ids. Nothing asserted
  reads them. The BigQuery build reproduces every DuckDB golden and pin
  byte-for-byte (`make test-int-bigquery`, Phase 9b) — parity is proven by
  diffing, never by re-freezing.

## Engineering contracts

- Schema contract: pydantic models in `generator/models.py` are the source of
  truth; the raw table DDL and dbt `sources.yml` are generated from them, never
  hand-edited. The generator validates on emit; staging asserts the columns.
- Label contract: the five-label set is an `accepted_values` test AND an
  `eval` check; a sixth label anywhere is a BLOCKER.
- Denominator contract: `on_time + upload_fault + timing_gap + unattributed
  == prompts_delivered` and `prompts_delivered + delivery_fault ==
  prompts_sent` per cohort-day is a dbt test
  (`assert_cohort_day_partition`); `delivery_fault` is counted beside the
  denominator, never inside it. Every metric is defined once in
  `docs/METRICS.md`; `schema.yml` links, never restates.
- Model-is-a-model: send-time scoring lives in `dbt/models/scores/`. Python
  never computes a score the pipeline serves.
- Write-back contract: replace only on strictly greater
  `(model_version, computed_as_of)`; key `user_id`.
- Boundary contract (Phase 10 round 4, Amendments N1–N3): a guard over an
  input the repo does not own — a vendor's JSON, a vendor's env-var
  namespace, a library's result type, a CLI's output — is an ALLOWLIST over
  a closed set or a strict parse to a declared shape, never a denylist of
  known-bad cases; a shape or value not recognised is REFUSED. The set is
  pinned exactly by a test, so widening it is a visible edit. Standing
  instances: `infra.cli.SAFE_ACTIONS` (plan actions), `CLOUD_ENV_ALLOW`
  (the Google env namespace), `ENV_ALLOW` (the terraform child), the
  strict `planned_changes` parse. A fix that adds the case a finding names
  to an existing check is not a fix (Workflow rules, "Fix the class").
- Credential standard (the same round; domain closed in round 5, O1): auth
  is ADC/WIF. A secret — key file, inline key, access token, credential-file
  override, an endpoint or metadata-host redirection — never sits in a file
  the repo controls, in code, a log or a message (refusals print NAMES,
  never values); the one credential at rest is gcloud's own ADC file under
  `CLOUDSDK_CONFIG`/`HOME`, outside the repo. If one reaches the pipeline's
  ENVIRONMENT the pipeline refuses to run: every name in the cloud-env
  domain (`infra.cli.in_cloud_namespace` — the vendor prefixes, the
  `_EMULATOR_HOST` suffix, the prefix-less names the libraries read, and the
  transport-redirection class `REDIRECTION_NAMES`) that
  is not a listed SETTING in `CLOUD_ENV_ALLOW` is a credential by
  definition, whenever it was introduced. The domain is an ENUMERATED closed
  set pinned exactly by a test (Q narrowed P1: the vendor-declaration/read
  scan is a coverage aid that flags a newly-read name, not the closure proof).
  A new vendor is its declarations and reads classified + a DECISIONS entry; a benign new
  variable refusing is the intended direction (one line to admit it).
- Adapter contract: a fake stands UNDER the thinnest adapter over a vendor
  type — it replaces the client, never the adapter. An adapter that is
  more than one library call is tested on the REAL type, built offline
  (protos, `__new__`), not on a fake of ours (round 4 #1: a mapping the
  fakes bypassed ran nowhere and survived the whole suite).
- Dialect contract: exactly five dispatch macros (JSON extract,
  `timestamp_diff`, `safe_divide`, `to_local_time`, partition overwrite; the
  fifth was added in Phase 2 with a DECISIONS entry). Each has a DuckDB body
  and a BigQuery body (Phase 9b: `json_value`, end-first `timestamp_diff` with
  both sides cast to `timestamp`, `safe_divide` on a `float64` numerator,
  `datetime(ts, tz)`; the partition-overwrite seam's BigQuery half is the
  adapter's native `insert_overwrite`, selected in the models' config on
  `target.type` — dbt-bigquery admits no custom strategy, so that dispatch
  body raises by design, Amendment U) — never a `default__`.
  A sixth needs a DECISIONS entry. `generate_schema_name` is a hook override
  keyed on `target.type`, not a dispatch macro. The `fix/append-landing`
  source-scan prune in `stg_events` is a documented carve-out from the five-macro
  rule (DECISIONS): native BigQuery `timestamp_sub` on a bare partition-column
  predicate, guarded to `target.type == 'bigquery'` — a macro would defeat
  partition pruning and there is no DuckDB body (it never runs there).
- Airflow contains no logic: a task is a `make` target or a dbt command.
- Minimal but scalable: simplest standard solution now; the scaling path is a
  DECISIONS note, not speculative code. Do not claim scale we don't run.

## Communication style (chat, comments, docs, commits)

- Result first: what changed / passed / failed, then details.
- Plain English, short sentences. No task restatement, no "I will now…", no
  closing summary that repeats the middle.
- One sentence if it fits. Explanations ≤ 4 sentences.
- Ban filler adjectives: "robust", "comprehensive", "production-ready",
  "seamless", "powerful". Show the property.
- Code comments only where the code can't say it. One-line docstrings unless
  behavior is non-obvious.
- Reports after a task: files touched, commands run, result, open risks, next
  step. Nothing else.

## Conventions

- Python 3.12 (`.python-version`). Type hints everywhere. No pandas on a
  pipeline path — SQL does the work; Python glues.
- Dependencies: ask before adding ANY package. Pre-approved allowlist by phase:
  pydantic (Phase 1); duckdb, dbt-core, dbt-duckdb (Phase 2); dbt-bigquery +
  its clients google-cloud-bigquery / google-cloud-storage, declared (Phase 9b;
  the adapter's ~45 transitive packages incl. pandas/pyarrow sit in the venv on
  no pipeline path — DECISIONS);
  apache-airflow via Docker only (Phase 8); google-cloud-spanner (Phase 10);
  dev: pytest, ruff, pre-commit. Anything else is a STOP-and-ask.
- SQL keywords lowercase, one column per line in select lists, every model
  ends with an explicit `order by`-free select (ordering belongs to readers).
- dbt: every model has a `schema.yml` with a description and at least one
  test; every var has a default in `dbt_project.yml` and is named in the spec
  that introduced it.
- Generator features (fault rates, injectors) are added one at a time, each
  with a profile knob and a test that uses the knob.
- Fault scenarios are generator profiles under `generator/profiles/`, not
  ad-hoc scripts.
- Secrets: never commit `.env`, `data/`, `*.tfvars`, credentials, service-
  account JSON. GCP auth is ADC / WIF only. A record — docs, specs, BACKLOG, DECISIONS —
  names a placeholder (`<project_id>`, `<operator>`, `<owner>/<repo>`), never a
  live project id, account address or repository slug; `check-docs` check 5
  pins every value position (`fix/public-release`); a bare id in prose is the
  security-reviewer's check.

## Teaching rule

The first time a stack concept appears in a session (dbt incremental
strategies, dispatch macros, BigQuery partitioning/clustering, Airflow data
intervals, Terraform state/modules, Spanner change streams/federation,
DuckDB-vs-BigQuery dialect), add a 2–4 sentence plain-language explanation of
what it is and why it is used here, BEFORE the implementation. Every line
merged must be explainable by the developer in a design review. Prefer the
simple, standard way over the clever way.

## Workflow rules

- The spec in `specs/` is the contract. Its DONE command is the only
  definition of done. Do not weaken failing tests. If a spec, fixture, or
  ARCHITECTURE.md seems wrong, STOP and report — never silently repair.
- Specs follow `specs/TEMPLATE.md`; its four REQUIRED sections — Invariants,
  Evidence, Record updates, Threat model — are mandatory. A spec without them
  is not approvable.
- Invariants before mechanisms: properties ("for all X, Y holds") each with
  the scenario test that falsifies it, written before any pinned decision
  names a mechanism; a pinned decision names a mechanism only by reference to
  the invariant it satisfies.
- A phase spec is finalized only after its predecessor merges. The FIRST
  commit on a phase branch is a spec-reconciliation amendment against main as
  it actually is; STOP for approval before implementing.
- ≤ ~6 pinned decisions / Done-when items per spec. Split otherwise (7a/7b).
- Before a phase: restate its "Done when" from `docs/PHASES.md`.
- Build at tiny scale first (`fixtures/tiny`), prove correctness, then turn up
  the profile.
- `fixtures/tiny/` is read-only after Phase 1. Re-freezing is a deliberate,
  signed-off change with a DECISIONS entry and a new MANIFEST.
- Exit cadence — the ONE statement; the Repo map, the agent table, the
  Project-tooling index and the agent file point here: at each phase exit,
  and at each `fix/` branch exit since the Phase 13 close, run the coherence
  audit and review BACKLOG.md for due items.
- Stack surprises: check official docs before working around; log under
  ARCHITECTURE.md §8 Gotchas.
- Do not add features outside ARCHITECTURE.md without asking.
- Destructive commands (dropping a DuckDB file, `terraform destroy`, Spanner
  deletes, truncating a serving table): only via a sanctioned `make` target
  that prompts unless `CONFIRM=yes` is given on the command line — tested
  with `$(origin CONFIRM)`; `unexport` does not distinguish origins.
- Cloud-cost commands (`terraform apply`, anything against BigQuery/Spanner/
  Composer): ask first, every time.
- Fix amendments: a fix that changes a data structure, a write path, or
  who-writes-what is a design change. One-paragraph spec amendment naming the
  invariant it restores, committed alone; STOP for approval. Wording-only and
  test-only fixes do not need one. A fix that RE-FREEZES a fixture needs a
  spec (`specs/fix-<slug>.md`, same template — the gate's `Freeze:` check
  reads a SPEC file), not only an amendment.
- Fix the class, not the case: before proposing any correctness fix,
  answer in the disposition "what set of inputs does this now accept, and
  is it closed?" A fix that appends the finding's case to an existing
  denylist, regex or `.get(…, default)` is refused as a fix (Engineering
  contracts, "Boundary contract"); the mechanism's KIND changes.
- Fix commits: one correctness finding per commit, the invariant it
  restores in the message; wording and record fixes batched in their own
  commit. Never a bulk "N findings applied" commit — Phase 10 rounds 1–3
  landed 24/21/15 findings in one commit each, every fix the minimal local
  patch, and the cap followed.
- Review cap: if two consecutive review rounds report correctness findings
  only in the previous round's fixes, stop patching. Write the invariant,
  re-implement against it ONCE, one scoped re-review. When it fires, the
  re-implementation replaces the mechanism's kind (denylist → allowlist or
  strict parse; a hand mapping → the library's own call, tested on the
  real type) and the amendment names the denylist it retires — never a
  longer list. A human applies this by comparing round N's table to round
  N−1's; `/review-round` prints the reminder, never a verdict.
- `/review-round N`: its deterministic half (`make review-gate`, `make
  mutate`) runs before any agent, every round.
- Scoped re-review: round N reviews round N−1's diff plus the spec's
  invariant list. A finding on code outside that range is labelled
  **"missed in round N−1"**.
- Commit at every green state with a descriptive message.
- End each loop with: what changed + decisions the spec didn't cover.

### Before reporting DONE

1. For every symbol deleted or renamed: grep the whole repo (docs, comments,
   specs, Makefile, CI, .claude/) and list each hit you updated.
2. For every Done-when item: name the test or command output that proves it.
3. For every new Makefile target with a variable or a delete: show behavior
   for an empty value, `../x`, a value containing `"; `, and the variable set
   from the environment rather than the command line.
4. For every new write path or model: can it give a different answer on
   re-run, on a non-UTC machine, with equal sort keys, or with a different
   `run_date`? Name the test pinning each.
5. For every new top-level package: is it pipeline code (guarded
   automatically by `test_truth_isolation.py`) or does it belong in that
   test's `EXEMPT` set? In the Repo map?
6. List every record file touched and every one the change implies you
   should have touched.
7. For each decision the spec didn't cover: the two alternatives not taken
   and why, one line each.
8. For every guard added or changed at an input the repo does not own: name
   the closed set or declared shape it accepts, the test that pins the set
   EXACTLY, and what an unrecognised input does (it refuses). For every
   adapter over a vendor type: the test that runs it on the real type.

## Git workflow (one branch + one PR per phase)

- `main` is protected: never commit to it directly; never force-push.
- Review gate BEFORE the remote: run the agents on the finished work and
  report verdicts. Do NOT push or open a PR until the developer has seen the
  verdicts and says to.
- STOP-on-findings: when any agent returns findings, STOP and report them
  verbatim. Do NOT fix anything — not even a trivial one — until the developer
  has reviewed the issue AND the proposed fix and says proceed.
- One consolidated report, not one per agent: wait for every agent in the
  round to finish, then present a single table over all findings (finding,
  raised by, file:line, class, in-range / missed) followed by one verdict line
  per agent. Never relay agent results one at a time as they arrive.
- Start each phase: `git checkout main && git pull && git checkout -b
  phase-N-<slug> && make round-reset`. One phase = one branch = one PR;
  `round-reset` clears the prior phase's local round tags so review round 1
  does not collide with them. Run it at phase start ONLY — mid-phase it would
  delete the current round's boundary.
- Commits small, at green states, prefixed `phase-N:`.
- PR via `gh pr create` when Done-when passes AND verdicts are approved. Body:
  Done-when check + output, files touched, decisions the spec didn't cover,
  open risks. Title `Phase N — <name>`.
- CI runs `make lint`, `make check-docs`, `make test` (and from Phase 2,
  `dbt build` on DuckDB). Mergeable only when CI is green and code-reviewer +
  functionality-tester have run.
- The developer merges (squash), never Claude. After merge: `git checkout
  main && git pull`.
- Hotfixes on `fix/<slug>` from main, same rules. Never mix two phases in a
  PR; a needed change in an earlier phase is a STOP and its own fix PR.

## Which review agents run (by diff surface)

The deterministic gate (`make review-gate`, `make mutate`) runs on EVERY
range. Agents run only when the range touches their surface — derived from
`git diff --name-only <RANGE>`, a lookup, not a judgment:

| Surface touched in the range | Agents |
|---|---|
| Code: `*.py`, `dbt/**` (models, macros, tests, yml), `Makefile`, `scripts/`, `tests/`, `generator/`, `eval/`, `serving/`, `orchestration/`, `infra/**/*.tf` | code-reviewer, then functionality-tester |
| Sensitive: `.github/`, `infra/`, `serving/`, `orchestration/` (Docker image / `docker-compose` / the container-spinning `test-int-airflow`), `.env*`, `.dockerignore`, `dbt/profiles.yml`, `.claude/hooks/`, `.claude/settings*.json`, any target that deletes / applies / takes `CONFIRM` | + security-reviewer |
| Docs and records only: `*.md` (incl. `specs/`, `docs/`, `DECISIONS.md`, `BACKLOG.md`, `CLAUDE.md`, `.claude/agents|commands/*.md`) | coherence-auditor only, scoped to the changed docs (drift and stale-record checks; no code to review or run) |
| Any of the above at an exit (Workflow rules, "Exit cadence") | + coherence-auditor over the whole repo (mandatory) |

A range that mixes surfaces runs the union. A docs-only range still runs the
gate (`check-docs`, the BACKLOG count, Evidence/Record checks). Running an
agent whose surface is untouched is waste and a source of noise findings;
skipping one whose surface IS touched is a STOP.

## Project tooling

Index only. All agents are report-only by contract: none carry Write/Edit
(one carve-out: functionality-tester may `git worktree add` a throwaway
checkout under `mktemp -d`, mutate THERE, and remove it; `git status
--porcelain` AND `git worktree list` must match before and after). Findings
are fixed in the main session or explicitly accepted — never auto-fixed.

- `run-tests` hook — `.claude/hooks/run-tests.py` (committed); after any .py
  edit in this repo, runs pytest and blocks on red; "no tests collected" is a
  skip. Wiring is local-only by design (a committed settings.json would
  auto-execute an inbound branch's hook for anyone opening it). Enable by
  copying into the gitignored `.claude/settings.local.json`:
  `{"hooks": {"PostToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit",
  "hooks": [{"type": "command", "command": "python3
  \"$CLAUDE_PROJECT_DIR/.claude/hooks/run-tests.py\""}]}]}}`.
  Running pytest on an inbound branch still executes that branch's
  conftest.py — review it in the GitHub UI first.
- `block-secrets` hook — `~/.claude/hooks/block-secrets.py` (user-level,
  already wired); blocks writes containing secret-looking values.
- `code-reviewer` agent — `.claude/agents/`; diff review against this file
  (determinism, truth isolation, schema/label/denominator contracts,
  allowlist, read-only fixtures, dbt conventions). When the range touches
  code (table above).
- `security-reviewer` agent — mandatory when CI, `.env`, `infra/`, IAM,
  service accounts, Spanner credentials, or a destructive target are touched.
- `functionality-tester` agent — runs the suite + the spec's DONE command,
  mutation step, Evidence rows. After code-reviewer, same trigger.
- `coherence-auditor` agent — whole-repo drift audit vs CLAUDE.md /
  ARCHITECTURE.md / PHASES.md / DECISIONS.md. MANDATORY once at each exit
  (Workflow rules, "Exit cadence"), before merge; the ONLY agent for a
  docs-only range (scoped to the changed docs).
- `/selfcheck` — `.claude/commands/selfcheck.md`; verifies the last commit,
  then stops.
- `/review-round N` — `.claude/commands/review-round.md`; gate → mutate →
  invariants → agents selected by diff surface → one table → tag → "Cap is
  the architect's call".
- `strategic-compact` skill — user-level; suggests /compact at breakpoints.

## Current status

**Phases 0–13 complete — the project is closed.** Phase 13 is the planned final
phase (`docs/PHASES.md`); there is no Phase 14. The pipeline runs two ways from
one codebase: LOCAL (DuckDB, meter off) for the whole correctness story, and
CLOUD (BigQuery + Spanner + Composer-managed Airflow, ask-first) — proven live
and torn down, nothing billable left up (the free-tier layer — two datasets,
bucket, SA + grants, budget — is cents/month; `make tf-destroy … CONFIRM=yes`
removes it). Every served number is a pin (`tests/pins.py`) or a committed
generated block; the README first screen and the findings chart regenerate
byte-identically under `make test`. The phase-by-phase trail lives in
`docs/PHASES.md` (Delivered paragraphs) and `DECISIONS.md`; this section is the
pointer, not the log.

**Phase 13** (`phase-13-docs-narrative`, spec
`specs/phase-13-docs-narrative.md`, merged as PR #20): the docs-and-narrative capstone
— `README.md` (a first-screen `make readme` block + a Mermaid architecture
diagram + a cloud-free quickstart + the docs index + the stack-roles table),
`docs/img/lift.svg` (the generated findings chart), `docs/INSIGHT.md` (the honest
one-pager — tiny's NEGATIVE simulated lift, the simulation's circularity, the A/B
as the real test), the ARCHITECTURE Amplitude-export mapping (§2.10) + the
privacy/PII paragraph (§6), and `check-docs` widened over the now-tracked README
and `TRACES`. `make readme` reuses Phase 6's marker-confined writer
(`eval/blocks.py`) from `tests/pins.py` (which the committed RESULTS blocks are
pinned to) — not one number a reader sees is typed; `tests/test_readme.py` regenerates both artifacts
byte-identically. No pin, fixture, model, or `.tf` moved.

Open BACKLOG rows: **17** — `fix/append-landing` (2026-09-03, ROADMAP item 6)
made raw landing append-only. The writer emits the §2.10 export shape — gzipped
hourly files `events_<date>_<HH>.jsonl.gz` (`filename=""` + `mtime=0` + fixed
level, byte-reproducible; `fixtures/tiny` re-frozen 10 → 169 files). The DuckDB
landing keeps `raw.events` across loads and overwrites the selected upload-date
partitions (delete-then-insert per `cast(server_upload_time as date)`); BigQuery
lands a `WRITE_TRUNCATE` per `raw.events$YYYYMMDD` on a DAY-partitioned table;
re-landing a date adds 0 net rows, `raw.dim_user` is a full replace, a payload
conflict rolls the load back, and `THROUGH` accumulates forward within a
warehouse. On BigQuery only, `stg_events` prunes its source read to a
derived-margin (`ceil(late_arrival_max_hours/24) + tz_days + 1`) superset
upload-time window that keeps every duplicate's copies co-located (≤ 1 h apart, a
generator invariant pinned by `test_duplicate_upload_span_bounded`), closing the
measured item-6 cost (an unpartitioned raw forced a 19.45 GB incremental
re-scan). DuckDB SQL is untouched, so every DuckDB golden is byte-identical
(attribution / ontime_rate_daily / scores_send_time — 0 differ) and the
MAE/coverage/accuracy/holdout pins hold; only the raw-structure pins moved
(`RAW_FILES` 10 → 169, `PHASE1_MANIFEST_LINES` 13 → 172). Two commits (packaging
+ DuckDB append-only + re-freeze; then the BigQuery prune + DAY-partitioned
landing) plus the records. **Remaining live proof: `make test-int-bigquery`
(ask-first, cents) — the BigQuery byte parity + pruned-bytes RESULTS line.**
Struck the item-6 BACKLOG row and opened one (gzip fixture reproducibility is
proven same-machine only — the frozen manifest assumes CI's zlib, no in-suite
canary); the append-landing on Spanner dims, finer-than-day partitioning, and
Composer-on-a-schedule (item 7) stay out of scope (BACKLOG if a case appears).
The review round found no survivors and no blockers; its should-fixes (the prune
margin is now a test-pinned var, the dialect carve-out is recorded, `_file_date`
asserts its shape) landed before merge. **Live proof still owed: `make
test-int-bigquery` (ask-first).** Prior: `fix/holdout-eval` (2026-09-02, ROADMAP item 4) added
the temporal holdout, the non-circular counterpart to the counterfactual
simulation (ARCHITECTURE §7 report (d), the opening amendment committed alone).
`eval/cli.py holdout` serves a schedule on data landed ≤ an upload-date cut
(`THROUGH`), then scores it against the RAW organic `app_opened` opens uploaded
after the cut — read off the warehouse, never `truth/`, never a reachable-window
or centre quantity, no clock. Two arms (`recommended` served hour, `cohort` band
anchor), two measures per arm: `in_window_share` (opens within ±1 h of the served
hour) and `mean_nearest_hours` (mean circular distance to a user's nearest
held-out open). It builds two throwaway DuckDB warehouses (served ≤ cut, full for
the held-out opens — one build's scores would have seen the held-out opens,
circular). On medium's 21,840 unseen opens the per-user schedule beats the cohort
band on both (share +0.065, nearest 1.096 → 0.613 h) — the proof; tiny (94 opens)
is the frozen regression pin. Both blocks live in `docs/RESULTS.md` beside the
simulation, pinned in `tests/pins.py` (`HOLDOUT_CUTS`, `HOLDOUT_WINDOW_HOURS`,
`HOLDOUT_TINY`, `HOLDOUT_MEDIUM`), byte-identical under `make test`
(`tests/test_holdout.py`). Struck the item-4 BACKLOG row, marked ROADMAP item 4
landed; no pin, fixture, model, or `.tf` moved. Prior: `fix/large-profile`
(2026-09-02, ROADMAP item 5, the
last branch in the one-week cut) published the real-scale cost numbers: a `large`
profile (200,000 users × 30 days, `shards` 200; 35,498,190 events, ~10 GB events
JSONL, 41.9 M records incl. truth/dims) and the
generator sharded into `profile.shards` derived `(seed + s·P_SHARD)` streams —
an amendment to the one-`Random` invariant, committed alone first (DECISIONS),
emit order preserved within a shard, counter ids threaded across shards, so
`tiny` (MANIFEST match) and `medium` reproduce byte-for-byte at shard 1 and every
DuckDB golden is untouched. The streaming writer (`write_output_streaming`,
`JsonlAppender`, `TruthStream`) bounds a sharded run to one shard in memory. On
BigQuery (ask-first, ≤ $5 cap, session ≈ $0.28 on the already-applied free-tier;
state migrated to the GCS backend this session — ROADMAP item 2's deferred step,
`tf-plan` 0/0): the full `--full-refresh` build scanned 18.33 GB / 1.37 M slot-ms
/ ≈ $0.11 in 5 m 11 s (`PASS=126`, identical to tiny), per-model bytes and the
mart partition-pruning proof are in `docs/RESULTS.md`. The measured item-6 case:
raw is unpartitioned, so the incremental re-run re-scans all of raw (19.45 GB, no
cheaper) — `fix/append-landing` is now backed by a number. Struck the real-scale
BACKLOG row, marked ROADMAP item 5 landed, and re-confirmed Spanner/Composer
clean (`Listed 0 items.`) at exit. Prior: `fix/scores-dim-current` (2026-09-02,
ROADMAP item 3) closed the last layering wart: `scores_send_time`'s `users` CTE now reads
`ref('dim_user_current')` for the open dim row instead of re-deriving it from
`source('raw', 'dim_user')` — the mart already computes that open row (and the
write-back already reads it). A zero-behaviour refactor (no spec amendment; the
fix/landing-package reasoning, recorded in DECISIONS): the three goldens are
byte-identical (`make scores-golden` / `report` / `attribution-golden`, 0 differ
each), tiny and medium MAE/coverage pins hold, the five scores dbt unit tests
now mock `ref('dim_user_current')` with unchanged expected rows, and the moved
DAG edge is pinned (`test_scores.py::test_scores_depends_on_dim_user_current_not_raw`,
off dbt's manifest). The federation seam is untouched — `raw.dim_user` is still
consumed, now through `dim_user_current`, so `test-int-spanner`'s
source-resolution assertion still holds. Struck the scores/dim_user_current row,
marked ROADMAP item 3 landed, and re-confirmed Spanner clean at exit. Prior:
`fix/tf-remote-state` (2026-09-01, ROADMAP item 2)
moved Terraform state to a versioned GCS remote backend: the drafted `backend
"gcs"` in `infra/main.tf` is uncommented as a PARTIAL config (no project id in
the `.tf` — the bucket is a `-backend-config` from the validated `PROJECT`), a
new gated target `make tf-migrate-state PROJECT=<id> CONFIRM=yes` runs `init
-migrate-state` under the shared `tf-*` env gates, the versioned
`<project_id>-tfstate` bucket is a hand-bootstrapped ask-first operator step
(`docs/DEPLOYMENT.md`), and `tf-freeze` re-pinned the manifest. Struck the
tfstate row (durable, recoverable state before a persisting apply), emptied
`FUTURE_TARGETS` (the target is built), and re-confirmed Spanner clean at exit.
`fix/process-doc` (2026-09-01, ROADMAP item 1b) landed
`docs/PROCESS.md`, the INSIGHT closing pass and `tests/test_insight.py` (the
essay's typed figures equal their pins; the six-decimal set is exact), struck
the phase-log row and the INSIGHT value-parity row, opened one at exit (the
"why trust it" claim is restated in three docs with no parity test), reframed
the tfstate row from a confidentiality gate to a durability one (same trigger),
and re-confirmed Spanner clean. `fix/front-door` (2026-09-01, ROADMAP item 1a) retold
the README as a story with its one number generated inside the block, struck the
structural-labels row (the counts now come from the pins), opened one (nothing
executes the quickstart in CI), re-deferred the front-door row to item 1b
(`fix/process-doc`: PROCESS.md + the INSIGHT pass) and re-confirmed Spanner clean.
`fix/public-release` (2026-09-01, the pre-publication security review) struck the
live-project-id row and opened one (the GitHub-side settings a public repo needs,
outside the tree): every record reads `<project_id>` / `<operator>` and
`check-docs` check 5 pins every value position (re-implemented once in round 2,
the review cap preempted), the history copies are accepted in DECISIONS,
`.gitignore` and `.dockerignore` carry ONE pinned secret-glob set; at exit
re-confirmed Spanner clean (`Listed 0 items.`) and re-deferred the local-tfstate
row to `fix/tf-remote-state` BEFORE the visibility flip. The post-13 roadmap
(`docs/ROADMAP.md`,
2026-09-01, branch `fix/roadmap`) opened four (the front-door reframe, the
scores→`dim_user_current` layering fix, the temporal holdout eval, the
append-only landing) beside the four rows it cites, and re-anchored the BACKLOG
review and the mandatory coherence audit to every `fix/` branch exit too
(stated once, Workflow rules "Exit cadence"). Phase 13 opened four: two obligations named as their
own future branches (the Composer-runnable DAG via Cosmos / `KubernetesPodOperator`,
superseding Option A; a real-scale cost run) and two review-exit latent-staleness
notes (the readme's non-pin-derived structural labels; INSIGHT's hand-typed
figures lacking a value-parity test); re-stated the tfstate confidentiality half
(row 16, due → `fix/tf-remote-state`, not built in a docs phase) and the CI-WIF
parity leg (row 30); re-confirmed Spanner clean (row 15) at exit. The full
disposition history is in `BACKLOG.md` and each phase's Delivered paragraph in
`docs/PHASES.md`.


(Update this section at the end of every working day.)
