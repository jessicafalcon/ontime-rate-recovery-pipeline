# Build Phases

Each phase is one focused session, one branch, one PR, one spec from
`specs/TEMPLATE.md`, and ends in a **demoable, testable capability** with a
one-line **Done when** whose proof is a command. ≤ ~6 pinned decisions per
phase — split otherwise (7a/7b). Phases 3, 6 and 8 are checkpoints: stopping at
any of them yields a coherent project.

Spec: `ARCHITECTURE.md`. Rules, commands, git workflow: `../CLAUDE.md`. The
brief's original 0–9 plan is in `../PROJECT_BRIEF.md` §6; this list is the
re-cut by verifiable capability (DECISIONS Phase 0).

**Core risk, proven first (Phases 1–3):** the three-clock / delivery-receipt
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

---

## Phase 2 — Staging on DuckDB

**Goal.** dbt project with `duckdb` target; loader `fixtures/<profile>/raw` →
raw tables; `stg_events` (dedupe on `insert_id`, typed, tz → local via
`dim_user` valid-at-time), `stg_prompts`; dbt source freshness, uniqueness,
not-null, accepted-values tests; the four dispatch macros stubbed for both
dialects; CI runs `dbt build`.

**Done when.** `make dbt-build PROFILE=tiny` green; staging row counts and the
dedupe count pinned in `tests/pins.py`; a duplicated `insert_id` in raw yields
one staged row.

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

---

## Phase 4 — On-time marts and metric definitions

**Goal.** `ontime_rate_daily` (denominator `prompts_delivered`),
`ontime_retention`; `docs/METRICS.md` as the single definition of each metric;
`make report`.

**Done when.** On tiny, `sum(label counts) == prompts_delivered` for every
cohort-day (dbt test); the report's on-time rate equals the fixture's expected
value; a delivery-fault-only day shows on-time rate 0, not null.

---

## Phase 5 — Send-time model as a dbt model

**Goal.** `features_user_hour` (organic `app_opened` only), `scores_send_time`
(cohort window + bounded per-user shift, shrinkage, circular hours, explicit
tie-break). `eval` reports reachable-center MAE (hours) and coverage vs truth on
tiny and medium.

**Done when.** MAE ≤ pin on medium; a user with zero organic opens gets the
cohort default with `confidence` at the prior; two runs of `make dbt-build`
give byte-identical `scores_send_time`; no model input references truth.

---

## Phase 6 — Counterfactual simulation and A/B spec ⭐ checkpoint

**Goal.** `eval/simulate.py`: re-run the generator's response function under
the recommended schedule (seeded); report simulated on-time rate vs baseline,
by cause. `docs/AB_DESIGN.md`: randomization unit, persistent holdout, power
calculation, primary metric, guardrails, jitter. `docs/RESULTS.md` generated
block.

**Done when.** `make simulate PROFILE=medium` regenerates the RESULTS block
deterministically (byte-identical on re-run); the simulated lift is reported
per cause and upload-fault lateness is unchanged by construction.

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

**Goal.** Airflow DAG (Docker, local) chaining load → `dbt build` → eval →
write-back (DuckDB stand-in) with data-interval-aware runs and `catchup`;
`make pipeline PROFILE=<p>` runs the same chain without Airflow.

**Done when.** `make pipeline` and a triggered DAG run produce byte-identical
`scores_send_time` and `send_schedule`; a backfill over three intervals equals
one run over the union.

---

## Phase 9 — GCP foundation (Terraform, BigQuery)

**Goal.** `infra/`: BigQuery datasets, GCS bucket, service account + least-
privilege IAM, budget alerts ($50/$150), GCS state backend (bootstrap documented),
`terraform.tfvars` gitignored. dbt `bigquery` target; the four macros proven on
BigQuery.

**Done when.** `terraform plan` clean from a fresh clone with only
`project_id`; `make dbt-build TARGET=bigquery PROFILE=tiny` green with the same
pins as DuckDB; `terraform destroy` leaves nothing billable.

---

## Phase 10 — Spanner: dims and write-back

**Goal.** `enable_spanner` module; `dim_user` via BigQuery federation
(`EXTERNAL_QUERY`); `send_schedule` schema; `serving/writeback.py` idempotent
upsert (replace only on strictly greater `(model_version, computed_as_of)`).
Threat model for every variable-taking / destructive `make` target.

**Done when.** Two write-back runs over the same scores leave `send_schedule`
unchanged (row hash); an older `model_version` never overwrites a newer one;
`make spanner-destroy` prompts unless `CONFIRM=yes` from the command line.

---

## Phase 11 — Composer module (written, not applied)

**Goal.** Isolated Terraform module behind `enable_composer`; DAG upload on
apply; `DEPLOYMENT.md` bring-up / run / teardown / cost table.

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
