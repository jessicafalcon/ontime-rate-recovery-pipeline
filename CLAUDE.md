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

`docs/ARCHITECTURE.md` is the spec. `docs/PHASES.md` is the plan.
`PROJECT_BRIEF.md` is the origin and the architecture-review log. Read all
three before design decisions.

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
  each. `docs/PHASES.md` is the list; specs are the executable contracts.
- `generator/` — `models.py` (pydantic, schema source of truth), `profiles.py`
  + `profiles/*.json` (every knob a required field), `generate.py` (cause-first,
  one `Random`, `SIM_START` fixed), `response.py` (the one response function,
  reused by Phase 6), `dims.py` (SCD2 seed), `writer.py` (canonical JSON/CSV;
  refuses `fixtures/`), `manifest.py`, `truth.py` (the ONLY truth writer),
  `cli.py` (`seed`, `freeze`). Truth goes to `<out>/truth/`, never read by the
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
- `loader/` *(Phase 2; 9b; 10)* — raw landing: `load.py` (fixtures → DuckDB
  `raw` schema, types from the generated `ddl.sql`), `bq.py` (Phase 9b: the
  same files → GCS staging → BigQuery `raw`, schema from the generated
  `bq_schema.json`, `WRITE_TRUNCATE`; every cloud call through an injectable
  `Clients` factory — the offline suite injects fakes), `spanner.py`
  (Phase 10: the same dim seed → the Spanner `dim_user` table, contract
  types from `bq_schema.json`, idempotent batch upsert, injectable client),
  `cli.py` (`load`, `bq-load`, `spanner-load`, `dbt-build` — lands by
  target — `drop-db`, `test-int-bigquery`, `test-int-spanner`).
  `require_confirm` is the ONE cloud gate (CONFIRM origin + the cloud-env
  allowlist, both imported from `infra.cli` — where `confirmed()`, the one
  origin predicate the integration fixtures' carried gate shares, and
  `CLOUD_ENV_ALLOW` live). Pipeline code — guarded by
  `test_truth_isolation.py`.
- `eval/` *(Phase 3+)* — the ONLY code that reads truth: `score.py` (label
  accuracy vs `truth/prompts.jsonl`; Phase 5: reachable-centre MAE and
  coverage vs `truth/users.jsonl`, off the model's own columns — never a
  centre Python derived), `golden.py` (a built table as canonical CSV +
  diff — one `Golden` spec per frozen file: attribution,
  `ontime_rate_daily`, `scores_send_time`), `report.py` (the overall rate
  off the mart), `cli.py` (`golden`, `score`, `report`, `scores-golden`;
  `truth_dir` = `fixtures/<p>/truth` when frozen, else
  `data/out/<p>/truth`, printed `(unfrozen)`; Phase 6: `simulate`,
  `power`), `simulate.py` (Phase 6: the counterfactual simulation — three
  arms under common random numbers through
  `generator.response.open_probability`, reading the SERVED pair and the
  band anchor, never `center_hour_local`), `power.py` (the A/B power
  table, `math.erf` + bisection), `blocks.py` (the marker-confined writer
  of generated doc blocks). Writes console, `data/out/<p>/expected/` and
  the marked blocks of `docs/RESULTS.md` / `docs/AB_DESIGN.md` only —
  never a table, never `fixtures/`.
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
- `orchestration/` *(Phase 8b)* — the Airflow DAG (Docker-local), no logic, only
  ordering: `dags/pipeline_dag.py` (BashOperators over `make` targets, `dbt_build
  >> writeback`, `max_active_runs=1`, `catchup=False`, `THROUGH` via `{{
  data_interval_end | ds }}`), `tasks.py` (the Airflow-free ordered-command
  manifest the DAG and the offline structure test share), `Dockerfile` +
  `docker-compose.yml` (lean `SequentialExecutor`/SQLite; `apache-airflow` never
  in `uv.lock`). A pipeline dir — `test_truth_isolation.py` covers it. `eval` is
  NOT a DAG task (union-only gate — reads truth, asserts full-data pins).
