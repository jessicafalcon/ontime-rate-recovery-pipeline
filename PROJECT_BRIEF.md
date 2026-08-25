# On-Time Rate Recovery Pipeline — Project Brief

> Handoff document. Self-contained summary of a data-engineering portfolio
> project so another session (or person) can pick it up cold. Written to be
> product-agnostic and safe to share publicly.

---

## 1. Objective

Build a production-shaped, portfolio-grade data engineering pipeline that
demonstrates a **solution**, not just an analysis: it identifies *why* users of
a daily-prompt engagement app miss their response window, and then acts on that
finding by optimizing when each user is prompted.

The project is deliberately built to showcase a specific cloud data stack
(GCP / BigQuery / dbt / Airflow / Terraform / Spanner) used **because each tool
is the elegant fit for its job**, not because a checklist demanded it.

It must satisfy two audiences at once:
- a reviewer browsing GitHub with **no cloud account** can run the whole thing
  locally, and
- the author can also **deploy it live** to a real GCP project.

---

## 2. The problem it solves (neutral framing)

Many social apps run on a **daily engagement ritual**: a prompt (push
notification) goes out, and users have a short window to respond. Responding
inside the window ("on-time") builds the daily habit; missing it (late, or no
response) breaks the habit loop — and a skipped day is one of the strongest
leading indicators of churn in daily-habit products.

A missed window has **very different causes that most analytics conflate**:

