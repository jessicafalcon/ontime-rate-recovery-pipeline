# Build Phases

Each phase is one focused session, one branch, one PR, one spec from
`specs/TEMPLATE.md`, and ends in a **demoable, testable capability** with a
one-line **Done when** whose proof is a command. ≤ ~6 pinned decisions per
phase — split otherwise (7a/7b). Phases 3, 6 and 8 are checkpoints: stopping at
any of them yields a coherent project.

Spec: `ARCHITECTURE.md`. Rules, commands, git workflow: `../CLAUDE.md`. The
`../PROJECT_BRIEF.md` §6 carries the same plan in track view; this list is
authoritative (DECISIONS Phase 0).

**Core risk, proven first (Phases 1–5):** the three-clock / delivery-receipt
attribution must recover the generator's assigned causes, and the organic-open
signal must recover the latent reachable window. If either fails, nothing
downstream matters.

---

## Phase 0 — Skeleton and workflow machinery

**Goal.** uv project (Python 3.12), ruff, pytest, Makefile (`setup test lint
check-docs review-gate mutate`), CI (lint + check-docs + test, SHA-pinned
actions), the four review agents, `/review-round` + `/selfcheck`, the offline
gates (`scripts/`), `specs/TEMPLATE.md`, DECISIONS.md + BACKLOG.md, the three
load-bearing docs. No pipeline code.

**Done when.** `make review-gate` prints `review-gate OK` on a clean checkout
with no services; CI green on the Phase 0 PR; CLAUDE.md "Project tooling" lists
only what is wired.

---

## Phase 1 — Event contract, generator, frozen tiny fixture

**Goal.** Pydantic models for the Amplitude-shape envelope + event types + the
`dim_user` SCD2 row + truth records (ARCHITECTURE §2.1–2.4). Seeded generator
with knobs: users, days, tz mix, upload-fault rate, delivery-fault rate,
reachable-width, duplicate injector, late-arrival injector, clock-skew
injector. `tiny` profile (~20 users × 7 days) committed under `fixtures/tiny/`
(`raw/`, `dims/`, `truth/`) with a sha256 manifest; **read-only after this
phase**. `medium` profile defined, not committed.

**Done when.** `make seed PROFILE=tiny` twice → byte-identical files matching
`fixtures/tiny/MANIFEST.sha256`; unit tests cover each knob; truth isolation
test green.

**Delivered** (`phase-1-event-contract`, spec
`specs/phase-1-event-contract.md`): as planned, plus `make freeze` (the only
writer of `fixtures/`, `CONFIRM=yes` from the command line) and the review
gate's fixture check (`Freeze:` declaration required for any fixture change).
`timing_gap` redefined as delivery + no-action evidence alone (ARCHITECTURE
§2.5, DECISIONS Phase 1). Truth is two files (`users`, `prompts`). tiny = 20
users × 7 days = 140 prompts, 970 events (incl. duplicates), 13 files.

---

## Phase 2 — Staging on DuckDB

**Goal.** dbt project with `duckdb` target; loader `fixtures/<profile>/raw` →
raw tables (`make load PROFILE=<p>`); `stg_events` (dedupe on `insert_id`,
typed, tz → local via `dim_user` valid-at-time), `stg_prompts`; source tests
(not-null, accepted values, uniqueness on `dim_user (user_id, valid_from)` —
`insert_id` is unique only after staging, and no freshness test: it reads the
wall clock); the dispatch macros stubbed for both dialects; CI runs
`dbt build`.

**Done when.** `make dbt-build PROFILE=tiny` green; staging row counts and the
dedupe count pinned in `tests/pins.py`; a duplicated `insert_id` in raw yields
one staged row.

**Delivered** (`phase-2-staging`, spec `specs/phase-2-staging.md`): as
planned, plus `sources.yml` + raw DDL generated from `generator/models.py`
(`make gen-sources`, equality-tested), `dim_user` loaded as a source (not a
dbt seed), a fifth dispatch macro `to_local_time`, `drop-db` (the only
deleter, `CONFIRM=yes` command-line origin), five dbt unit tests + three
singular tests. tiny: 970 raw → 926 staged (44 duplicates), 140 prompts, 22
dim rows. `stg_prompts` = one row per `prompt_id` with the first delivery
receipt. dbt SQL mutation operator re-deferred to Phase 3.

---

## Phase 3 — Attribution ⭐ checkpoint

**Goal.** `attribution` model: the five-label exhaustive set with the
precedence in ARCHITECTURE §2.5, `SKEW_MAX_MIN`, `DELIVERY_GRACE_MIN`,
`UNATTRIBUTED_MAX` as vars. dbt **unit tests** (fixed inputs → expected label)
per precedence rule. `fixtures/tiny/expected/attribution.csv` frozen; `eval`
scores labels vs truth.

**Done when.** `make attribution-golden` diff is empty; label accuracy vs truth
≥ the pin; `unattributed` share ≤ `UNATTRIBUTED_MAX`; every label is exactly
one of the five (accepted-values test).