- `infra/` *(Phase 9a; 10)* — Terraform. `main.tf`/`variables.tf`/`outputs.tf` +
  `modules/{bigquery,gcs,iam,budget}` (unconditional, free/near-free) and
  `modules/{composer,spanner}` `count`-gated behind `enable_*` toggles that
  default false (so is the CI WIF layer inside `iam`: `enable_ci_wif`).
  Phase 10 filled the spanner module: instance (100 PU), database with the
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
  `cli.py` (validates `PROJECT`, gates `tf-apply`/`tf-destroy`/`tf-freeze` on
  `CONFIRM=yes $(origin)`; toggles only as a command-line `VARS` → argv
  `-var`, refuses `TF_VAR_*`/`TF_CLI_ARGS*`, auto-loaded tfvars and any
  `GOOGLE_*`/`GCLOUD_*`/`CLOUDSDK_*` variable outside `CLOUD_ENV_ALLOW` (the
  one allowlist every cloud command shares — Amendment N2; the plan-first
  apply's action allowlist `SAFE_ACTIONS` is N1), runs terraform under an
  env allowlist —
  `fix/tf-vars-argv`) drives
  `make tf-validate|tf-plan|tf-apply|tf-destroy|tf-freeze`.
  `terraform.tfvars.example` only (never a `*.tfvars`); `.terraform.lock.hcl`
  is tracked (the provider pin); ADC/WIF, never a key. A pipeline dir — guarded
  by `test_truth_isolation.py`; the `.tf` tree is pinned byte-for-byte by
  `MANIFEST.sha256` (`make tf-freeze CONFIRM=yes` its only writer) plus the
  static property checks in `tests/test_infra.py`.
- `fixtures/tiny/` — golden `raw/events_<upload-date>.jsonl` + `dims/` +
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
  table, operator permissions, teardown, the optional kill-switch; all under
  `docs/`).
- `DECISIONS.md` — why-not-X log. One entry per non-obvious choice.
- `BACKLOG.md` — deferred findings with revisit triggers. Reviewed at every
  phase exit: do due items or re-defer with a new trigger, never drop.
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
  CLAUDE.md, README (read only if one is tracked — none today), docs/,
  PROJECT_BRIEF, DECISIONS, BACKLOG resolves; every
  `make <target>` the LIVING docs name exists in the Makefile (ARCHITECTURE,
  PHASES and PROJECT_BRIEF are plans — link-checked only); every trace token in `TRACES` exists in source as an exact token;
  this file's "Open BACKLOG rows: **N**" equals BACKLOG.md's un-struck rows
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
  `fixtures/<p>/{raw/events_*.jsonl,dims/dim_user.csv}` into `data/<p>.duckdb`
  schema `raw`; prints `load: source=…` (falls back to `data/out/<p>/`,
  marked `(unfrozen)`), verifies `MANIFEST.sha256` first when one exists
  (`load DRIFT`, exit 1); types come from the generated `loader/ddl.sql`, never
  inferred. Idempotent: tables are recreated. `THROUGH` (an upload date
  `YYYY-MM-DD`, validated, never a path) lands only the files uploaded on or
  before it — a landing is the raw-table state (Phase 7); empty loads them all
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
  *(Phase 9b)* — the BigQuery landing alone (`loader/cli.py bq-load`): the same
  files `load` selects → `gs://<id>-ontime/landing/<p>/{raw,dims}/` → `raw.events`
  / `raw.dim_user` with the schema GENERATED from `generator/models.py`
  (`loader/bq_schema.json`), one `WRITE_TRUNCATE` load job per table — the
  only landing mechanism; an empty selection lands a zero-byte
  `_empty.jsonl` through it (BigQuery rejects a job over zero URIs; §8) —
  idempotent; prints `bq-load OK: <p> — N files[, landing ≤ <THROUGH>], E
  event rows, D dim rows`. Cloud-cost (cents):
  `CONFIRM=yes` command-line origin; `PROJECT` validated before any client;
  ADC (impersonated SA), never a key. Verifies `MANIFEST.sha256` like `load`
- `make drop-db PROFILE=<p> CONFIRM=yes` — deletes `data/<p>.duckdb` and its
  `.wal` (nothing else); `CONFIRM=yes` must have command-line origin
