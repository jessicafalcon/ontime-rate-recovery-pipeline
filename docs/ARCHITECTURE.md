# ARCHITECTURE.md — On-Time Rate Recovery Pipeline

The spec. Describes the system **as it will be when done** — parts not yet
built are marked *(Phase N)*. When a phase spec seems wrong, this is what you
point at; when this is wrong, STOP and say so (CLAUDE.md → Workflow rules).
`docs/PHASES.md` is the plan; `PROJECT_BRIEF.md` is the origin story and the
architecture-review log; `DECISIONS.md` is the why-not-X.

## 1. What this is

A batch data pipeline that (a) attributes every late or missed daily-prompt
response to its true cause — **upload fault, delivery fault, timing gap** — and
(b) turns the timing-gap share into a **send-time recommendation** per user
cohort, validated by counterfactual simulation against generator ground truth.
It runs end-to-end on a laptop (DuckDB) with no cloud account, and deploys with
a profile switch to GCP (BigQuery, Airflow/Composer, Spanner, Terraform).

Deterministic throughout: a seeded generator, SQL transformations with no
clock on the data path, a model that is itself a dbt model. "Did it work" is a
diff against frozen fixtures, not a judgment.

## 2. Data model

### 2.1 Event contract (raw, Amplitude export shape) *(Phase 1)*

Pydantic models in `generator/models.py` are the **schema source of truth**; the
generator emits exactly the Amplitude raw-export shape so the local stub and the
production export are interchangeable at the dbt `source` level.

Common envelope (every event):

| column | type | note |
|---|---|---|
| `insert_id` | str | dedupe key — the export can carry duplicates |
| `event_type` | enum | see 2.2 |
| `user_id` | str | counter id `u-000123`, never a UUID |
| `device_id` | str | counter id |
| `client_event_time` | timestamp UTC | the device clock when the event happened |
| `server_received_time` | timestamp UTC | when the server first saw it |
| `server_upload_time` | timestamp UTC | when the export batch landed |
| `event_properties` | json | per-event-type payload (2.2) |

**The three clocks are the reliability signal.** `server_received_time −
client_event_time` is the upload delay; a response whose client time is inside
the window but whose received time is outside it is an *upload fault*, no
heuristic needed. Client clock skew beyond ±`SKEW_MAX_MIN` (spec-pinned) is
`unattributed`, never guessed.

### 2.2 Event types

| `event_type` | emitted by | key properties |
|---|---|---|
| `prompt_sent` | notification service | `prompt_id`, `cohort_id`, `window_minutes` |
| `prompt_delivered` | push provider receipt | `prompt_id` |
| `prompt_opened` | client | `prompt_id` |
| `capture_started` | client | `prompt_id` |
| `upload_started` / `upload_failed` / `upload_completed` | client | `prompt_id`, `attempt`, `error_code` |
| `response_recorded` | backend | `prompt_id`, `response_id` |
| `app_opened` | client | — (organic; the reachability signal) |

A `prompt_sent` with no `prompt_delivered` inside `DELIVERY_GRACE_MIN` is a
*delivery fault*. Delivery receipts are the one signal that separates "never
arrived" from "arrived at a bad time".

### 2.3 Dimensions (Spanner, SCD2) *(seed file locally; Phase 1 / Phase 10)*

`dim_user`: `user_id`, `tz` (IANA), `cohort_id`, `signup_date`, `valid_from`,
`valid_to` (empty = the open row). **All reachability math is in user-local
time**; the conversion happens once, in staging
(`stg_events.client_event_time_local`), against the tz valid at
`client_event_time` — `valid_from <= t and (valid_to is null or t < valid_to)`.

### 2.4 Ground truth (generator side-file, never a source)

Two files. `truth/users.jsonl`: per user the latent
`reachable_center_local_hour` and `reachable_width_hours` (+ `cohort_id`).
`truth/prompts.jsonl`: per prompt the generator's assigned cause (one of the
five labels) and `local_send_hour`. Lives under `fixtures/<profile>/truth/`
and `data/out/<profile>/truth/`; **no dbt source may reference it**
(`tests/test_truth_isolation.py`). Only `eval/` reads it; inside `generator/`
only `truth.py` (the writer), `models.py` (the record types) and `cli.py` (the
entry point that calls the writer) may name it — generation logic never does.
The generator is cause-first: the cause is drawn, then the events it implies
are emitted, so truth is exact by construction (DECISIONS Phase 1).

