# DEPLOYMENT.md — GCP bring-up, cost, teardown

The operational companion to `infra/` (Phase 9a). ARCHITECTURE.md §6 is the
posture; this is the runbook. **Every cloud step is ask-first** and the meter is
off by default — `terraform plan` needs only `project_id`, and nothing billable
is applied until you run `make tf-apply` yourself.

## Auth (ADC / WIF only — never a key)

- **Local:** `gcloud auth application-default login` sets Application Default
  Credentials; `make tf-plan|tf-apply|tf-destroy` and `bq`/dbt pick them up. No
  service-account key is ever downloaded or committed.
- **CI:** the `iam` module provisions a Workload Identity Federation pool +
  provider trusting this repo's GitHub OIDC token (scoped to
  `attribute.repository`). A workflow uses `google-github-actions/auth` with the
  provider and the `ontime-pipeline` service account — a short-lived credential,
  no secret at rest.

## One-time state-backend bootstrap

Terraform's state lives locally by default, so a fresh clone plans with no
setup. To share state (team / CI), bootstrap the GCS backend once — the bucket
cannot create the backend that stores its own state:

```
gcloud storage buckets create gs://<project_id>-tfstate \
  --project=<project_id> --location=us-central1 --uniform-bucket-level-access
gcloud storage buckets update gs://<project_id>-tfstate --versioning
```

Then uncomment the `backend "gcs"` block in `infra/main.tf` and
`terraform -chdir=infra init -migrate-state`.

## Apply and plan

```
make tf-validate                              # offline: init -backend=false + validate + fmt -check
make tf-plan    PROJECT=<project_id>          # reads GCP APIs; shows the diff (free)
make tf-apply   PROJECT=<project_id> CONFIRM=yes   # creates resources — ask first
```

`tf-apply` / `tf-destroy` require `CONFIRM=yes` from the command line
(`$(origin CONFIRM)`); an environment `CONFIRM=yes` is refused. `PROJECT` is
validated as a GCP project-id before any terraform runs.

The default apply creates only the free/near-free layer:

| Resource | What it costs left up | If it runs twice |
|---|---|---|
| BigQuery datasets `raw`, `ontime` | empty datasets free; storage ~$0.02/GB·mo (tiny ≈ $0); queries $5/TB (tiny ≈ $0) | idempotent — Terraform no-ops, no double spend |
| GCS bucket `<project>-tfstate` | ~$0.02/GB·mo; state + artifacts are tiny (≈ $0) | idempotent |
| Service account + IAM + WIF | free | idempotent |
| Budget ($50 / $150 alerts) | free (notifies only — see below) | idempotent |
| **Composer** (`enable_composer=false`) | **not created** — ~$300+/mo if enabled | — |
| **Spanner** (`enable_spanner=false`) | **not created** — ~$65+/mo after the 90-day trial | — |

Total left up by default: a few cents of storage per month. Composer and Spanner
stay off until their phase (11 / 10) flips the toggle on a deliberate apply.

## Budget alerts do not stop spend (the optional kill-switch)

A GCP **budget notifies** when spend crosses a threshold; it does **not** cap or
stop spend. The only thing that actually stops it is disabling billing on the
project. The real guardrail is a **Pub/Sub → Cloud Function** that, on a budget
notification at the $150 threshold, calls the Cloud Billing API to detach the
billing account. It is **documented here as optional and left unbuilt** (the
meter is off by default, so there is no runaway to catch yet); build it before
any long-lived apply (Phase 12 demo day) if you want a hard stop. Sketch:

1. `google_pubsub_topic` the budget publishes to (`budget.all_updates_rule`).
2. `google_cloudfunctions2_function` subscribed to it, running
   `billing.projects.updateBillingInfo({billingAccountName: ""})` when the cost
   exceeds the cap.
3. The function's SA granted `roles/billing.projectManager` on the project.

## Teardown (leaves nothing billable)

```
make tf-destroy PROJECT=<project_id> CONFIRM=yes
```

Removes every resource in state — the two datasets, the bucket
(`force_destroy`, so it goes even with objects), the service account + WIF pool,
and the budget. Verify the meter is at zero:

```
bq ls --project_id=<project_id>                       # no ontime/raw datasets
gcloud storage buckets list --project=<project_id>    # no tfstate bucket
gcloud iam service-accounts list --project=<project_id>   # no ontime-pipeline
gcloud billing budgets list --billing-account=<acct>  # no ontime-<project> budget
```

Nothing is created outside Terraform, and no resource carries
`prevent_destroy`, so `tf-destroy` is total — the Phase 9a Done-when.

## Spanner trial (Phase 10) and Composer (Phase 11) — teardown dates

Both stay off (`enable_spanner`/`enable_composer` default false) until their
phase. When applied: **Spanner** starts a 90-day free trial, then bills ~$65/mo —
tear it down (`make tf-destroy MODULE=spanner`, Phase 10) before day 90;
**Composer** bills ~$300+/mo — Phase 11 applies it on demo day and destroys it
the same hour (Phase 12). Record the actual apply dates here when they land.