1. **Upload-fault misses** — the upload stalls / fails, or a genuinely on-time
   action gets recorded as "late" due to a client or backend fault.
   *(The app's fault, wrongly blamed on the user.)*
2. **Delivery-fault misses** — the prompt never reached the device (APNs/FCM
   failure, notification permissions off). *(Infra fault; the user never had a
   chance to respond. Distinct from both of the others.)*
3. **Timing-gap misses** — the prompt fires when the user is unreachable
   (asleep, at work, commuting), so they physically can't respond in time.
   *(A fixable scheduling problem, wrongly treated as user apathy.)*

The attribution label set is **mutually exclusive and exhaustive**:
`on_time | upload_fault | delivery_fault | timing_gap | unattributed`.
Every late/missed event gets exactly one label; `unattributed` is an explicit
bucket whose share is bounded by a dbt test — that bound is what makes the
metric trustworthy.

Conflating these is expensive: reliability bugs look like disengagement, and
timing problems look unfixable.

**The solution:** a pipeline that (a) correctly **attributes** every late/missed
event to its true cause, and (b) feeds a per-user **send-time model** that shifts
each prompt toward that user's reachable hours — raising the on-time rate, the
metric most coupled to retention. The intervention is validated with a
**counterfactual simulation** (see §5b) and ships with a **production A/B
design spec** before any real timing change would go live.

**Product constraint (decided):** daily-prompt apps send the prompt to everyone
*simultaneously* — the shared moment is the social feature. Per-user send times
would dissolve it. The model therefore optimizes per **timezone/behavior
cohort (band)**, and per-user only *within* a bounded window around the cohort
moment, with a **max-shift-per-day guardrail**. This lowers the theoretical
recovery ceiling (below the naive ~90%) and is stated as a design decision.

### How this maps to observed user complaints
Research into complaints about daily-prompt social apps surfaced, by share of
negative feedback, a cluster dominated by **reliability/infrastructure issues**
(upload/camera failures, missed/unreliable notifications) and **timing/product
issues** (prompt lands at a bad moment, missed window). Cross-referenced against
retention research, the three highest-retention-impact issues were:
1. Upload / camera failures (strongest documented churn driver).
2. Missed / unreliable notifications (the prompt *is* the habit trigger).
3. Friend / social-graph depth (protective; e.g. "N friends in M days" activation).

This project targets issues **#1 and #2 directly** (attribution + send-time),
and carries **#3** as a retention feature.

---

## 3. The stack, and why each tool earns its place

| Tool | Role in this project | Verdict |
|------|---------------------|---------|
| **BigQuery** | Analytical warehouse: all marts, attribution queries, feature tables. | **Load-bearing.** Right tool, no caveat. |
| **dbt** | Transformation layer: staging → attribution → on-time marts → features, with tests as the quality gate. Cross-warehouse macros let the same SQL run on BigQuery (prod) and DuckDB (local). | **Load-bearing.** Sweet-spot fit. |
| **Amplitude** | Upstream instrumentation: app SDKs → Amplitude → raw-event export into BigQuery. Also the reverse-ETL activation target for send-time cohorts. Bookends the pipeline (source + activation). **Its three clocks (`client_event_time`, `server_received_time`, `server_upload_time`) are the reliability-attribution signal** — on-time-by-capture vs on-time-by-receipt disagreeing *is* the upload fault, no heuristics needed. | **Elegant fit.** Stubbed in repo (event generator emits the **exact Amplitude export schema**, incl. `insert_id`, so the stub is faithful and the swap is a source-config change). |
| **Airflow (Cloud Composer)** | Orchestrates the batch path: ingest → `dbt build` (tests gate downstream models by dependency — no separate branch needed) → write-back, on a daily, data-interval-aware schedule with `catchup` for backfill. | **Justified** by the DAG shape (retries, backfill, quality gate). Heavier than a cron job, but the workflow warrants it. Local Docker Airflow against real BigQuery is the fallback demo path. |
| **Terraform** | Infrastructure as code: BigQuery datasets, GCS bucket, Spanner instance, service account, IAM, budget alerts, Composer env. | **Load-bearing.** Declaratively complete; author deploys with own project ID. |
| **Spanner** | Transactional **application DB** (NOT an analytics tool). Two legitimate roles only: (1) source of slowly-changing dims (user/friend state, **user timezone as SCD2**) → BigQuery; (2) **write-back target** for the send-time serving table the notification service reads. Closes the loop. Demo reads dims via **BigQuery federation (`EXTERNAL_QUERY`)** — zero Dataflow workers; change streams documented as the production path when SCD history matters. | **Fits precisely** in those two roles; would be cargo-cult anywhere else. |

**Key design principle:** every tool has a distinct job and nothing does another
tool's job. The system is a **closed loop** — Spanner (transactional) → Amplitude
(capture) → BigQuery (analytics) → dbt (transform) → send-time model → back to
Spanner (serving) → app sends better-timed prompt → new events.

---

## 4. Architecture

### Closed-loop data flow
```
   App + SDK ──► Amplitude ──► BigQuery ──► dbt marts ──► Send-time model
       ▲          (capture)   (raw events   (attribution,   (optimal window
       │                       + marts)      on-time)         per user)
       │                          ▲                               │
       │                          │ change streams                │ scores
       │                          │ (dims)                        ▼
   Notification ◄── Spanner ◄──── Spanner  ◄──────────────  write-back
   service         (app DB)      (serving table)
   sends timed
   prompt ──► generates new events (loop closes)

   Airflow (Composer): orchestrates ingest → dbt → test gate → score → write-back
   Terraform: provisions BigQuery, GCS, Spanner, Composer, IAM, budget alerts
```

### dbt model layers
```
staging  ──►  attribution        ──►  on-time marts   ──►  features ──► scores
(clean,      (exhaustive label:       (on-time rate,       (per-user     (send-time
 dedupe on    upload_fault /           on-time→retention)   local-hour    model AS A
 insert_id,   delivery_fault /                              histograms)   dbt MODEL)
 tz-convert)  timing_gap / unattrib.)
```

**The send-time model is a dbt model, not a Python service.** Per-user
local-hour histogram of *organic* app opens (not prompt responses — avoids
exposure bias), Bayesian shrinkage toward the cohort prior for sparse users,
pick the window maximizing P(open within response window). Circular hour math
(23:00 and 01:00 are 2h apart). Versioned, unit-tested, runs on both
warehouses. Python is reserved for the offline evaluation notebook.

**Incrementality & late-arriving events (core design):** upload-fault events
arrive hours/days after the prompt — exactly the events we care about. All
event-level models are incremental with a **reprocessing lookback window**
(3–7 days; `insert_overwrite` on date partitions in BigQuery, equivalent macro
on DuckDB). Attribution is `provisional` until the window closes.

**Timezone:** all reachability math is in user-local time. `tz` is an SCD2 dim
(Spanner), converted once in staging; DST and travel handled by the dim, not
the marts. The generator emits tz per user.

**Metric definition (single documented place):** on-time rate denominator =
**prompts delivered**, not user-days — otherwise delivery faults vanish.

**Ground-truth isolation, enforced:** generator latents live in a `truth.*`
schema with no dbt source; a CI check asserts no model references it.

### What's real vs. stubbed in the repo
- **Fully built + runnable locally (DuckDB):** all dbt models + tests, the
  send-time model, the Airflow DAG, Terraform. BigQuery-dialect SQL runs on
  DuckDB via cross-warehouse macros.
- **Stubbed with documented production path:** Amplitude export (event generator
  stands in, emitting the real export schema), Spanner dim replication (seed
  file locally; BigQuery federation on GCP; change streams documented for prod),
  Spanner write-back (idempotent batch upsert keyed by `user_id` with
  `send_hour_local`, `tz`, `confidence`, `model_version`, `computed_at`; the
  notification service falls back to the cohort default when no row exists).
  Each stub is marked in code with the exact GCP service/template that replaces it.
- **Cross-warehouse macros are limited to the known divergences:** JSON
  extraction, `TIMESTAMP_DIFF`, `SAFE_DIVIDE`, `insert_overwrite`. CI runs
  `dbt build` on DuckDB per PR from Phase 1 so dialect drift is caught early.

### Deployment posture (decided)
- **Region:** `us-central1`.
- **Dual-path:** same codebase runs locally on DuckDB *and* deploys to real GCP
  (profile switch).
- **Free/near-free layer, safe to leave up:** BigQuery (free tier: 1 TB queries
  + 10 GB storage/mo), Spanner (**90-day free-trial instance**, separate from
  the $300 credit), Cloud Storage (pennies), Terraform, IAM, budget alerts.
- **Composer (paid, ~$400/mo floor, bills continuously):** built as an
  **isolated Terraform module** with its own apply/destroy. NOT left running.
  Plan: spin up once on demo day, run the DAG live, capture the green run, then
  `terraform destroy` — ~1 hr of meter, under $25 of the $300 credit.
- **Guardrails as a deliverable:** budget alerts ($50 / $150), isolated Composer
  module, one-command teardown, all documented in `DEPLOYMENT.md`.
- **Known cost/risk edges (documented, not hidden):**
  - Budget alerts do **not** stop spend. Optional Pub/Sub → Cloud Function that
    disables billing at $150 is the real guardrail.
  - Spanner trial expires at 90 days → ~$65/mo minimum. Teardown date recorded
    in `DEPLOYMENT.md`; `enable_spanner` Terraform toggle.
  - Dataflow (change-stream template) is **not** free — hence federation for demo.
  - Composer env creation can fail or take 40+ min. Rehearse once before demo
    day; Docker Airflow → real BigQuery is the zero-Composer fallback.
  - Terraform: GCS state backend (bootstrapped manually, documented),
    `enable_composer` via `count`, `terraform.tfvars` gitignored, Workload
    Identity Federation for CI — never JSON keys.

---

## 5. Validated foundation (premise validation — feeds Phase 1)

A synthetic event generator produces a daily-prompt dataset engineered so the
two lateness causes are **separable** and the timing gap is **recoverable**.
Validation results on the locked dataset (8,000 users, ~86k responses):

- **Outcome mix:** ~57% on-time, ~38% timing-gap late, ~5% upload-fault late.
- **Recoverable curve:** on-time rate runs **93.5%** when the prompt lands within
  1h of the user's reachable window → **9%** at 6h+ away (clean, monotonic).
  This is the signal the send-time model exploits.
- **Intervention ceiling estimate:** ~57% on-time today → ~90% if perfectly
  timed (timing-gap lateness recovered; upload faults remain).
- **Retention coupling:** users with low on-time rate (<40%) churn ~80% vs ~69%
  for high on-time rate (≥60%) — ~11–12 pt gap.

Ground-truth latent "reachable center" is emitted per user **for offline model
scoring only** (never a model input — that would be leakage; see enforcement in §4).

**Framing rule:** these numbers are what the generator was *told*. Present them
as "the dataset is engineered so the signal exists and is separable," never as
insight. The retention gap in particular is a designed property, not a finding.

**Repo gap (open):** the generator and locked dataset are not yet committed.
The validation is not reproducible until Phase 1 lands them with a fixed seed
and a dataset hash (`fixtures/tiny/MANIFEST.sha256`).

### 5b. Validation: counterfactual simulation + production A/B spec

Treatment-vs-control on synthetic data, where outcomes are re-simulated from
the same latent that generated the data, is **not an A/B test** — it is a
counterfactual simulation against the generator's response function. It is
reported as such:
- (a) model recovery of the latent reachable center — MAE in hours, coverage;
- (b) simulated on-time rate under the new (cohort-constrained) schedule.

What ships for production is an **A/B design spec**: user-level randomization,
**persistent holdout**, power calculation, pre-registered primary metric
(on-time rate per delivered prompt), guardrail metrics (notification opt-outs,
unsubscribes), plus a small **randomized send-time jitter** in production so
the model keeps learning and provides a continuous natural experiment.

---

## 6. Build phases

Authoritative plan: `docs/PHASES.md` (re-cut by verifiable capability on
2026-08-24; each phase ends in a demoable capability with a one-line "Done
when" whose proof is a command). Spec: `docs/ARCHITECTURE.md`. The table below
is the same plan in the brief's track view.

**Track A = local pipeline (free). Track B = real GCP (free tier/trial).
Composer is last and torn down after one run.** ⭐ = checkpoint (stopping there
yields a coherent project).

| Phase | Track | What it delivers |
|-------|-------|------------------|
| **0 — Skeleton & workflow machinery** | — | uv/ruff/pytest, Makefile gates (`review-gate`, `mutate`, `check-docs`), CI, review agents, spec template, DECISIONS/BACKLOG, the three load-bearing docs. No pipeline code. |
| **1 — Event contract, generator, frozen tiny fixture** | A | Pydantic models (Amplitude shape, three clocks, tz, delivery events); seeded generator with fault/late/skew/duplicate knobs; `fixtures/tiny/` committed with a sha256 manifest, read-only after. Carries §5's validation. |
| **2 — Staging on DuckDB** | A | dbt project; loader; `stg_*` (dedupe on `insert_id`, tz → local); source tests; the four dispatch macros; **CI: `dbt build`**. |
| **3 — Attribution** ⭐ | A | Five-label exhaustive set with precedence; dbt unit tests per rule; `expected/attribution.csv` golden diff; label accuracy vs truth pinned; `unattributed` bound. |
| **4 — On-time marts & metric definitions** | A | `ontime_rate_daily` (denominator = prompts delivered), `ontime_retention`, `docs/METRICS.md`, `make report`. |
| **5 — Send-time model as a dbt model** | A | Organic-open features; cohort-constrained scores with bounded per-user shift, shrinkage, circular hours; MAE vs truth pinned on tiny + medium. |
| **6 — Counterfactual simulation & A/B spec** ⭐ | A | Seeded re-simulation under the recommended schedule; `docs/AB_DESIGN.md`; `docs/RESULTS.md` generated block. |
| **7 — Incrementality & late arrival** | A | Lookback-window reprocessing behind one partition-overwrite macro; provisional→final; two-landing convergence proof. |
| **8 — Local orchestration** ⭐ | A | Airflow DAG (Docker) + `make pipeline`; byte-identical outputs; backfill equals one run over the union. |
| **9 — GCP foundation (Terraform, BigQuery)** | B | Datasets, GCS, least-privilege IAM, budget alerts, GCS state; dbt `bigquery` target with the same pins as DuckDB. |
| **10 — Spanner: dims & write-back** | B | `enable_spanner` module; dims via federation; idempotent `send_schedule` upsert; threat model on destructive targets. |
| **11 — Composer module (written, not applied)** | Composer | Isolated module behind `enable_composer`; DAG upload; `DEPLOYMENT.md`. Zero meter. |
| **12 — Live run & teardown** | demo day | Apply, one DAG run, capture, `terraform destroy`. Under $25. |
| **13 — Docs, dashboard & narrative** | — | README first screen + diagram, findings chart, insight writeup, `check-docs` traces. |

**Ordering logic:** machinery before code so the first phase is judged by a
command, not a judgment; the frozen fixture lands first so every later phase is
a diff against it; the core risk (attribution recovers assigned causes, organic
opens recover the latent window) is proven in 1–5 before any cloud; Composer
split into "write it" (11) vs "run it" (12) to keep the meter off until demo
day; docs last so the numbers in them are real.

---

## 7. Current status & next action

- **Done:** premise validation (§5; generator + dataset exist locally, **not
  yet committed**). Architecture and full phase plan agreed. Deployment
  decisions locked (region, dual-path, Composer-once). **Architecture review
  completed 2026-08-24** — decisions folded into this document (see §8).
  **Phase 0 (skeleton & workflow machinery) built on `phase-0-skeleton`**,
  awaiting review and merge.
- **Next:** merge Phase 0, then **Phase 1** (event contract + seeded generator
  + frozen `fixtures/tiny/`), Phase 2 staging, then **pause at the Phase 3
  checkpoint** for review of the attribution logic before building the model
  and cloud layers.

### Key facts for a resuming session
- Project is **product-agnostic** for public GitHub — no real app names anywhere.
- Repo working name: `ontime-rate-recovery`.
- Local warehouse: **DuckDB**; prod warehouse: **BigQuery**; same SQL via dbt
  cross-warehouse dispatch macros.
- The **honest core** of the pitch: cannot prove real-world retention lift from
  synthetic data, so the pipeline *estimates* on-time lift via counterfactual
  simulation and ships the A/B design to confirm in production. State this plainly — it's a
  strength, not a hedge.
- Spanner is an **app DB**, not analytics — only two roles (dim source incl.
  tz SCD2; serving-table write-back). Don't let it drift elsewhere.
- Send-time model lives in **dbt**, optimizes per **cohort band** with bounded
  per-user shift; uses **organic opens**, not prompt responses.
- Validation is a **counterfactual simulation**, not an A/B; the A/B is a spec.

---

## 8. Architecture review log (2026-08-24)

Staff-level review of this brief before implementation. Verdict: right
skeleton for both portfolio and production; five issues would have broken or
misled in production. All resolved by design changes above, none by changing
the stack.

| # | Blind spot | Resolution |
|---|-----------|------------|
| 1 | Per-user send times dissolve the simultaneous "shared moment" that defines the product category | Cohort-band optimization; bounded per-user shift; max-shift/day guardrail (§2) |
| 2 | Late-arriving upload-fault events are dropped by naive daily batch — the exact events the project is about | Incremental models with reprocessing lookback; provisional→final attribution (§4) |
| 3 | Two-cause taxonomy was not exhaustive; delivery faults (complaint #2) had no home | Five-label exclusive/exhaustive set incl. `delivery_fault`, `unattributed` with tested bound (§2) |
| 4 | Timezone never mentioned; reachability is meaningless in UTC | tz as SCD2 dim, local-time conversion in staging, circular hour math (§4) |
| 5 | "Offline A/B" on synthetic data is circular | Renamed counterfactual simulation; production A/B spec ships separately (§5b) |

Elegance improvements adopted: Amplitude three-clock attribution; model-as-dbt-
model; organic opens + production jitter; Spanner federation instead of Dataflow
for demo; `dbt build` as the gate; dbt unit tests; enforced truth isolation.

Risk register: budget alerts don't stop spend; Spanner trial expiry; Dataflow
cost; Composer demo-day failure; cross-warehouse dialect drift; synthetic
numbers mistaken for findings; §5 validation not reproducible until Phase 1
commits the generator.