- `make gen-sources` — re-renders `loader/ddl.sql`, `loader/bq_schema.json`
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
- `make power [WRITE=yes]` — the A/B power table (`eval/cli.py power`):
  users per arm and days to power for `(tiny, medium) × MDE {1, 2, 5} pp`
  at α 0.05 / power 0.8 off the pinned baseline rates, rendered as the
  `<!-- power:begin -->` block of `docs/AB_DESIGN.md`; same check /
  `WRITE=yes` shape; prints `power OK: 6 rows, block matches`
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
  ten exact names: `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`,
  `CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT`, `SSL_CERT_FILE`, `NO_PROXY`,
  `HTTPS_PROXY` — so no credential name, `TF_WORKSPACE`, `TF_DATA_DIR` or
  `TF_LOG*` reaches it). Reads GCP APIs (your own ADC —
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
  `-auto-approve` on apply. Any `GOOGLE_*`/`GCLOUD_*`/`CLOUDSDK_*` variable
  outside `CLOUD_ENV_ALLOW` in the environment refuses every project-taking
  `tf-*` (and every other cloud command) loudly, names only (N2). Apply
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
  `loader/cli.py test-int-bigquery` validates `PROJECT`/`PROFILE` and gates
  `CONFIRM` FIRST, then runs `tests/integration/test_int_bigquery.py` with
  `OTR_INT=1` + the validated project: lands tiny, builds on `bigquery`, reads
  the three golden tables back through the same `Golden` specs and diffs them
  against `fixtures/tiny/expected/` byte-for-byte, re-asserts the pins, and
  asserts exactly two datasets exist. Cloud-cost, ask first, as the SA
- `make spanner-load PROFILE=<p> PROJECT=<id> CONFIRM=yes` *(Phase 10)* — the
  Spanner dims landing (`loader/cli.py spanner-load`): the same
  `dims/dim_user.csv` the other landings select → the Spanner `dim_user`
  table (the production dims home BigQuery federates from, §2.3/§3.3),
  columns/types from the generated `loader/bq_schema.json`, one idempotent
  batch `insert_or_update` keyed `(user_id, valid_from)`; prints
  `spanner-load OK: <p> — N dim rows`. Cloud-cost: `CONFIRM=yes` command-line
  origin, `PROJECT` validated before any client, ADC never a key; verifies
  `MANIFEST.sha256` like `load`. Needs an `enable_spanner=true` apply
- `make test-int-spanner PROJECT=<id> CONFIRM=yes [PROFILE=tiny]` *(Phase 10)*
  — the Spanner/federation run behind `OTR_INT` (CI never runs it):
  `loader/cli.py test-int-spanner` validates (PROFILE is `tiny` only — the
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
  late_arrival_max_hours` on every profile), and `dim_user_identifier`
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

- Same `SEED` + profile → byte-identical generator output. Counter ids, a fixed
  `sim_start`, no UUID, no wall clock, emit order = arrival order.
- No clock on the data path. dbt models take `run_date` / `as_of` as vars;
  `current_timestamp()` / `now()` in a model is a bug. `computed_as_of` is
  data-derived (`max(client_event_time)` over the inputs).
- Output is a function of content, never of order: every comparison sorts by
  the model's declared key; every tie-break names its key.
- Re-running any incremental model over the same raw converges; running a
  write-back twice is a no-op; a `final` label never changes.
- Truth isolation: `truth/` is never a dbt source, never an input to
  `features`/`scores`. `tests/test_truth_isolation.py` greps every pipeline
  directory (`loader/`, `dbt/`, `serving/`, `orchestration/`, `infra/`) for the
  word; in `generator/` only `truth.py` (the writer), `models.py` (record types) and
  `cli.py` (the entry point that calls the writer) may name it — generation
  logic never does.
- Model scoring and simulation are seeded; the generated blocks of
  `docs/RESULTS.md` and `docs/AB_DESIGN.md` regenerate byte-identically
  (`tests/test_simulate.py` / `tests/test_power.py` under `make test` are the
  CI proof; `make simulate` / `make power` check mode is the local one). The
  simulation uses common random numbers (four uniforms per prompt, one
  seeded stream, `prompt_id` order), so the lift is a function of the
  schedules alone.
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
- Credential standard (the same round): auth is ADC/WIF. A secret — key
  file, inline key, access token, credential-file override — exists only
  as an environment variable, never in a file, code, a log or a message
  (refusals print NAMES, never values), and the pipeline refuses to run
  with one present: every `GOOGLE_*`/`GCLOUD_*`/`CLOUDSDK_*` name that is
  not a listed SETTING in `CLOUD_ENV_ALLOW` is a credential by definition,
  whenever it was introduced. A new vendor is its prefix + its settings +
  a DECISIONS entry; a benign new variable refusing is the intended
  direction (one line to admit it).
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
  keyed on `target.type`, not a dispatch macro.
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
  account JSON. GCP auth is ADC / WIF only.

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
- At each phase exit: run the coherence audit and review BACKLOG.md for due
  items.
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
  test-only fixes do not need one.
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
| Any of the above at a phase exit | + coherence-auditor over the whole repo (mandatory) |

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
  ARCHITECTURE.md / PHASES.md / DECISIONS.md. MANDATORY once at each phase
  exit, before merge; the ONLY agent for a docs-only range (scoped to the
  changed docs).
- `/selfcheck` — `.claude/commands/selfcheck.md`; verifies the last commit,
  then stops.
- `/review-round N` — `.claude/commands/review-round.md`; gate → mutate →
  invariants → agents selected by diff surface → one table → tag → "Cap is
  the architect's call".
- `strategic-compact` skill — user-level; suggests /compact at breakpoints.

## Current status

**Phase 9 complete (9a PR #12, 9b PR #13); `fix/tf-vars-argv` merged (PR #14,
2026-08-30); Phase 10 in flight.**
Phases 0–8 merged (PRs #1–#11); **9a merged as PR #12** (2026-08-29, amendments
A–T, 8 review rounds) — the Terraform foundation, meter off by default, plan-clean
and destroy-empty proven live; **`fix/tf-vars-argv` merged as PR #14**. **9b** owns Phase 9's two warehouse clauses: `make
dbt-build TARGET=bigquery PROFILE=tiny` green with the same pins, and `make
test-int-bigquery`. Landed on the branch (spec `specs/phase-9b-bigquery-dialect.md`,
reconciliation items 1–9 approved 2026-08-29; item 4 = (b), the CI WIF job
deferred with a dated trigger): the five `bigquery__` macro bodies (no
`default__`, still five); `generate_schema_name` collapsing to `ontime` on
`target.type == 'bigquery'` only — **DuckDB keeps `main_<folder>`**, no reader
changed; the incremental models' overwrite column under
`overwrite_partition_col` with a dialect-guarded native `partition_by` dict
(the key collision — §8); the GCS→BigQuery landing `loader/bq.py` on
dbt-bigquery's transitive google clients (one impersonated-ADC path, generated
`bq_schema.json`, `WRITE_TRUNCATE`, injectable fake clients); `dbt_build` lands
by target (Amendment S lifted; `OTR_GCP_PROJECT` from the validated `PROJECT`;
`location: us-central1`); `make bq-load` / `make test-int-bigquery`; `TARGET`
threaded through `orchestration/tasks.py`; the conflicting-duplicate guard as a
singular dbt test both dialects run; `eval/golden.py`'s one renderer for both
engines. **Live (2026-08-30, `ontime-rate-recovery`, as the SA):** the SA-id
detour (undelete + `terraform import`), `Apply complete! 18 added` with
`operator_principal = user:<operator>`, then `dbt-build OK:
tiny/bigquery` (PASS=126 — the DuckDB count) and `make test-int-bigquery` →
`3 passed`: the three goldens byte-identical off BigQuery, pins hold, exactly
two datasets. Re-proven at round-2 HEAD (`d204513`): `PASS=126`, `4 passed` (a
planted conflict fails on BigQuery too); re-proven again at round-3 HEAD
(`5878cf5`) after the cap's Amendment X: an empty-selection landing through
the ONE mechanism (a zero-byte `_empty.jsonl` load job → `0 event rows`),
`PASS=126`, `4 passed`. Two live surprises, both §8: dbt-bigquery admits no custom
incremental strategy → **Amendment U** (native `insert_overwrite` selected on
`target.type`; the dispatch body raises by design), and unit fixtures had
DuckDB-only forms (`::json`, `date_diff`) → portable fixtures. Offline suite
green (432), lint clean, mutate 8/8; review rounds 1–4 applied (amendments
V, W/W′ → X; round 4 was the cap's one scoped re-review, re-proven live:
`test-int-bigquery` `4 passed` at `374ab4e`). **Phase 9's Done-when is
met.** Round 3 invoked the **cap** (two rounds of findings inside the
previous round's fixes — the landing's empty-selection path): Amendment X
re-implemented it once as ONE mechanism (the load job; a zero-byte object for
an empty selection; `recreate` gone). The coherence-auditor's whole-repo exit
pass ran (10 findings: records, one BACKLOG row for Phase 10's write-back
seam). **9b merged as PR #13** (`8c1c389`, 2026-08-30). `fix/tf-vars-argv`
(this branch, reviewed by the three agents — 11 findings, all applied):
toggles only as a command-line `VARS='name=value,…'` → argv `-var`
(`$(origin VARS)`); any `TF_VAR_*`/`TF_CLI_ARGS*` refuses every `tf-*`; the
terraform child runs under an env allowlist (no keyfile env, no
`TF_WORKSPACE`); two §8 gotchas (the impersonated-SA ADC cannot run
Terraform — the first post-9b `tf-destroy` failed at refresh, no resource or
state changed; env `TF_VAR_*`/`TF_CLI_ARGS*`). The post-9b `tf-destroy`
(2026-08-30) emptied the stack; Phase 10's apply the same day re-created the
free-tier layer (SA undeleted + imported, so it is live and in state — no
detour until the next full `tf-destroy`). **Current stack: see the Phase 10
paragraph below.**
**Phase 10 in flight** (`phase-10-spanner-writeback`, spec approved
2026-08-30, reconciliation items 1–6): offline complete — the TARGET-keyed
read seam (`candidates_sql` relation override; `TARGET=spanner` reads
BigQuery `ontime`, writes Spanner through injectable clients, fakes offline),
`version_key` numeric order (BACKLOG row 32 struck; contract wording
unchanged), `loader/spanner.py` dims landing + `make spanner-load`, the
spanner terraform module body (instance 100 PU, database DDL, EXTERNAL_QUERY
connection + `raw.dim_user_spanner` view, two scoped grants; count-gated,
default plan still creates nothing), the generated `dim_user_identifier`
source swap, `make test-int-spanner`, threat-model sweep. **Review round 1
applied (2026-08-30, 24 findings):** Amendments A (the Spanner guard + upsert
in ONE read-write transaction), B (the version parses before the insert
shortcut — the BLOCKER), C (the build's one validated var seam +
manifest-proven swap); fakes that execute the SQL on DuckDB; grant scope /
gated-module allowlists / name literals / `region` validation pinned;
`disable_builtin_metrics`; the view casts; records; suite 474, mutate 5/5,
review-gate 6/6, tf-validate/tf-freeze clean. **Live 2026-08-30
(`ontime-rate-recovery`):** SA undelete + `terraform import` detour, toggled
plan `27 to add`, first apply 26/27 → **Amendment D** (no service-agent
grant: a Spanner federated query runs as the querying principal; module = 8
resources, two grants both to the SA), re-plan `No changes`; as the SA:
`spanner-load OK: tiny — 22 dim rows`, `make test-int-spanner` **`4 passed`**
(view ≡ seed, manifest resolved the source to the view, three goldens
byte-identical, write-back idempotent with the DuckDB hash), `writeback OK:
ontime-rate-recovery.ontime → spanner, 20 users, 0 written`. Torn down the
same session (operator ADC, `8 destroyed`, `Listed 0 items.`) — dated lines
in DEPLOYMENT.
**Nothing billable is up; the free-tier layer (two datasets, bucket, SA +
grants, budget — cents/month) IS up** — `make tf-destroy … CONFIRM=yes`
when the phase is done with it. **Phase 10's Done-when is met.** **Review
round 2 applied (2026-08-30, 21 findings):** Amendments E (custom
data-plane role — `databaseUser` carries `updateDdl`), F (`tf-apply` plans
first, applies the saved plan, refuses destroys without `ALLOW_DESTROY=yes`),
G (a credential in the env refuses every cloud command, one policy), H (the
DuckDB write-back is one transaction; single-writer pinned), I (the read
maps by name), J (the landing refuses instead of coercing); `region`
validated in every module; the metrics pin over the tracked tree;
`carried_gate` through the one `confirmed` predicate; records de-contradicted.
**E and F verified live 2026-08-31:** an apply omitting the applied
`operator_principal` was refused (`tf-apply: refused — the plan destroys
module.iam…operator_token_creator[0]`, exit 2, nothing changed); the
re-apply with both toggles `9 added`, re-plan `No changes`, the live role =
the module's 11 permissions; as the SA under it `spanner-load OK … 22 dim
rows`, `test-int-spanner` **`4 passed in 239.42s`**, `writeback OK … 0
written`; the `ALLOW_DESTROY=yes` toggle-flip `9 destroyed` (02:48 UTC),
`Listed 0 items.`, state 21, default plan `No changes` (the custom role's
undelete window runs to 2026-09-07). Nothing billable is up. **Review
round 3 applied (2026-08-31, 15 findings):** Amendments K (`planned_deletes` <!-- historical -->
fails CLOSED — an unreadable `show -json` refuses, never "no deletes"), L
(the keyfile-env policy covers the google-auth family: `*KEYFILE*`,
`CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE`), M (records: the instance is
`PROVISIONED` and bills from creation — there was never a trial clock);
the stored-pair read maps by name (missed in round 2); ONE `confirmed`
predicate in `infra.cli`; the explicit rollback pinned on an open
connection; the unversioned-tfstate BACKLOG row (accepted, dated trigger);
records. **Review round 4 (2026-08-31, 28 findings) invoked the cap** —
round 4's correctness findings sat inside round 3's fixes as round 3's sat
inside round 2's: each fix was a longer denylist at an open-world boundary.
Three boundaries re-implemented ONCE, denylist → allowlist / real type:
**Amendments N1** (the plan-first apply is an action allowlist —
`SAFE_ACTIONS`, `delete` only with `ALLOW_DESTROY`, any other verb or an
unparseable entry refuses always; supersedes K), **N2** (the Google env
namespace `GOOGLE_*`/`GCLOUD_*`/`CLOUDSDK_*` is allowlisted —
`CLOUD_ENV_ALLOW`, every other name refuses every cloud command;
`KEYFILE_ENV_RE` deleted; conftest scrubs with the gate's own function; <!-- historical -->
supersedes G/L), **N3** (the Spanner read is the library's `to_dict_list`,
tested offline on real `StreamedResultSet`s through the real adapter;
`existing_of` refuses non-str; one DuckDB-side `rows_by_name`); M's four
`infra/*.tf` survivors reworded + re-frozen, PROJECT_BRIEF annotated,
records. **N3 re-proven live 2026-08-31 06:07–06:42 UTC** (third session):
toggled apply `9 added` (the custom role re-created with NO undelete
detour — the provider undeletes on create), re-plan `No changes`; as the
SA `spanner-load OK … 22 dim rows`, `test-int-spanner` **`4 passed in
248.70s`**, `writeback OK … 20 users, 0 written`; the first teardown
attempt (06:38) failed at refresh — the ADC browser login had picked the
git-only account (403 `serviceusage`, nothing changed; §8, DEPLOYMENT
step 5) — re-login as the operator, toggle-flip `9 destroyed` (06:42),
`Listed 0 items.`, state 21, re-plan `No changes`; ~35 min up ≈ 5¢.
**Nothing billable is up.** The tfstate BACKLOG row re-deferred (trigger:
the first apply NOT torn down in the same session). Process rules from
the round recorded (Engineering contracts: Boundary / Credential /
Adapter; Workflow: Fix the class, Fix commits; DONE item 8; the agents).
NEXT: round 5 scoped to `review-round-4..HEAD`, the coherence-auditor exit
pass, then the PR.
Open BACKLOG rows: **13** (Phase 10 struck: the write-back read-seam row and
the `model_version`-lexical row; re-deferred: the `computed_as_of`
discriminator (new trigger: a served-row change without an advancing as-of /
a dim change mid-schedule / two live versions), the `loader/`→`landing/`
rename (trigger: `fix/landing-package` after Phase 10 merges, before
Phase 11); the Spanner row retitled 2026-08-31 — `PROVISIONED`, bills
from creation, no trial clock (Amendment M; trigger: every phase exit,
`Listed 0 items.`); opened: the local unversioned tfstate row (round 3 #6,
trigger: before the next `enable_spanner=true` apply).
Earlier: `fix/tf-vars-argv` struck the env-`TF_VAR_*` row; 9b struck: the two-datasets row, the DAG-landing
row, the conflicting-duplicate guard, the dialect denylist, the SA-id row
(first 9b apply 2026-08-30); opened: the guard's contract residual (JSON
null vs missing key, `|` in a value), the
project-id/SA-email-in-records note (round 2), the write-back's DuckDB-only
relation/connection seam for Phase 10 (exit audit), the `loader/` package
shape (exit questions); the
CI-drift row re-deferred with the trigger "the first `enable_ci_wif = true`
apply"; THROUGH-calendar, Spanner, argmax-bins re-deferred with 9b notes).

(Update this section at the end of every working day.)