### 2.5 Attribution label set (exhaustive, exclusive) *(Phase 3)*

```
on_time | upload_fault | delivery_fault | timing_gap | unattributed
```

One label per `prompt_id × user_id` (= per `prompt_id`; a prompt has one
user). The window is `[sent_at, sent_at + window_minutes)`. Precedence when
evidence overlaps (a delivery fault cannot also be a timing gap — the prompt
never arrived); the built label is the FIRST matching rule, one `case` arm
each, one dbt unit test per arm and per adjacent pair (Phase 3):

1. `delivery_fault` — no `prompt_delivered` within `DELIVERY_GRACE_MIN`.
   The receipt is server-stamped, so a skewed device clock cannot forge it.
2. **Skew gate** *(Phase 3)* — `unattributed` when any event of the prompt
   has a client clock AHEAD of the server past the bound
   (`min(server_received_time − client_event_time) < −SKEW_MAX_MIN · 60`,
   the delay in seconds, the bound in minutes). Sits
   before the clock rules because they read the clock it distrusts; a
   backward skew is indistinguishable from an upload delay (§8), so a
   positive delay of any size is never skew.
3. `on_time` — `response_recorded` with `client_event_time` inside the window
   AND `server_received_time` inside the window.
4. `upload_fault` — a `response_recorded` exists but the device-side capture
   (`capture_started` / `upload_*`; `response_recorded` itself is
   backend-stamped, its two clocks equal) has client time inside the window
   and received time outside; or an `upload_failed` chain with no
   `response_recorded`.
5. `timing_gap` — delivered inside `DELIVERY_GRACE_MIN`, no `capture_started`
   and no `response_recorded` inside the window, no `upload_*` chain. Evidence
   is delivery + no-action ALONE; whether the window was outside the user's
   reachable hours is Phase 5's question (2.8), never an attribution input
   (DECISIONS Phase 1, "timing_gap is delivery + no-action evidence alone").
6. `unattributed` — everything else (contradictory evidence, e.g.
   `capture_started` without any upload or response event). Its share is
   bounded by a dbt test (`UNATTRIBUTED_MAX`, spec-pinned); the bound is what
   makes the metric honest.

Labels are `provisional` until the reprocessing lookback closes (2.7), then
`final`. A final label never changes.

### 2.6 Marts *(Phase 4)*

Every metric is defined once in `METRICS.md` (grain, numerator, denominator,
null policy, pinning test); the marts' `schema.yml` links there.

- `ontime_rate_daily`: per `cohort_id × prompt_date` — `prompts_sent`,
  `prompts_delivered` (the denominator = prompts with `delivered_in_grace`;
  **never user-days**, or delivery faults vanish; never `prompts_sent`), one
  count per label, `ontime_rate = safe_divide(on_time, prompts_delivered)`.
  `prompt_date` is the **local** date of `sent_at` — cohorts are defined by
  the local send hour, and a Tokyo 08:00 prompt is the previous UTC day (§8).
  Partition: the four delivered labels sum to `prompts_delivered`;
  `delivery_fault` is counted beside it and completes `prompts_sent` (a dbt
  test). Null policy: the rate is NULL only when nothing was delivered; a day
  with delivered prompts and none on time is 0.
- `ontime_retention`: per user — on-time rate over the `RETENTION_DAYS`
  (28) window from the user's first prompt, and `retained`: an organic
  `app_opened` on or after the window closes. Three states: true / false /
  **NULL while the data horizon (`max` local event time, never the clock)
  has not reached the close** — an unobservable user is never reported as
  churned; on tiny (7 days) every row is NULL. Descriptive only; the
  retention gap in synthetic data is a designed property, not a finding
  (PROJECT_BRIEF §5, §7).

