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

AIRFLOW orders: load → dbt build → eval → write-back    TERRAFORM: BigQuery · GCS · Spanner · Composer · IAM · budgets
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
  `ontime_retention`; every metric defined once in `docs/METRICS.md`),
  `models/features` (Phase 5: `features_user_hour` — organic `app_opened`
  local-hour histogram per user), `models/scores` (Phase 5:
  `scores_send_time` — the send-time model, cohort band + circular
  shrinkage; `docs/METRICS.md` § scores_send_time); `macros/` (the five dispatch
  macros), `tests/` (singular data tests), `profiles.yml` (`duckdb`,
  `bigquery` targets).
  `models/staging/sources.yml` is GENERATED (`make gen-sources`), never edited.
- `loader/` *(Phase 2)* — raw landing: `load.py` (fixtures → DuckDB `raw`
  schema, types from the generated `ddl.sql`), `cli.py` (`load`, `dbt-build`,
  `drop-db`). Pipeline code — guarded by `test_truth_isolation.py`.
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
- `serving/` *(Phase 8; Spanner target Phase 10)* — write-back to
  `send_schedule`.
- `orchestration/` *(Phase 8)* — the Airflow DAG. No logic, only ordering.
- `infra/` *(Phase 9+)* — Terraform; `modules/composer`, `modules/spanner`
  behind `enable_*` toggles.
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
  (`make gen-sources`: raw DDL + `sources.yml` from `generator/models.py`).
- `docs/` — ARCHITECTURE.md (spec), PHASES.md (plan), METRICS.md (Phase 4:
  the single definition of every served metric — grain, numerator,
  denominator, null policy, pinning test), RESULTS.md (Phase 6: the
  counterfactual simulation — one generated block per profile, tiny and
  medium), AB_DESIGN.md (Phase 6: the production experiment; its power
  table is a generated block); later DEPLOYMENT.md (all under `docs/`).
- `DECISIONS.md` — why-not-X log. One entry per non-obvious choice.
- `BACKLOG.md` — deferred findings with revisit triggers. Reviewed at every
  phase exit: do due items or re-defer with a new trigger, never drop.
- `data/` — gitignored working output (`data/out/<profile>/`, `data/truth/`,
  `*.duckdb`).

## Commands (macOS, uv)

- `make setup` — `uv sync`, `pre-commit install`
- `make test` — pytest, no services, no network; `tests/integration/` is
  skipped unless `OTR_INT=1`, which only the `test-int-*` targets export
- `make lint` — ruff via pre-commit (rewrites files; never run inside a gate)
- `make check-docs` — `scripts/check_docs.py`: every relative link/anchor in
  CLAUDE.md, README, docs/, PROJECT_BRIEF, DECISIONS, BACKLOG resolves; every
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
- `make dbt-build PROFILE=<p> [TARGET=duckdb] [CONFIRM=yes] [FULL=yes]` — `load`,
  then `dbt build` (source tests → `stg_events`, `stg_prompts`, `attribution` →
  data, unit and singular tests) against `data/<p>.duckdb`; prints `dbt-build OK:
  <p>/<target>`, exit 1 on any failure. The three event-level models are
  incremental (Phase 7): a first run is a full build, a later run reprocesses
  only partitions inside the `lookback_days` window of the data-derived horizon
  (`max(server_upload_time)`) via the `partition_overwrite` strategy.
  `FULL=yes` (command-line origin, `$(origin)`-gated) passes `--full-refresh`
  (rebuild from scratch). Any `TARGET` other than `duckdb` is a cloud-cost
  command: refused unless `CONFIRM=yes` has command-line origin. dbt telemetry
  is off (`flags.send_anonymous_usage_stats`, `DO_NOT_TRACK`)
- `make drop-db PROFILE=<p> CONFIRM=yes` — deletes `data/<p>.duckdb` and its
  `.wal` (nothing else); `CONFIRM=yes` must have command-line origin
