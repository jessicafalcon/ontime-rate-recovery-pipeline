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
`valid_to`. **All reachability math is in user-local time**; the conversion
happens once, in staging, against the tz valid at `client_event_time`.

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

One label per `prompt_id × user_id`. Precedence when evidence overlaps (a
delivery fault cannot also be a timing gap — the prompt never arrived):

1. `delivery_fault` — no `prompt_delivered` within `DELIVERY_GRACE_MIN`.
2. `on_time` — `response_recorded` with `client_event_time` inside the window
   AND `server_received_time` inside the window.
3. `upload_fault` — client time inside the window, received time outside; or an
   `upload_failed` chain with no `response_recorded`.
4. `timing_gap` — delivered inside `DELIVERY_GRACE_MIN`, no `capture_started`
   and no `response_recorded` inside the window, no `upload_*` chain. Evidence
   is delivery + no-action ALONE; whether the window was outside the user's
   reachable hours is Phase 5's question (2.8), never an attribution input
   (DECISIONS Phase 1, "timing_gap is delivery + no-action evidence alone").
5. `unattributed` — everything else (skew beyond bound, contradictory evidence,
   e.g. `capture_started` without any upload or response event). Its share is
   bounded by a dbt test (`UNATTRIBUTED_MAX`, spec-pinned); the bound is what
   makes the metric honest.

Labels are `provisional` until the reprocessing lookback closes (2.7), then
`final`. A final label never changes.

### 2.6 Marts *(Phase 4)*

- `ontime_rate_daily`: per `cohort_id × prompt_date` — `prompts_delivered`
  (the denominator; **never user-days**, or delivery faults vanish), counts per
  label, `ontime_rate`.
- `ontime_retention`: per user, on-time rate over a trailing window vs
  retained-at-28d. Descriptive only; the retention gap in synthetic data is a
  designed property, not a finding (PROJECT_BRIEF §5).

### 2.7 Incrementality and late arrival *(Phase 7)*

Upload-fault events arrive hours or days after the prompt — the events the
project is about. Every event-level model is incremental with a **reprocessing
lookback window** of `LOOKBACK_DAYS` (spec-pinned, 3–7) partitioned by
`prompt_date`: BigQuery `insert_overwrite`, DuckDB delete-and-insert per
partition, both behind one macro. Running the lookback twice over the same raw
converges (idempotent). Loads are driven by the partitions a landing touched,
never by the wall clock.

### 2.8 Features and scores *(Phase 5)*

- `features_user_hour`: per user, local-hour histogram of **organic
  `app_opened`** events over `FEATURE_WINDOW_DAYS`. Prompt responses are not
  inputs (exposure bias: you only observe response at the hour you prompted).
- `scores_send_time`: per `cohort_id`, the send window maximizing P(open within
  `window_minutes`); per user, a bounded shift within `MAX_USER_SHIFT_MIN` of
  the cohort moment. Bayesian shrinkage toward the cohort prior for sparse
  users. Circular hour arithmetic (23:00 and 01:00 are 2 h apart). Ties broken
  by explicit key order, never by insertion order.
- Columns: `user_id`, `cohort_id`, `send_hour_local`, `send_minute_local`,
  `confidence`, `model_version`, `computed_as_of` (= max `client_event_time` in
  the feature window — data-derived, never `now()`).

**The model is a dbt model.** Versioned, unit-tested, runs on both warehouses.
Python is reserved for `eval/`.

### 2.9 Serving table *(DuckDB stand-in Phase 8; Spanner Phase 10)*

`send_schedule(user_id PK, cohort_id, send_hour_local, send_minute_local, tz,
confidence, model_version, computed_as_of, written_at)`. Written by an
idempotent batch upsert keyed `user_id`; a row is replaced only when
`(model_version, computed_as_of)` is strictly greater. The notification service
falls back to the cohort default when no row exists.

## 3. Components

```
GENERATOR (seeded)  ── truth side-file (never a source) ── dim_user seed (tz SCD2)
   │ raw events, Amplitude export shape (three clocks, insert_id)
   ▼
RAW LANDING   fixtures/<profile>/raw/*.jsonl  →  DuckDB (local) | BigQuery (prod, Amplitude export)
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

AIRFLOW  load → dbt build → eval → write-back, data-interval-aware, catchup for backfill
         (local Docker | Cloud Composer, isolated Terraform module, applied once)
TERRAFORM  BigQuery datasets · GCS · Spanner (toggle) · Composer (toggle) · IAM · budget alerts
```

### 3.1 Boundaries (who may do what)

| component | reads | writes | may NOT |
|---|---|---|---|
| generator | profile, seed | raw events, truth, dim seed | read anything else |
| dbt | raw, dims | staging → scores | reference `truth/`; call `now()` on a data path |
| eval | dbt outputs, truth | `docs/RESULTS.md` blocks, console | write any table the pipeline reads |
| write-back | `scores_send_time` | `send_schedule` | read truth; read raw |
| Airflow | — | — | contain logic (it orders `make` targets / dbt commands) |

### 3.2 Local ↔ GCP profile switch

One dbt project, two `profiles.yml` targets (`duckdb`, `bigquery`). Dialect
divergences behind exactly four dispatch macros: JSON extraction,
`timestamp_diff`, `safe_divide`, partition overwrite. CI runs `dbt build` on
DuckDB per PR; BigQuery runs are manual (`make dbt-build TARGET=bigquery`).

### 3.3 What is stubbed (and the production swap)

| stub | replaces | swap |
|---|---|---|
| generator → `fixtures/<profile>/raw/events_<upload-date>.jsonl` (one file per UTC `server_upload_time` date — the landing unit Phase 7 replays) | Amplitude → BigQuery export | dbt `source` config |
| `dim_user` seed | Spanner change streams / federation | `EXTERNAL_QUERY` source (demo), Dataflow template (prod) |
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
  forward-only; Phase 3 pins the rule on the negative side.