### 2.7 Incrementality and late arrival *(Phase 7)*

Upload-fault events arrive hours or days after the prompt — the events the
project is about. Every event-level model is incremental with a **reprocessing
lookback window** of `LOOKBACK_DAYS` (spec-pinned, 3–7; 5 in Phase 7) partitioned
by the local event date: `prompt_date` (the local send date) for `stg_prompts`
and `attribution`, and `event_date` (the local `client_event_time` date) for
`stg_events`, whose `app_opened` rows carry no `prompt_id`. BigQuery
`insert_overwrite`, DuckDB delete-and-insert per partition, both behind one
macro. The horizon is data-derived (`max(server_upload_time)`), never the clock;
`final` once a partition is ≥ `LOOKBACK_DAYS` behind it, and
`LOOKBACK_DAYS · 24 > late_arrival_max_hours` keeps a late event off a closed
partition. Running the lookback twice over the same raw converges (idempotent).
Loads are driven by the partitions a landing touched, never by the wall clock.

### 2.8 Features and scores *(Phase 5)*

- `features_user_hour`: per user, local-hour histogram of **organic
  `app_opened`** events over `FEATURE_WINDOW_DAYS` (the window ends at the
  data horizon, `max(client_event_time)` over all staged events). Prompt
  responses are not inputs (exposure bias: you only observe response at the
  hour you prompted). The unit is `user_id`, each open on its own local hour
  (`client_event_time_local`): a user whose tz changes mid-window keeps one
  histogram on one clock — the latent centre is per user, in local hours
  (DECISIONS Phase 5). Sparse: no row for an empty bin.