- `make gen-sources` — re-renders `loader/ddl.sql` and
  `dbt/models/staging/sources.yml` from `generator/models.py`;
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
- Later phases add:
  `writeback`, `pipeline`, `test-int-airflow` (8), `tf-plan | tf-apply |
  tf-destroy`, `test-int-bigquery` (9). Each lands with its phase and is
  listed here in the same PR.

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
  `attribution`). Vars `skew_max_min` (5 = `generator/models.py`),
  `delivery_grace_min` (10), `unattributed_max` (0.10), `retention_days` (28),
  the send-time model's `feature_window_days` (30), `max_user_shift_min`
  (120), `shrinkage_pseudo_count` (5), `model_version` (`v1`), and the
  incremental `lookback_days` (5 — Phase 7; `lookback_days·24 >
  late_arrival_max_hours` on every profile) in `dbt_project.yml`.
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
  directory (`dbt/`, `serving/`, `orchestration/`) for the word; in
  `generator/` only `truth.py` (the writer), `models.py` (record types) and
  `cli.py` (the entry point that calls the writer) may name it — generation
  logic never does.
- Model scoring and simulation are seeded; the generated blocks of
  `docs/RESULTS.md` and `docs/AB_DESIGN.md` regenerate byte-identically
  (`make simulate` / `make power` check mode is the CI proof). The
  simulation uses common random numbers (four uniforms per prompt, one
  seeded stream, `prompt_id` order), so the lift is a function of the
  schedules alone.
- Non-deterministic by nature and carved out: dbt run ids and timings,
  Airflow run ids, Terraform apply output, BigQuery job ids. Nothing asserted
  reads them.

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
- Dialect contract: exactly five dispatch macros (JSON extract,
  `timestamp_diff`, `safe_divide`, `to_local_time`, partition overwrite; the
  fifth was added in Phase 2 with a DECISIONS entry). Each has a DuckDB body
  and a BigQuery body that raises until Phase 9 — never a `default__`. A
  sixth needs a DECISIONS entry.
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
  pydantic (Phase 1); duckdb, dbt-core, dbt-duckdb (Phase 2); dbt-bigquery (Phase 9);
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
- Review cap: if two consecutive review rounds report correctness findings
  only in the previous round's fixes, stop patching. Write the invariant,
  re-implement against it ONCE, one scoped re-review. A human applies this by
  comparing round N's table to round N−1's; `/review-round` prints the
  reminder, never a verdict.
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
| Sensitive: `.github/`, `infra/`, `serving/`, `.env*`, `dbt/profiles.yml`, `.claude/hooks/`, `.claude/settings*.json`, any target that deletes / applies / takes `CONFIRM` | + security-reviewer |
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

**Phase 7 implemented, in review (`phase-7-incremental`).** Phases 0–6 merged
(PRs #1–#8, round-tag fix #3). `stg_events`/`stg_prompts`/`attribution` are
incremental via the `partition_overwrite` custom strategy (delete-and-insert
seam completed, BACKLOG row closed); horizon = data-derived
`max(server_upload_time)`, reprocess window `<= lookback_days` (5), `final` at
`>= lookback_days`; `attribution` gains `status` (`provisional`/`final`) out of
the golden. `make load … [THROUGH=<date>]` lands a file subset, `make dbt-build
… [FULL=yes]` rebuilds. Two landings converge to one (all three table hashes +
frozen `attribution.csv`), landing 2 twice is a no-op, no `final` label changes,
the straddling duplicate `e-0000259` dedupes; every Phase 3–6 gate byte-
identical (report 0.609756, eval MAE 0.816201/0.352354, simulate +0.162371,
power 6 rows). `make mutate` 5/5. tiny 80 final / 60 provisional; `fixtures/tiny/`
untouched (no re-freeze). Next: review rounds, then Phase 8 (orchestration).
Open BACKLOG rows: **9**.

(Update this section at the end of every working day.)
