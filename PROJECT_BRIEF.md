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

A missed window has **two very different causes that most analytics conflate**:

1. **Reliability-driven misses** — the upload stalls / fails, or a genuinely
   on-time action gets recorded as "late" due to a client or backend fault.
   *(The app's fault, wrongly blamed on the user.)*
2. **Timing-driven misses** — the prompt fires when the user is unreachable
   (asleep, at work, commuting), so they physically can't respond in time.
   *(A fixable scheduling problem, wrongly treated as user apathy.)*

Conflating them is expensive: reliability bugs look like disengagement, and
timing problems look unfixable.

**The solution:** a pipeline that (a) correctly **attributes** every late/missed
event to its true cause, and (b) feeds a per-user **send-time model** that shifts
each prompt toward that user's reachable hours — raising the on-time rate, the
metric most coupled to retention. The intervention is validated with an offline
A/B holdout before any real timing change would ship.

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
| **Amplitude** | Upstream instrumentation: app SDKs → Amplitude → raw-event export into BigQuery. Also the reverse-ETL activation target for send-time cohorts. Bookends the pipeline (source + activation). | **Elegant fit.** Stubbed in repo (event generator stands in for the export); documented production path. |
| **Airflow (Cloud Composer)** | Orchestrates the batch path: ingest → dbt → **test gate** → score → write-back, on a daily schedule. | **Justified** by the DAG shape (branching, retries, quality gate). Heavier than a cron job, but the workflow warrants it. |
| **Terraform** | Infrastructure as code: BigQuery datasets, GCS bucket, Spanner instance, service account, IAM, budget alerts, Composer env. | **Load-bearing.** Declaratively complete; author deploys with own project ID. |
| **Spanner** | Transactional **application DB** (NOT an analytics tool). Two legitimate roles only: (1) source of slowly-changing dims (user/friend state) via change streams → BigQuery; (2) **write-back target** for the send-time serving table the notification service reads. Closes the loop. | **Fits precisely** in those two roles; would be cargo-cult anywhere else. |

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
staging  ──►  attribution        ──►  on-time marts   ──►  features
(clean       (label each late         (on-time rate,       (per-user inputs
 event        event: upload_fault      on-time→retention)   to the model)
 streams)     vs timing_gap)
```

### What's real vs. stubbed in the repo
- **Fully built + runnable locally (DuckDB):** all dbt models + tests, the
  send-time model, the Airflow DAG, Terraform. BigQuery-dialect SQL runs on
  DuckDB via cross-warehouse macros.
- **Stubbed with documented production path:** Amplitude export (event generator
  stands in), Spanner change-stream dim replication (seed file stands in),
  Spanner write-back (table write stands in). Each stub is marked in code with
  the exact GCP service/template that replaces it.

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

---

## 5. Validated foundation (already done — Phase 0)

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
scoring only** (never a model input — that would be leakage).

---

## 6. Build phases

**Track A = local pipeline (free). Track B = real GCP (free tier/trial).
Composer is last and torn down after one run.**

| Phase | Track | What it delivers |
|-------|-------|------------------|
| **0 — Data foundation & premise validation** | A | Event generator; proved separability, recoverable curve, retention coupling. Dataset locked. |
| **1 — Ingestion & staging** | A | Loader → warehouse; dbt sources + staging models; freshness/uniqueness tests. |
| **2 — Attribution & on-time marts** ⭐ *review checkpoint* | A | Core logic: label each late event upload-fault vs timing-gap; on-time-rate + on-time→retention marts; business-logic tests. |
| **3 — Send-time model & offline A/B** | A | Per-user reachable-window model; scored vs held-out truth; treatment-vs-control holdout estimates on-time lift (the recovery number). |
| **4 — Local orchestration** | A | Makefile + Airflow DAG chaining all stages with the test gate; whole pipeline runs on DuckDB, no GCP needed. |
| **5 — GCP foundation (Terraform)** | B | Real BigQuery datasets, GCS bucket, service account + IAM, budget alerts. Free tier. |
| **6 — Spanner trial + closed loop** | B | Spanner free-trial instance; app + serving-table schema; score write-back; dbt against real BigQuery. |
| **7 — Composer module (written, not applied)** | Composer | Composer env as isolated Terraform module (own apply/destroy); DAG auto-uploads. Zero meter until applied. |
| **8 — Live run & teardown** | demo day | Apply Composer (~25 min provision), trigger DAG once, capture green run, `terraform destroy`. Under $25. |
| **9 — Docs, dashboard & narrative** | — | Neutral README, architecture doc, `DEPLOYMENT.md` (bring-up/run/teardown + guardrails), stack-roles table, one-page insight writeup, findings chart. |

**Ordering logic:** local-first so cloud deploys known-good SQL; Phase 2 is the
intellectual core (attribution) and the natural review pause; Composer split into
"write it" (7) vs "run it" (8) to keep the meter off until demo day; docs last so
the numbers in them are real.

---

## 7. Current status & next action

- **Done:** Phase 0 (validated dataset + generator). Architecture and full phase
  plan agreed. Deployment decisions locked (region, dual-path, Composer-once).
- **Next:** build **Phase 1**, then **pause at the Phase 2 checkpoint** for
  review of the attribution logic before building the model and cloud layers.

### Key facts for a resuming session
- Project is **product-agnostic** for public GitHub — no real app names anywhere.
- Repo working name: `ontime-rate-recovery`.
- Local warehouse: **DuckDB**; prod warehouse: **BigQuery**; same SQL via dbt
  cross-warehouse dispatch macros.
- The **honest core** of the pitch: cannot prove real-world retention lift from
  synthetic data, so the pipeline *estimates* on-time lift via offline A/B and
  ships the A/B design to confirm in production. State this plainly — it's a
  strength, not a hedge.
- Spanner is an **app DB**, not analytics — only two roles (dim source via
  change streams; serving-table write-back). Don't let it drift elsewhere.
