# DEPLOYMENT.md — GCP bring-up, cost, teardown

The operational companion to `infra/` (Phase 9a). ARCHITECTURE.md §6 is the
posture; this is the runbook. **Every cloud step is ask-first** and the meter is
off by default — `terraform plan` needs only `project_id`, and nothing billable
is applied until you run `make tf-apply` yourself.

## Auth (ADC / WIF only — never a key)

- **Local:** `gcloud auth application-default login` sets Application Default
  Credentials; `make tf-plan|tf-apply|tf-destroy` and `bq`/dbt pick them up. No
  service-account key is ever downloaded or committed. No `set-quota-project`
  step: the provider sends `project_id` as the quota project itself
  (`user_project_override`; ARCHITECTURE §8).
- **CI (opt-in):** with `enable_ci_wif = true` the `iam` module provisions a
  Workload Identity Federation pool + provider trusting a GitHub OIDC token,
  scoped to **both** the repository (`var.github_repository`) **and** the
  trusted ref (`var.github_ref`, default `refs/heads/main`) — a non-main branch's
  CI cannot mint the token. A workflow uses `google-github-actions/auth` with the
  provider and the `ontime-pipeline` service account — a short-lived credential,
  no secret at rest. The toggle **defaults false**: a default apply builds no WIF
  trust at all, so a fork cannot end up trusting *this* repo's `main` (the
  `github_repository` default) to impersonate *its* service account. A fork that
  wants CI sets `enable_ci_wif = true` **and** its own `github_repository`.

## One-time API bootstrap (a brand-new project only)

Terraform enables every API it needs (`google_project_service`), but that
resource — and the `data.google_project` read at plan time — themselves require
**`serviceusage` and `cloudresourcemanager`** already on. A project that has ever
been used with `gcloud` has them; a brand-new project needs them enabled by hand
once, before the first `tf-plan`:

```
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
  --project=<project_id>
```

After that, `terraform plan` is clean with only `project_id`, and Terraform keeps
the rest of the service set on.

## One-time state-backend bootstrap

Terraform's state lives locally by default, so a fresh clone plans with no
setup. To share state (team / CI), bootstrap the GCS backend once — the bucket
cannot create the backend that stores its own state, so it is **created by hand
and never managed by Terraform** (distinct from the `module.gcs`
artifacts/staging bucket `<project_id>-ontime`, which Terraform does manage and
`tf-destroy` removes). The state bucket therefore survives `tf-destroy` on
purpose, and is hardened to match the managed bucket:

```
gcloud storage buckets create gs://<project_id>-tfstate \
  --project=<project_id> --location=us-central1 \
  --uniform-bucket-level-access --public-access-prevention
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
| GCS staging bucket `<project>-ontime` (NOT the tfstate bucket) | ~$0.02/GB·mo; tiny (≈ $0); noncurrent versions reaped by a lifecycle rule | idempotent |
| Service account + IAM (+ WIF only when `enable_ci_wif=true`) | free | idempotent |
| Budget ($50 / $150 alerts) | free (notifies only — see below) | idempotent |
| **Composer** (`enable_composer=false`) | **not created** — ~$300+/mo if enabled | — |
| **Spanner** (`enable_spanner=false`) | **not created** — ~$65+/mo after the 90-day trial | — |

Total left up by default: a few cents of storage per month. Composer and Spanner
stay off until their phase (11 / 10) flips the toggle on a deliberate apply.

## Budget alerts do not stop spend (the optional kill-switch)

A GCP **budget notifies** when spend crosses a threshold; it does **not** cap or
stop spend. With no `all_updates_rule`/notification channel configured (the case
here), the alert emails go to the **billing-account administrators and billing
users** by default — add an `all_updates_rule` with a Cloud Monitoring
notification channel if you need other recipients or a Pub/Sub trigger. The only
thing that actually stops spend is disabling billing on the project. The real
guardrail is a **Pub/Sub → Cloud Function** that, on a budget notification at the
$150 threshold, calls the Cloud Billing API to detach the billing account. It is
**documented here as optional and left unbuilt** (the meter is off by default, so
there is no runaway to catch yet); build it before
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

Removes every resource in state — the two datasets
(`delete_contents_on_destroy`, so they go even with 9b's tables), the staging
bucket (`force_destroy`, so it goes even with objects), the service account (+
the WIF pool if `enable_ci_wif` was on), and the budget. The bootstrap **tfstate bucket is not managed and is not
removed** (it holds the state). The **API enablements stay on**
(`disable_on_destroy = false`) — deliberately, so a re-apply works and a
project-wide API another workload may use is never disabled by our teardown;
enabled APIs are free, so this leaves nothing billable. Verify the meter is at
zero:

```
bq ls --project_id=<project_id>                       # no ontime/raw datasets
gcloud storage buckets list --project=<project_id>    # only the tfstate bucket remains
gcloud iam service-accounts list --project=<project_id>   # no ontime-pipeline
gcloud billing budgets list --billing-account=<acct>  # no ontime-<project> budget
```

Nothing else is created outside Terraform, and no resource carries
`prevent_destroy`, so `tf-destroy` is total for the managed resources — the
Phase 9a Done-when.

**Gotcha — 30-day soft-delete on re-apply (ARCHITECTURE §8).** GCP soft-deletes
a service account and a Workload Identity pool/provider and **reserves their ids
for 30 days**. Because `infra` uses fixed ids (`ontime-pipeline`,
`ontime-github-pool`), an `apply → destroy → apply` cycle **within 30 days**
fails re-creating them ("already exists, in a deleted state"). Recover with
`gcloud iam service-accounts undelete <id>` /
`gcloud iam workload-identity-pools undelete`, or wait out the window, before the
second apply. Harmless for a single demo-day apply/destroy.

## Spanner trial (Phase 10) and Composer (Phase 11) — teardown dates

Both stay off (`enable_spanner`/`enable_composer` default false) until their
phase, so 9a's `tf-*` targets never create them. When applied: **Spanner** starts
a 90-day free trial, then bills ~$65/mo — tear it down before day 90 by setting
`enable_spanner=false` and re-applying (Phase 10 adds a scoped
`make tf-destroy MODULE=spanner`; **no `MODULE` variable exists in 9a**, so a 9a
`make tf-destroy` destroys the whole stack). **Composer** bills ~$300+/mo —
Phase 11 applies it on demo day and destroys it the same hour (Phase 12). Record
the actual apply dates here when they land.