- `scores_send_time`: per `cohort_id`, the send window maximizing P(open within
  `window_minutes`) — the hour `h` whose `[h, h + window_minutes)` holds the
  most pooled opens, `h` over the cohort's opened bins, ties to the smaller
  opened hour (BACKLOG: a wider window may prefer an empty earlier start);
  per user, a bounded shift
  within `MAX_USER_SHIFT_MIN` of the cohort moment. Bayesian shrinkage toward
  the cohort prior for sparse users: hours are angles at bin centres, the
  prior is the cohort's pooled resultant vector weighted as
  `SHRINKAGE_PSEUDO_COUNT` opens, the centre is the combined direction and
  `confidence` its mean resultant length (`[0, 1]`; a zero-open user gets the
  prior's exactly). Circular hour arithmetic (23:00 and 01:00 are 2 h apart)
  in plain ANSI `floor`/`atan2`, plus integer `mod` on hour bins and on the minute-of-day — not a
  dispatch macro (nothing diverges).
  Ties broken by explicit key order, never by insertion order.
- Columns: `user_id`, `cohort_id`, `send_hour_local`, `send_minute_local`,
  `cohort_hour_local` (the band's anchor), `center_hour_local` (the unclamped
  posterior centre — the column `eval` scores MAE against; never served),
  `confidence`, `model_version`, `computed_as_of` (= max `client_event_time`
  of the opens in the feature window — data-derived, never `now()`). Every
  column is defined once in `METRICS.md` § scores_send_time. **The table is
  the served schedule, not an eval scratchpad**: `center_hour_local` and
  `cohort_hour_local` are the two diagnostic columns (Phase 5, DECISIONS);
  any further one is a design change with its own entry, and the write-back
  carries only §2.9's columns. Downstream readers (Phase 6's simulation,
  Phase 8's write-back) consume the clamped `send_hour_local` /
  `send_minute_local`, never the unclamped centre.

**The model is a dbt model.** Versioned, unit-tested, runs on both warehouses.
Python is reserved for `eval/`.

### 2.9 Serving table *(DuckDB stand-in Phase 8; Spanner Phase 10)*

`send_schedule(user_id PK, cohort_id, send_hour_local, send_minute_local, tz,
confidence, model_version, computed_as_of, written_at)`. Written by an
idempotent batch upsert keyed `user_id`; a row is replaced only when
`(model_version, computed_as_of)` is strictly greater. The notification service
falls back to the cohort default when no row exists. `tz` is the current (open
SCD2) `dim_user` zone, joined at write-back time from the `dim_user_current`
model (Phase 8a), not carried on the score. `written_at = computed_as_of` on the
DuckDB stand-in (Phase 8a): a per-row data-derived value keeps `send_schedule`
byte-identical on a re-run and under a backfill — a wall clock would break both
(§4, the determinism policy); a production serving store may stamp a real ingest
time in a carved-out audit column, never asserted.

## 3. Components

```
GENERATOR (seeded)  ── truth side-file (never a source) ── dim_user seed file (tz SCD2)
   │ raw events, Amplitude export shape (three clocks, insert_id)
   ▼
RAW LANDING   fixtures/<profile>/{raw/*.jsonl, dims/dim_user.csv}  →  DuckDB `raw` schema (local, `make load`) | BigQuery (prod, Amplitude export)
   ▼
dbt  staging      dedupe on insert_id · tz → local time · typed columns
     attribution  exhaustive label per prompt×user · provisional→final over LOOKBACK_DAYS
     marts        ontime_rate_daily (denominator = prompts_delivered) · ontime_retention
     features     organic app_opened local-hour histograms
     scores       send-time model (cohort-constrained, shrinkage, circular hours)
   │ dbt build = tests gate downstream models by dependency
   ▼
EVAL (Python, reads truth)   label accuracy · reachable-center MAE · counterfactual simulation
   ▼
WRITE-BACK (Python)   idempotent upsert → Spanner send_schedule (local: a DuckDB table stands in)

AIRFLOW  dbt build (THROUGH=data_interval_end) → write-back, data-interval-aware; backfill on demand (catchup=False — no auto-catchup-to-now)
         (eval is a union-only validation gate in make pipeline / CI — it reads truth and writes no table, Phase 8b)
         (local Docker | Cloud Composer, isolated Terraform module, applied once)
TERRAFORM  BigQuery datasets · GCS · Spanner (toggle) · Composer (toggle) · IAM · budget alerts
```

### 3.1 Boundaries (who may do what)

| component | reads | writes | may NOT |
|---|---|---|---|
| generator | profile, seed | raw events, truth, dim seed | read anything else |
| loader | `fixtures/<p>/{raw,dims}` (or `data/out/<p>/`, marked; a `THROUGH` upload date lands a file subset — a landing is a raw-table state, §2.7) | `raw.events`, `raw.dim_user` (recreated each load) | read any other byte of the fixture; name or read `truth/`; dedupe (staging's job) |
| dbt | raw, dims | staging → scores | reference `truth/`; call `now()` on a data path |
| eval | dbt outputs, truth, the profile JSON (the generator's input) | console, `data/out/<p>/expected/` (the golden, frozen only by `make freeze`), the marker-confined blocks of `docs/RESULTS.md` and `docs/AB_DESIGN.md` *(Phase 6; `WRITE=yes` only)* | write any table the pipeline reads; write under `fixtures/`; create or append to a doc |
| write-back | `scores_send_time`, `dim_user_current` (the open `dim_user` row's tz — Phase 8a) | `send_schedule` | read truth; read raw; re-derive a score |
| Airflow | — | — | contain logic (it orders `make` targets / dbt commands) |

### 3.2 Local ↔ GCP profile switch

One dbt project, two `profiles.yml` targets (`duckdb`, `bigquery`). Dialect
divergences behind exactly five dispatch macros: JSON extraction,
`timestamp_diff`, `safe_divide`, `to_local_time` (UTC → local wall time; added
in Phase 2, DECISIONS), partition overwrite. Each has a DuckDB body and a
BigQuery body that raises until Phase 9 — no `default__` an unknown adapter
could fall into. CI runs `dbt build` on DuckDB per PR; BigQuery runs are manual
(`make dbt-build PROFILE=<p> TARGET=bigquery`). `PROFILE` names the data
profile, `TARGET` the warehouse.

### 3.3 What is stubbed (and the production swap)

| stub | replaces | swap |
|---|---|---|
| generator → `fixtures/<profile>/raw/events_<upload-date>.jsonl` (one file per UTC `server_upload_time` date — the landing unit Phase 7 replays) | Amplitude → BigQuery export | dbt `source` config |
| `dim_user` seed file (`dims/dim_user.csv`, loaded as the `raw.dim_user` source by `make load` — not a dbt seed, whose path could not follow `PROFILE`) | Spanner change streams / federation | `EXTERNAL_QUERY` source (demo), Dataflow template (prod) — a source-config swap, no model changes |
| DuckDB `send_schedule` table | Spanner serving table | write-back target flag |

## 4. System invariants (hold across every phase)

1. For all (seed, profile), two generator runs are byte-identical.
2. For all dbt models, output row content is a function of raw + dims + vars —
   never the clock, never iteration order.
3. For all prompt×user, exactly one attribution label; a `final` label never
   changes on re-run or backfill.
4. For all users, no model input derives from `truth/`.
5. For all write-back runs, re-running over the same scores is a no-op.
6. For all metrics, the on-time denominator is `prompts_delivered`.

Each phase spec restates the subset it touches with the scenario test that
falsifies it (`specs/TEMPLATE.md` → Invariants).

## 5. Non-goals (v1)

Real-time scoring; per-user send times outside the cohort band; a served ML
model; reading real Amplitude data; multi-app tenancy. Out-of-scope items get a
BACKLOG row, not code.

## 6. Deployment posture

Region `us-central1`. Free/near-free layer safe to leave up: BigQuery, GCS,
IAM, budget alerts. Spanner: 90-day trial, `enable_spanner` toggle, teardown
date in `docs/DEPLOYMENT.md`. Composer: `enable_composer` toggle, applied once on
demo day, destroyed the same hour. Budget alerts do not stop spend — stated,
with the optional billing-disable function as the real guardrail. Terraform
state in GCS (bootstrapped manually); WIF for CI, never JSON keys.

Implemented in Phase 9a (`infra/`, behind `enable_*` toggles that default false;
`project_id` the only required var; one least-privilege service account, with
the CI WIF pool/provider opt-in behind `enable_ci_wif` so a default apply builds
no cross-repo trust).
`docs/DEPLOYMENT.md` is the runbook — auth (ADC/WIF), the one-time state-backend
bootstrap, the cost table, the optional billing kill-switch, and the teardown
that leaves nothing billable.

## 7. Validation stance

Synthetic data cannot prove retention lift. The pipeline reports (a) label
accuracy vs truth, (b) reachable-center MAE, (c) simulated on-time rate under
the recommended schedule — a **counterfactual simulation**, not an A/B — and
ships the production A/B design (user-level randomization, persistent holdout,
power calculation, pre-registered primary metric, guardrails, send-time jitter).

## 8. Gotchas (stack surprises found live)

- **make: `unexport VAR` counts as a file definition** (Phase 1). With
  `unexport CONFIRM` in the Makefile, an unset `CONFIRM` has
  `$(origin CONFIRM)` = `file`, not `undefined`. Harmless — only `command line`
  is accepted — but a test expecting `undefined` was wrong; pinned in
  `tests/test_makefile.py::test_freeze_requires_confirm_from_the_command_line`.
- **Clock skew is only observable in one direction** (Phase 1). A client clock
  *behind* the server looks exactly like a slow upload (`received − client`
  large and positive); only a clock *ahead* (negative delay past
  `SKEW_MAX_MIN`) is distinguishable. The generator's skew injector is
  forward-only; Phase 3 pinned the rule on the negative side (§2.5 rule 2).
- **`response_recorded` carries no device clock** (Phase 3). It is a backend
  event: `client_event_time = server_received_time` by construction, so a
  literal "response with client time inside, received outside" never
  matches; the three-clock signal is on `capture_started` / `upload_*`, and
  the skew injector never touches the response either — a skewed prompt's
  response looks perfectly on time, which is why the skew gate precedes the
  clock rules (§2.5).
- **DuckDB `timezone(tz, ts)` converts in the wrong direction for a UTC
  column** (Phase 2). On a naive `timestamp` it interprets the value as local
  wall time in `tz` and returns a `timestamptz` that the client renders in the
  session `TimeZone` (the host's zone by default — a non-UTC machine shows a
  different hour). The session-independent UTC → local form is
  `timezone(tz, timezone('UTC', ts))::timestamp`, verified identical under
  UTC / America/Mexico_City / Asia/Tokyo sessions; it is the DuckDB body of
  `to_local_time`, and the loader and profile also pin `TimeZone = 'UTC'`.
- **dbt unit tests: `unit_tests:` is a top-level YAML key, and a `format: sql`
  `expect` must list every model column** (Phase 2). Nested under `models:`
  they parse silently and never run (the first green build reported "33 data
  tests" and 0 unit tests). A dict-format `expect` compares only the columns
  it names; `format: sql` inputs are still the way to type a `json` column.
  `accepted_values` takes `arguments: {values: […]}` in dbt-core 1.12
  (deprecation warning otherwise).
- **dbt phones home by default** (Phase 2). `send_anonymous_usage_stats`
  defaults to true: every `dbt build` — including the in-process one under
  `make test` — POSTs to a vendor endpoint and writes `dbt/.user.yml`. Off in
  `dbt_project.yml` (`flags:`) and via `DO_NOT_TRACK=1` before dbt imports;
  pinned by `tests/test_dbt_conventions.py::test_telemetry_is_off`.
- **`dbt/target/` compiles `schema.yml` into a directory named `schema.yml`**
  (Phase 2). A grep that treats every `.yml` path as a file raises
  `IsADirectoryError`; `test_truth_isolation.py` now checks `is_file()`.
- **A model turning `table` → `incremental` (or gaining a column) needs a
  full-refresh against a pre-existing DuckDB file** (Phase 7). The first Phase 7
  `dbt build` on a db left by an earlier phase runs `is_incremental()` = true
  against the old-schema table and fails — `Binder Error: Referenced column
  "event_date" not found`. A fresh checkout / CI is unaffected (no db yet, so
  the first run is a full build); the remedy on an existing db is `make dbt-build
  … FULL=yes` (or `make drop-db … CONFIRM=yes`) once. dbt unit tests on an
  incremental model must also pin `overrides: {macros: {is_incremental: false}}`
  (dbt-core 1.12 refuses to parse them otherwise).
- **A `%` in a Jinja `{% … %}` block trips a naive SQL-modulo denylist**
  (Phase 7). `tests/test_dbt_conventions.py`'s dialect check flagged the
  incremental models' statement blocks; the `%` alternative now excludes a `%`
  adjacent to a brace (`(?<!\{)%(?!\})`), keeping the `x % 24` control.
- **`.dockerignore` patterns are root-anchored — a leading `*` does not cross
  `/`** (Phase 8b). `*.tfvars` / `.env` excluded only root-level files, so
  `infra/.env`, `infra/sa-key.json` still shipped into the image; every secret
  pattern is now `**/`-anchored (`**/*.tfvars`, `**/.env`, …). A planted-canary
  test (`test_int_airflow.py::test_image_has_no_secrets`) caught it. The scan
  prunes the build-created `/opt/otr/.venv` (its library files — e.g. dbt's
  `credentials.py` — are not repo secrets).
- **`docker compose down -v` removes containers and volumes but NOT the image**
  (Phase 8b). If a secret ever slipped past `.dockerignore` it persists in the
  image layer; recovery is `docker image rm otr-airflow-8b:latest` (or `down
  --rmi local`), documented in `.dockerignore`.
- **A service account and a Workload Identity pool/provider soft-delete for 30
  days, reserving their ids** (Phase 9a). `infra` uses fixed ids
  (`ontime-pipeline`, `ontime-github-pool`), so an `apply → destroy → apply`
  cycle *within 30 days* fails re-creating them ("exists in a deleted state").
  Recover with `gcloud iam service-accounts undelete` /
  `gcloud iam workload-identity-pools undelete`, or wait out the window
  (`docs/DEPLOYMENT.md`). Harmless for a single demo-day apply/destroy; the
  destroy itself still leaves nothing billable. Datasets and the bucket have no
  such reservation.
