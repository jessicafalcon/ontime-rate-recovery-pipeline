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
Phase 10: Spanner is the production dims home — `make spanner-load` upserts the
seed into the Spanner `dim_user` table (key `(user_id, valid_from)`, DDL
generated from the contract), and BigQuery reads it through the
`raw.dim_user_spanner` `EXTERNAL_QUERY` view (§3.3's source swap).

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
`stg_events`, whose `app_opened` rows carry no `prompt_id`. DuckDB
delete-and-insert per partition behind the `partition_overwrite` dispatch
macro; on BigQuery the adapter's native `insert_overwrite` strategy, selected
in the models' `config()` on `target.type` — dbt-bigquery admits no custom
strategy, so that dispatch body raises by design (Phase 9b, Amendment U; §8). The horizon is data-derived (`max(server_upload_time)`), never the clock;
`final` once a partition is ≥ `LOOKBACK_DAYS` behind it, and
`LOOKBACK_DAYS · 24 > late_arrival_max_hours` keeps a late event off a closed
partition. Running the lookback twice over the same raw converges (idempotent).
Loads are driven by the partitions a landing touched, never by the wall clock.

**Append-only landing and the source-scan prune** *(fix/append-landing)*. Raw
lands append-only: `raw.events` persists across loads and each load overwrites
only the selected upload-date partitions — DuckDB delete-then-insert per
`cast(server_upload_time as date)`, BigQuery a `WRITE_TRUNCATE` load per
`raw.events$YYYYMMDD` on a DAY-partitioned table — mirroring the
`partition_overwrite` strategy one layer up; re-landing a date adds 0 net rows.
On BigQuery only, `stg_events` then prunes its `source('raw','events')` read to a
superset upload-time window (`server_upload_time ≥ max(server_upload_time) −
(lookback_days + margin) days`) so an incremental re-run scans a window of
partitions instead of all of raw — the measured item-6 cost (an unpartitioned raw
forced a 19.45 GB re-scan). The window is a superset: wide enough to hold every
row whose local `event_date` is in the reprocess window despite the client↔server
clock offset, and to co-locate both copies of any duplicate `insert_id` (≤ 1 h
apart by generator construction), so the earliest-copy dedupe is unchanged. DuckDB
has no partitions and no benefit, so its SQL is untouched and every DuckDB golden
is byte-identical; the prune is offline-verified (the guard renders only
incrementally on BigQuery, the duplicate span is bounded, the margin is a
per-profile-pinned floor). Its live INCREMENTAL byte-parity is a follow-up:
`make test-int-bigquery` does one FULL build, so it proves the landing +
DAY-partitioning parity (run live 2026-09-03, byte-identical), not the prune
predicate (which fires only when `is_incremental()`). The margin is derived from bounded generator
knobs (`ceil(late_arrival_max_hours/24) + tz_days + 1`), not tuned to a fixture.

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
DuckDB stand-in (Phase 8a) AND on Spanner (Phase 10): a per-row data-derived
value keeps `send_schedule` byte-identical on a re-run and under a backfill — a
wall clock would break both (§4, the determinism policy); a production serving
store may stamp a real ingest time in a carved-out audit column, never
asserted. Phase 10: `make writeback TARGET=spanner` reads the same two
relations off BigQuery `ontime` and writes the Spanner `send_schedule` (the
module's DDL — the nine columns, `model_version` compared under a parsed
numeric order, `v10 > v2`); `TARGET=duckdb` (default) keeps the stand-in. On
Spanner the stored-pair read and the winners' `insert_or_update` are ONE
read-write transaction (`run_in_transaction`, retried on abort), so
replace-iff-greater holds across concurrent write-backs, not only within one
run (Phase 10 Amendment A); on the DuckDB stand-in the same three steps are
one `begin`/`commit` and the file is single-writer across processes
(Amendment H). The read and the write both map columns by NAME
(Amendment I). The SA's Spanner access is a custom data-plane role — no
`updateDdl`; Terraform owns the schema (Amendment E).

### 2.10 Amplitude export mapping *(the local ↔ production interchange)*

The generator emits the Amplitude raw-export shape so the local stub and a real
Amplitude export are the SAME dbt `source` — no model changes between them. The
mapping the export path relies on:

| Amplitude export field | pipeline column (`raw.events`) | note |
|---|---|---|
| `$insert_id` | `insert_id` | Amplitude's own dedupe key; the export can carry duplicates (44 in `tiny`) — staging dedupes on it |
| `event_type` | `event_type` | the nine types in §2.2; a foreign type fails the `accepted_values` source test |
| `user_id` | `user_id` | here a counter (`u-000123`); in production the app's stable user key, never a UUID on the wire |
| `device_id` | `device_id` | counter locally; the app install id in production |
| `event_time` (client) | `client_event_time` | device clock — clock 1 of the three |
| `server_received_time` | `server_received_time` | clock 2; `received − client` is the upload delay |
| `server_upload_time` | `server_upload_time` | clock 3; the export batch landing time — the incremental horizon |
| `event_properties` | `event_properties` (json) | the per-type payload in §2.2 (`prompt_id`, `cohort_id`, `error_code`, …) |
| `user_properties` | — | **not consumed**; `tz`/`cohort_id` come from the `dim_user` SCD2 dimension (§2.3), not the event |

The file shape matches a real export too *(fix/append-landing)*: raw lands as
gzipped hourly files `events_<upload-date>_<HH>.jsonl.gz`, one gzip member per
`server_upload_time` hour (`.json.gz` is Amplitude's own export unit). The name's
first 10 chars are the upload-date partition key (`landing.load._file_date`); the
hour is packaging only, so `THROUGH` still selects by date and a date's hourly
files land together. Gzip is written with no embedded name and `mtime=0`, so the
bytes are reproducible and the frozen manifest matches on a re-seed.

Production landing (§3.3) is the Amplitude → GCS → BigQuery export writing
`raw.events` with the schema **generated** from `generator/models.py`
(`landing/bq_schema.json`); locally the same schema lands the fixture files. Two
export realities the contract already handles: `insert_id` is **not** unique in
raw (staging makes it so), and `error_code` is JSON `null` on
`upload_started`/`upload_completed` and SQL `NULL` once staged. A field the
export adds that no model reads is simply not in `sources.yml` — the generator is
the schema authority, so a new consumed field is a generator change first
(`make gen-sources`), never a hand edit.

## 3. Components

```
GENERATOR (seeded)  ── truth side-file (never a source) ── dim_user seed file (tz SCD2)
   │ raw events, Amplitude export shape (three clocks, insert_id)
   ▼
RAW LANDING   fixtures/<profile>/{raw/*.jsonl.gz, dims/dim_user.csv}  →  DuckDB `raw` schema (local, `make load`) | BigQuery (prod, Amplitude export)
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
| landing | `fixtures/<p>/{raw,dims}` (or `data/out/<p>/`, marked; a `THROUGH` upload date lands a file subset — a landing is a raw-table state, §2.7) | `raw.events` (append-only: persists, per-upload-date partition overwrite; DAY-partitioned on BigQuery), `raw.dim_user` (full replace each load); on BigQuery also the staging objects `gs://<project>-ontime/landing/<p>/{raw,dims}/` incl. a zero-byte `_empty.jsonl` (Phase 9b); on Spanner the `dim_user` table (`make spanner-load`, idempotent upsert — Phase 10) | read any other byte of the fixture; name or read `truth/`; dedupe (staging's job) |
| dbt | raw, dims | staging → scores | reference `truth/`; call `now()` on a data path |
| eval | dbt outputs, truth, the profile JSON (the generator's input), `tests/pins.py` (which the committed RESULTS blocks are pinned to; Phase 13 `make readme`) | console, `data/out/<p>/expected/` (the golden, frozen only by `make freeze`), the marker-confined blocks of `docs/RESULTS.md` and `docs/AB_DESIGN.md` *(Phase 6; `WRITE=yes` only)*, the `README.md` first-screen block + the wholly-generated `docs/img/lift.svg` *(Phase 13 `make readme`; `WRITE=yes` only)* | write any table the pipeline reads; write under `fixtures/`; create or append to a doc |
| write-back | `scores_send_time`, `dim_user_current` (the open `dim_user` row's tz — Phase 8a) | `send_schedule` | read truth; read raw; re-derive a score |
| Airflow | — | — | contain logic (it orders `make` targets / dbt commands) |

### 3.2 Local ↔ GCP profile switch

One dbt project, two `profiles.yml` targets (`duckdb`, `bigquery`). Dialect
divergences behind exactly five dispatch macros: JSON extraction,
`timestamp_diff`, `safe_divide`, `to_local_time` (UTC → local wall time; added
in Phase 2, DECISIONS), partition overwrite. Each has a DuckDB body and a
BigQuery body (Phase 9b — they raised until then) — no `default__` an unknown
adapter could fall into. The partition-overwrite seam's BigQuery half is the
adapter's native `insert_overwrite` strategy, selected in the incremental
models' config on `target.type` (dbt-bigquery admits no custom strategy —
§8), so its dispatch body raises by design. Where a model lands is a `generate_schema_name` hook
(not a sixth macro): on `target.type == 'bigquery'` every model resolves to the
`ontime` dataset Terraform created (two datasets is 9a's pin); every other
target keeps dbt's per-folder default (`main_<folder>` on DuckDB). CI runs
`dbt build` on DuckDB per PR; BigQuery runs are manual and as the pipeline SA
(`make dbt-build PROFILE=<p> TARGET=bigquery PROJECT=<id> CONFIRM=yes`), and
the DuckDB≡BigQuery pin parity is `make test-int-bigquery` (the three goldens
byte-for-byte off the BigQuery tables, behind `OTR_INT`). `PROFILE` names the
data profile, `TARGET` the warehouse.

### 3.3 What is stubbed (and the production swap)

| stub | replaces | swap |
|---|---|---|
| generator → `fixtures/<profile>/raw/events_<upload-date>_<HH>.jsonl.gz` (one gzip per UTC `server_upload_time` HOUR; the date is the landing partition key Phase 7 replays, the hour is packaging — §2.10) | Amplitude → BigQuery export | dbt `source` config |
| `make bq-load` — the fixture files → the GCS staging bucket (`landing/<profile>/`) → `raw.events` / `raw.dim_user`, explicit schema generated from the contract; append-only — a `WRITE_TRUNCATE` load per upload-date partition into the DAY-partitioned `raw.events$YYYYMMDD` plus one for `raw.dim_user`; an empty events selection lands a zero-byte object into the base table (Phase 9b, X; fix/append-landing) | Amplitude's own export job writing the `raw` dataset | the landing step is dropped; the source config is unchanged |
| `dim_user` seed file (`dims/dim_user.csv`, loaded as the `raw.dim_user` source by `make load` — not a dbt seed, whose path could not follow `PROFILE`) | Spanner change streams / federation | *Delivered Phase 10:* the `raw.dim_user_spanner` `EXTERNAL_QUERY` view (spanner module, behind `enable_spanner`) over the Spanner dims `make spanner-load` lands; the swap is the generated source's `dim_user_identifier` var (default = the landed table, so free-tier builds never touch Spanner) — a source-config swap, no model changes. Dataflow template stays the prod path |
| DuckDB `send_schedule` table | Spanner serving table | *Delivered Phase 10:* `make writeback TARGET=spanner` (default `duckdb` keeps the stand-in) |

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
IAM, budget alerts. Spanner: a `PROVISIONED` 100-PU instance that bills from
creation (no free-trial instance — Phase 10 Amendment M), `enable_spanner`
toggle, applied and torn down in one session, dates in `docs/DEPLOYMENT.md`. Composer: `enable_composer` toggle, applied once on
demo day, destroyed the same hour. Budget alerts do not stop spend — stated,
with the optional billing-disable function as the real guardrail. Terraform
state: a versioned GCS remote backend (`fix/tf-remote-state`, ROADMAP item 2 —
the `backend "gcs"` block in `infra/main.tf` is a PARTIAL config, the
`<project_id>-tfstate` bucket bootstrapped by hand and supplied at init from the
validated `PROJECT`, migrated via `make tf-migrate-state`), so a lost local
working copy no longer strands a persisting `enable_spanner=true` stack; WIF for
CI, never JSON keys.

Implemented in Phase 9a (`infra/`, behind `enable_*` toggles that default false;
`project_id` the only required var; one least-privilege service account, with
the CI WIF pool/provider opt-in behind `enable_ci_wif` so a default apply builds
no cross-repo trust; an optional `operator_principal` gets
`serviceAccountTokenCreator` ON the SA so manual BigQuery builds impersonate it
rather than run as an operator's Owner ADC).
`docs/DEPLOYMENT.md` is the runbook — auth (ADC/WIF), the one-time state-backend
bootstrap, the cost table, the optional billing kill-switch, and the teardown
that leaves nothing billable.

**Privacy and PII.** This is a synthetic project: every `user_id`/`device_id` is
a counter, no name, email, IP, or device fingerprint is generated, and the
`event_properties` payloads are counters and enum error codes — nothing personal
to leak. In production the same shape carries real identifiers, so the standing
rules are the safeguards, not an afterthought: `dim_user` holds a coarse IANA `tz`
and a `cohort_id`, never a precise location; the served `send_schedule` is a
`(user_id, hour, minute, tz)` tuple, no behavioural history; the truth side-file
never leaves `eval/` (it is not even a pipeline input); and `data/`, `*.tfvars`,
and any credential are gitignored and never committed (block-secrets hook). A
production deployment adds what synthetic data does not need — field-level access
control on `raw` and the dims, a retention policy on raw events, and a
DELETE-by-`user_id` path for erasure requests — recorded here as the deployment
obligation, not built against fake data.

## 7. Validation stance

Synthetic data cannot prove retention lift. The pipeline reports (a) label
accuracy vs truth, (b) reachable-center MAE, (c) simulated on-time rate under
the recommended schedule — a **counterfactual simulation**, not an A/B — (d) a
temporal-holdout evaluation of the served schedule against real held-out opens
(below) — and ships the production A/B design (user-level randomization,
persistent holdout, power calculation, pre-registered primary metric,
guardrails, send-time jitter).

**Temporal holdout — report (d)** (`fix/holdout-eval`, ROADMAP item 4). The
non-circular counterpart to the simulation. Where the simulation re-draws every
outcome from the same latent that generated the data (it reads `truth/`), the
holdout reads only observed behaviour. The pipeline serves a schedule on data
landed with an upload-date cut (`make dbt-build … THROUGH=<upload-date>`), then
`eval/cli.py holdout` scores that served schedule against the RAW organic
`app_opened` opens uploaded *after* the cut — the reachability signals the model
did not see at serving time. It reports, for the served per-user hour and again
for the cohort band anchor, the share of held-out opens inside a fixed window of
the served hour and the mean circular distance from the served hour to each
user's nearest held-out open. **New invariant: the holdout's only input is raw
organic opens read from the warehouse; it never reads `truth/`, never a
reachable-window or centre quantity (those are truth concepts), and draws no
clock — so the served schedule is scored against real, unseen behaviour.** Its
block regenerates byte-identically (`tests/pins.py`; a generated block in
`docs/RESULTS.md` beside the simulation, checked under `make test`), like the
simulation and the power table.

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
  `to_local_time`, and the landing and profile also pin `TimeZone = 'UTC'`.
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
- **User ADC has no quota project; `billingbudgets.googleapis.com` refuses it**
  (Phase 9a, found on the first live apply). A developer's `gcloud auth
  application-default login` credential carries no quota project, and the
  Billing Budgets API 403s (`SERVICE_DISABLED` on the gcloud default consumer
  project) — 17 of 18 resources applied, the budget did not. Fix in the tree,
  not per machine: `provider "google"` sets `user_project_override = true` +
  `billing_project = var.project_id`, so every call is billed/quota'd against
  our own project (whose APIs Terraform enables). The alternative
  `gcloud auth application-default set-quota-project` would have to be
  remembered on every machine. WIF/SA credentials are unaffected.
- **A budget's currency must be the billing account's** (Phase 9a, the apply
  after the quota-project fix — the first stopped at 17/18 before reaching the
  budget). `currency_code = "USD"` on an MXN billing account is a bare 400
  "Request contains an invalid argument" — no field named. The module now reads
  `data.google_billing_account.currency_code` and uses that, so
  `budget_alert_thresholds` are numbers in the account's currency (the
  "$50/$150" in the records assumes a USD account; the variable lost its `_usd`
  suffix in round 4, Amendment L). The data source lives inside
  `modules/budget`, which `depends_on` the API enablement, so it is read at
  apply time and a fresh project still plans.
- **`terraform init` rewrites the provider lock unless told not to** (Phase 9a,
  review round 7). `.terraform.lock.hcl` carries per-platform `h1:` hashes and
  a plain `init` adds the current platform's — silently changing a file the
  manifest pins. `tf-validate` runs `init -lockfile=readonly`, so on a platform
  the lock lacks (the pin is darwin/arm64 only) it FAILs instead of mutating;
  the fix is a deliberate `terraform providers lock -platform=…` plus `make
  tf-freeze CONFIRM=yes` in one commit (`docs/DEPLOYMENT.md`). Provable only
  from a second platform — static-pinned in the suite.
- **An impersonated-SA ADC cannot run Terraform** (found live, the first
  `tf-destroy` after Phase 9b). The build runs as the SA
  (`gcloud auth application-default login --impersonate-service-account=…`);
  that credential stays in ADC, and the next `tf-*` fails at refresh —
  `Permission denied to list services for consumer container` (the SA holds
  no `serviceusage` role by design, invariant 4 of 9a). Harmless — no
  resource and no state file changed (refresh fails before any plan); the
  runbook's step 5 is to re-login as yourself before any `tf-*`. The same
  403 (`Caller does not have required permission to use project …
  serviceusage.services.use`) appears when that re-login picked a Google
  account with no role on the project (Phase 10 round 4's first teardown
  attempt: the git-only account) — check the ADC email (tokeninfo, step 5)
  before chasing the SA case; again nothing changed.
- **`TF_VAR_*` from the environment reaches Terraform unseen**
  (`fix/tf-vars-argv`, after Phase 9b). Amendment T refused auto-loaded
  tfvars but 9a's runbook still said "or `TF_VAR_*`": an exported
  `TF_VAR_enable_composer=true` would let a plain `make tf-apply … CONFIRM=yes`
  create billable resources with nothing in the argv — and `TF_CLI_ARGS` /
  `TF_CLI_ARGS_<cmd>` are strictly worse (Terraform splices them into the
  argv, `-var-file` included). `infra/cli.py` now refuses to run while any
  `TF_VAR_*` / `TF_CLI_ARGS*` is in its environment, gives the child an
  ALLOWLISTED environment (so a credential variable — a keyfile despite
  "ADC only", under any spelling: the Google namespace is allowlisted,
  Phase 10 Amendment N2 — `TF_WORKSPACE`, `TF_DATA_DIR`, `TF_LOG*` cannot
  reach it either), and a toggle reaches Terraform only as `VARS='name=value,…'` from
  the command line (`$(origin VARS)`) → argv `-var` (validated, `project_id`
  excluded) — the argv is the whole input by construction.
- **`partition_by` is a model config BOTH adapters interpret** (Phase 9b, found
  reading main for the reconciliation). Phase 7's custom strategy read the
  overwrite column from `config.require('partition_by')` as a plain string.
  dbt-bigquery parses that key as its native partitioning **dict**
  (`{field, data_type, granularity}` — a string is a compile error), and
  dbt-duckdb's `duckdb__get_partitioned_by` reads it too (a string is warned
  and ignored for non-DuckLake tables; a **dict raises** in
  `normalize_string_or_list`). No single value satisfies both. The models now
  name the column under `meta.overwrite_partition_col` (a custom key under `meta`, as dbt ≥ 1.10 asks; one neither adapter reads)
  and set the native dict only under `target.type == 'bigquery'` inside
  `config()`; pinned by `tests/test_dbt_conventions.py::
  test_incremental_models_partition_config_is_dialect_safe`.
- **dbt-bigquery admits no custom incremental strategy** (Phase 9b, the first
  live build — Amendment U). Its own `incremental` materialization validates
  `incremental_strategy` against `merge | insert_overwrite | microbatch`
  (`dbt/include/bigquery/macros/materializations/incremental.sql`) and never
  looks up a `get_incremental_<name>_sql` macro, so Phase 7's custom
  `partition_overwrite` strategy — and the fifth seam's BigQuery body — is
  unreachable there. The incremental models select the adapter's native
  `insert_overwrite` on `target.type == 'bigquery'` (dynamic mode: delete the
  batch's partitions, insert — the DuckDB body's semantics), and
  `bigquery__partition_overwrite` raises by design. `dbt parse` does not catch
  it (the check runs at materialization) — only a build does.
- **Unit-test `format: sql` fixtures are Jinja-rendered, but project macros
  are out of scope there** (Phase 9b). `{{ json_literal(...) }}` in a fixture
  → `'json_literal' is undefined`, while `{% if target.type == 'bigquery' %}`
  renders fine — so a dialect-dependent literal (`json '…'` vs `'…'::json`)
  is an inline `target.type` conditional, and fixture arithmetic
  (`date_diff('second', …)`, DuckDB-only) is written as the literal it
  evaluates to. The models' dialect denylist never covered `schema.yml`; the
  BigQuery build is what caught both.
- **BigQuery rejects a load job over zero URIs, but loads a zero-byte NDJSON
  object as 0 rows** (Phase 9b, review rounds 2–4 — the cap's seam). An
  empty selection (`THROUGH` before the first upload) must still recreate
  `raw.events` empty (the DuckDB landing exits 0 with an empty table). A
  second mechanism for it (create/truncate) drew three rounds of findings;
  Amendment X lands a zero-byte `landing/<profile>/raw/_empty.jsonl` through
  the same `WRITE_TRUNCATE` load job — one mechanism, schema always the
  contract, no SQL string on the path. Proven live 2026-08-30.
- **`generate_schema_name_for_env` is not dbt's default** (Phase 9b, first
  DuckDB build after the override). The obvious "else keep the default" call
  collapses EVERY non-`prod` target to `target.schema` — the first build landed
  `main.stg_events` instead of `main_staging.stg_events` and every DuckDB
  reader broke. The override restates dbt's default verbatim
  (`<target.schema>_<custom | trim>`) in its else branch; the in-process
  DuckDB build's relation names are the pin.
- **A JSON column is neither groupable nor castable to STRING on BigQuery**
  (Phase 9b, writing the conflicting-duplicate dbt test). DuckDB happily does
  `count(distinct cast(event_properties as varchar))`; BigQuery refuses both
  the `DISTINCT` and the cast (`TO_JSON_STRING` is the dialect form — which
  DuckDB lacks). The portable predicate compares the payload key by key through
  the `json_extract` macro over the six keys `generator/models.py::
  PROPERTY_KEYS` allows — the payload IS those keys by contract.
- **Terraform auto-loads `terraform.tfvars` and `*.auto.tfvars{,.json}`**
  (Phase 9a, review round 8, reproduced by a local file). They are gitignored
  and outside the manifest, so a toggle could reach an apply with nothing in
  the tree or the argv showing it; `infra/cli.py` refuses plan/apply/destroy
  while one exists (Amendment T).
- **google-cloud-spanner exports client metrics to Cloud Monitoring by
  default** (Phase 10, review round 1). `spanner.Client()` starts a
  built-in metrics exporter thread (`disable_builtin_metrics=False`;
  `google-cloud-monitoring` arrives transitively). The pipeline never asked
  for that egress and the SA carries no monitoring grant, so both clients
  pass `disable_builtin_metrics=True`; pinned statically in
  `tests/test_spanner_landing.py`.
- **A Cloud Spanner federated query has no service-agent identity — it runs
  as the querying principal** (Phase 10, first live apply, 2026-08-30). The
  spec's stack risk ("the connection's service agent existing before the IAM
  grant") was the wrong model: `service-<number>@gcp-sa-bigqueryconnection`
  is Cloud SQL's delegation identity, is never provisioned by a Spanner
  connection, and the grant to it failed the apply (26/27 created). Per the
  docs, the principal running `EXTERNAL_QUERY` needs
  `roles/spanner.databaseReader` on the database and
  `roles/bigquery.connectionUser` on the connection — the pipeline SA's
  database grant + `connectionUser` were the whole set (Amendment D; the
  database grant became the custom data-plane role in round 2, Amendment E).
- **Every predefined Spanner role that writes also carries `updateDdl`; a
  custom role may only carry permissions of an ENABLED API** (Phase 10,
  review round 2). `roles/spanner.databaseUser` is read+write+DDL, so the
  pipeline SA's grant became the custom `ontimeSpannerDataUser`; and because
  "a permission might not be available for use in custom roles if you have
  not enabled the API" (IAM docs), the role sits in the spanner module
  beside the API enablement, not at the root. A deleted custom role keeps
  its id reserved for 7 days (undelete + import detour, like the SA's 30).
- **`terraform apply -auto-approve` applies whatever the toggles imply —
  including the destruction of a resource whose toggle you forgot** (Phase
  10, review round 2). `enable_spanner` defaults false and the database has
  `deletion_protection = false` (the toggle-flip is the sanctioned
  teardown), so an apply that omitted `enable_spanner=true` while Spanner
  was up would have destroyed it silently. `tf-apply` now saves a plan,
  reads it back (`show -json`) and refuses any `delete` action unless
  `ALLOW_DESTROY=yes` has command-line origin (Amendment F); round 4's
  Amendment N1 made the gate an action ALLOWLIST — an unreadable plan or an
  action outside `{no-op, read, create, update}` (+ `delete` with the flag)
  is refused always.
- **The make-based DAG parses on Cloud Composer but cannot EXECUTE there**
  (Phase 12). The Phase 8b DAG's tasks are `BashOperator`s that shell out to
  `make … → uv run` with `cwd = REPO` (`Path(__file__).resolve().parents[2]`); a Composer
  worker has no repo checkout, no `make`, no project venv, and `parents[2]` of
  `/home/airflow/gcs/dags/pipeline_dag.py` is `/home/airflow` (no Makefile). So
  Composer's contribution is that the module APPLIES and the DAG IMPORTS with no
  error (the dual-path import — row 47 — resolves `from tasks import TASKS` in the
  flat `dags/` bucket); the green DATA run + the `send_schedule` evidence come
  from the local Docker-Airflow → real-BigQuery+Spanner rehearsal, which has the
  toolchain (Option A; DECISIONS Phase 12). Making the DAG execute on Composer
  (a custom image with the repo + toolchain baked in — Option B) was rejected as
  out of scope. The DAG is pointed at the cloud by config, not code:
  `orchestration/tasks.py::build_tasks` reads `OTR_DAG_TARGET`/`OTR_DAG_PROJECT`
  at parse time (unset → the local DuckDB default, byte-identical to Phase 8b).
- **Enabling `composer.googleapis.com` transitively enables `compute` and can
  fail with a transient INTERNAL error** (Phase 12, live). The first
  `enable_composer=true` apply failed at `google_project_service.composer`:
  `Error code 13 … [composer.googleapis.com]: … with failed services
  [compute.googleapis.com]` — Composer runs on Compute/GKE, so enabling its API
  pulls in `compute.googleapis.com`, and the batch enable hit a transient
  internal error. No resource was created (the API is the module's first
  resource; nothing billable started). Remedy (a one-time bootstrap, not a code
  change): `gcloud services enable compute.googleapis.com composer.googleapis.com`
  by hand, then re-run `tf-apply` (the API enablement is idempotent — the second
  apply found it on and proceeded to the environment). `docs/DEPLOYMENT.md`
  carries it as a Composer bootstrap step.
- **Two in-process `dbtRunner().invoke` builds against DIFFERENT DuckDB paths in
  one process are nondeterministic** (fix/holdout-eval, review). Every earlier
  in-process build (`test_scores`, `test_simulate`, `test_backfill`) targets ONE
  path per process (or rebuilds the same one), setting `OTR_DUCKDB_PATH` once.
  The holdout is the first to build two paths back-to-back (served ≤ cut, then
  full); on ~1 in 6 runs the second build cross-resolved and the evaluation
  scored 0 users — dbt-duckdb's adapter can hold its connection to the FIRST
  `OTR_DUCKDB_PATH` across invokes in the same process. Remedy: run each build in
  its own subprocess (`eval/holdout.py::build` → `python -m eval.holdout`), so
  each resolves its own path cleanly — exactly how the real `make dbt-build`
  runs one build per process. Any future multi-warehouse builder (ROADMAP items
  6, 7) must isolate its builds the same way, not swap the env in-process.
- **gzip embeds an mtime and (via `fileobj.name`) a source name** (fix/append-landing).
  `gzip.open` at defaults writes a wall-clock mtime into every member, and
  `gzip.GzipFile(fileobj=f)` copies `f.name` into the header — either makes the
  bytes non-reproducible, so the same SEED would fail the frozen manifest.
  Remedy: write raw with `gzip.GzipFile(filename="", fileobj=…, mtime=0,
  compresslevel=<fixed>)` — no name, no timestamp. (Cross-zlib reproducibility
  is assumed; a reseed-identity test proves same-machine, the frozen manifest
  assumes CI's zlib matches — the standing stack risk to watch.)
- **A DAY-partitioned source needs an explicit predicate to prune; a duplicate
  can straddle upload dates** (fix/append-landing). On BigQuery a partition
  filter is what turns an incremental re-run from a full raw re-scan into a
  window scan (§2.7). But `stg_events` dedupes over the source before the
  lookback filter, and a duplicate `insert_id`'s two copies share
  `client_event_time` (hence `event_date`) while differing in
  `server_upload_time` — so a naive `server_upload_time` prune could keep one
  copy and drop the other, changing the earliest-copy winner. Remedy: prune to a
  SUPERSET window whose margin is derived from the bounded generator knobs (the
  ≤ 1 h duplicate span, `late_arrival_max_hours`, the max tz offset), never a
  tight `− lookback_days` cut. Offline-verified (guard + bounds); its live
  incremental byte-parity is a BACKLOG follow-up (`make test-int-bigquery` runs
  one full build, which does not fire the prune).
  The predicate is native BigQuery SQL (`timestamp_sub` on a bare
  `server_upload_time`), a documented carve-out from the five-macro dialect
  contract (DECISIONS): a dispatch macro is the wrong shape — it must be a bare
  partition-column predicate to prune, and it never runs on DuckDB.