**Delivered** (`phase-3-attribution`, spec `specs/phase-3-attribution.md`):
as planned, plus the skew gate as its own precedence rule (§2.5 rule 2 — a
skewed prompt's backend-stamped response otherwise labels `on_time`), the
three-clock signal read off `capture_started`/`upload_*`, `cohort_id` =
the prompt's own with an equality singular test, `expected/` produced under
`data/out/` by `make attribution-golden WRITE=yes` and frozen only by `make
freeze` (seed self-check scoped to generator keys; freeze refuses a missing
manifest file), `make eval` (label accuracy, the only truth reader), and the
dbt SQL mutation operators `drop-arm` / `swap-arms`. tiny: 140 labels,
accuracy 1.000, 75/34/17/8/6, unattributed share 0.043; 13 unit tests, 3
singular tests; vars `skew_max_min` 5, `delivery_grace_min` 10,
`unattributed_max` 0.10.

---

## Phase 4 — On-time marts and metric definitions

**Goal.** `ontime_rate_daily` (denominator `prompts_delivered`),
`ontime_retention`; `docs/METRICS.md` as the single definition of each metric;
`make report`.

**Done when.** On tiny, `on_time + upload_fault + timing_gap + unattributed
== prompts_delivered` (and `+ delivery_fault == prompts_sent`) for every
cohort-day (dbt test); the report's on-time rate equals the fixture's expected
value; a cohort-day with delivered prompts and none on time shows on-time
rate 0, not null, and a cohort-day with nothing delivered shows NULL with its
counts populated. *(Corrected at exit: the original "sum(label counts)"
summed all five, which holds only when there is no delivery fault; the
original "delivery-fault-only day" has a zero denominator — BACKLOG row
closed, DECISIONS Phase 4.)*

**Delivered** (`phase-4-marts`, spec `specs/phase-4-marts.md`): as planned
with the two corrections above, plus `prompt_date` as the LOCAL date (34 of
tiny's 140 prompts straddle the UTC date), `ontime_retention.retained` as a
three-state boolean (NULL until the window closes in the data — every tiny
row; `retention_days` 28 kept as the definition), `eval/golden.py`
generalised to a `Golden` spec per frozen file (attribution byte-identical),
`make report` (console only), `docs/METRICS.md` as a LIVING doc with a test
that every mart metric has exactly one block. tiny: 14 cohort-days, 123
delivered, rate 0.609756; 6 unit tests, 2 singular tests; `safe_divide`'s first
caller. Re-freeze: one manifest line added, fourteen unchanged.

---

## Phase 5 — Send-time model as a dbt model

**Goal.** `features_user_hour` (organic `app_opened` only), `scores_send_time`
(cohort window + bounded per-user shift, shrinkage, circular hours, explicit
tie-break); vars `FEATURE_WINDOW_DAYS`, `MAX_USER_SHIFT_MIN`. `eval` reports reachable-center MAE (hours) and coverage vs truth on
tiny and medium.

**Done when.** MAE ≤ pin on medium (seeded and unfrozen — `data/out/medium/`
is the generator's byte-identical output and the pins in `tests/pins.py`
are its manifest; tiny carries the regression pin); a user with zero organic
opens gets the cohort default with `confidence` at the prior; two runs of
`make dbt-build` give byte-identical `scores_send_time`; no model input
references truth.

**Delivered** (`phase-5-send-time`, spec `specs/phase-5-send-time.md`): as
planned, with `medium` run unfrozen (109 MB was not worth a fixture the
read-only rule binds forever; `eval` resolves `truth/` fixtures-then-
`data/out`, printed `(unfrozen)`), one extra column `center_hour_local` (the
unclamped centre `eval` measures MAE against — without it Python would have
to re-derive the model) plus `cohort_hour_local` (the band's anchor, so the
band is a singular test), a third frozen golden `expected/scores_send_time.csv`
(tiny manifest 15 → 16), circular hours as ANSI `floor`/`atan2` with no
sixth macro, the tz-change BACKLOG row closed (per-user pooling on local
hours). tiny: MAE 0.816201 h, coverage 0.6 (20 users, 9–14 opens each);
medium: MAE 0.352354 h, coverage 0.7345 (2,000 users, ≥ 30 opens each); 8
unit tests, 1 singular test; `%` joined the dialect denylist.

---

## Phase 6 — Counterfactual simulation and A/B spec ⭐ checkpoint

**Goal.** `eval/simulate.py`: re-run the generator's response function under
the recommended schedule (seeded) — the SERVED `scores_send_time.
send_hour_local` / `send_minute_local`, never the unclamped
`center_hour_local` (Phase 5 exit audit; an invariant with a test); `medium`
is unfrozen, so `simulate` resolves `data/out/medium/` the way `eval` does.
Report simulated on-time rate vs baseline, by cause. `docs/AB_DESIGN.md`: randomization unit, persistent holdout, power
calculation, primary metric, guardrails, jitter. `docs/RESULTS.md` generated
block.

**Done when.** `make simulate PROFILE=medium` regenerates the RESULTS block
deterministically (byte-identical on re-run); the simulated lift is reported
per cause and upload-fault lateness is unchanged by construction.

**Delivered** (`phase-6-simulation`, spec `specs/phase-6-simulation.md`): as
planned, with common random numbers (four uniforms per prompt from one
seeded stream, applied in the generator's draw order through
`open_probability` — `responds` owns its own draw and is not reused; the
generator is untouched), so `delivery_fault` and `unattributed` are
identical across arms by construction and only `timing_gap` ↔ {`on_time`,
`upload_fault`} moves at the prompt level; a third arm `cohort` (the band
anchor, no per-user shift) beside `baseline` (the prompt's own hour) and
`recommended` (the served pair), and a `data` row (built `attribution`
counts) as the anchor the simulated baseline sits near; both arms
simulated, so the lift is the schedules' alone. `docs/RESULTS.md` carries
one generated block per profile (tiny the regression pin, medium the proof
from `data/out/medium/`); `docs/AB_DESIGN.md` carries a generated power
table (`eval/power.py`, `math.erf` + bisection, no scipy); `make simulate`
/ `make power` check mode diffs a block byte-for-byte, `WRITE=yes`
replaces the marked bytes only. medium: recommended 0.623291 vs baseline
0.460920 (+0.162371, `timing_gap` −10,216). tiny: −0.033 — the `c-morning`
bin-3/10 tie (20 users), a pin, not a claim. No dbt, generator or fixture
change; `eval/` gains `simulate.py`, `power.py`, `blocks.py`.

---

## Phase 7 — Incrementality and late arrival

**Goal.** Event-level models incremental on `prompt_date` with `LOOKBACK_DAYS`
reprocessing behind one partition-overwrite macro (both dialects);
`provisional`/`final` status; the generator's late-arrival knob exercises it.

**Done when.** A raw set split into two landings (day-N, then day-N late
arrivals) converges to the same attribution as a single landing; running the
second landing twice is a no-op; a `final` label never changes.

---

## Phase 8 — Local orchestration ⭐ checkpoint

**Goal.** `serving/writeback.py` + `make writeback` against the DuckDB
stand-in `send_schedule` (replace only on strictly greater `(model_version,
computed_as_of)`); Airflow DAG (Docker, local) chaining load → `dbt build` →
eval → write-back with data-interval-aware runs and `catchup`; `make pipeline
PROFILE=<p>` runs the same chain without Airflow; `make test-int-airflow`.

**Done when.** `make pipeline` and a triggered DAG run produce byte-identical
`scores_send_time` and `send_schedule`; a backfill over three intervals equals
one run over the union.

---

## Phase 9 — GCP foundation (Terraform, BigQuery)

**Goal.** `infra/`: BigQuery datasets, GCS bucket, service account + least-
privilege IAM, budget alerts ($50/$150), GCS state backend (bootstrap documented),
`terraform.tfvars` gitignored; `make tf-plan | tf-apply | tf-destroy`. dbt
`bigquery` target; the five macros proven on BigQuery (the fifth,
`to_local_time`, added in Phase 2); `make test-int-bigquery`.

**Done when.** `terraform plan` clean from a fresh clone with only
`project_id`; `make dbt-build TARGET=bigquery PROFILE=tiny` green with the same
pins as DuckDB; `terraform destroy` leaves nothing billable.

---

## Phase 10 — Spanner: dims and write-back

**Goal.** `enable_spanner` module; `dim_user` via BigQuery federation
(`EXTERNAL_QUERY`); Spanner `send_schedule` schema; the Phase 8 write-back
gains a Spanner target (`make writeback TARGET=spanner`). Threat model for every variable-taking / destructive `make` target.

**Done when.** Two write-back runs over the same scores leave `send_schedule`
unchanged (row hash); an older `model_version` never overwrites a newer one;
`make tf-destroy MODULE=spanner` prompts unless `CONFIRM=yes` from the command
line.

---

## Phase 11 — Composer module (written, not applied)

**Goal.** Isolated Terraform module behind `enable_composer`; DAG upload on
apply; `docs/DEPLOYMENT.md` bring-up / run / teardown / cost table.

**Done when.** `terraform plan` with `enable_composer=false` shows zero Composer
resources; with `true` shows exactly the module's; nothing applied.

---

## Phase 12 — Live run and teardown (demo day)

**Goal.** Apply Composer, trigger one DAG run against BigQuery + Spanner,
capture the green run and the row counts, `terraform destroy`. Rehearsed once
beforehand with local Airflow → BigQuery as the fallback path.

**Done when.** Captured run log + `send_schedule` count in `docs/RESULTS.md`;
billing shows the Composer meter stopped; total spend under $25.

---

## Phase 13 — Docs, dashboard, narrative

**Goal.** README (first screen + Mermaid diagram + docs index), findings chart
from RESULTS, one-page insight writeup, stack-roles table, `check-docs` traces
over every named guard and target.

**Done when.** `make check-docs` green; every number on the README first
screen is sourced from a generated block; a cold reader can run Phases 1–8
from README alone.
