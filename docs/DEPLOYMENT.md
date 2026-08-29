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
  provider (the `workload_identity_provider` root output — `null` until the
  toggle is on) and the `ontime-pipeline` service account — a short-lived
  credential, no secret at rest. The toggle **defaults false**, and
  `github_repository` **has no default**: a default apply builds no WIF trust at
  all, and `enable_ci_wif = true` without a repository is refused at plan by
  the pool's precondition — so no repository, this one included, is ever trusted
  unless you name it (Amendments H, K). CI setup is therefore always the pair:
  `enable_ci_wif = true` **and** `github_repository = "<owner>/<repo>"` (plus
  `github_ref` if not `refs/heads/main`). 9b's `test-int-bigquery` in CI needs
  exactly this opt-in apply.

### Operator permissions for `tf-apply` (your ADC identity, not the SA)

Beyond creating the project resources (project Owner, or Editor + Project IAM
Admin for the SA grants), three `tf-apply` mechanisms need a specific permission — the
first is inside Owner/Editor, the two billing ones are not — and one
post-apply path (the last row) needs a grant Terraform makes:

| Mechanism | Permission | Minimal predefined role |
|---|---|---|
| `user_project_override` (every API call is quota'd on `project_id`) | `serviceusage.services.use` on the project | `roles/serviceusage.serviceUsageConsumer` (in Owner/Editor) |
| `data.google_billing_account` (the budget's currency) | `billing.accounts.get` on the billing account | `roles/billing.viewer` on the billing account |
| `google_billing_budget` create/delete | `billing.budgets.create` / `.delete` on the billing account | `roles/billing.costsManager` on the billing account |
| Impersonating the SA for manual BigQuery builds (9b on) | `iam.serviceAccounts.getAccessToken` on `ontime-pipeline` | `roles/iam.serviceAccountTokenCreator` ON the SA — Terraform grants it to `operator_principal` when set (Amendment Q) |

The billing-account read is deferred to apply (the budget module `depends_on`
the API enablement so a brand-new project still plans), so a missing billing
permission fails **mid-apply with 17 resources already created** — not at
plan. Preflight it once before the first apply:

```
gcloud billing projects describe <project_id>                # shows billingAccountName
gcloud billing accounts describe <billingAccountName>        # 403 here = missing billing.accounts.get
```

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
make tf-validate                              # offline: init -backend=false -input=false -lockfile=readonly + validate + fmt -check
make tf-plan    PROJECT=<project_id>          # reads GCP APIs; shows the diff (free) — ALWAYS before an apply
make tf-apply   PROJECT=<project_id> CONFIRM=yes   # creates resources — ask first
make tf-freeze  CONFIRM=yes                   # after ANY .tf / .tf.json / lock edit: rewrites infra/MANIFEST.sha256 (the offline pin)
```

`tf-apply` / `tf-destroy` / `tf-freeze` require `CONFIRM=yes` from the command
line (`$(origin CONFIRM)`); an environment `CONFIRM=yes` is refused. `PROJECT` is
validated as a GCP project-id before any terraform runs.

Toggles are command-line `-var`s only. Terraform would auto-load an
`infra/terraform.tfvars` or `*.auto.tfvars{,.json}` — gitignored and outside
the manifest, so a plan could differ from the pinned tree with nothing showing
it — so `tf-plan`/`tf-apply`/`tf-destroy` refuse while one exists (`tf-plan:
refused — infra/terraform.tfvars auto-loads …`, Amendment T): delete it and
pass `-var` (or `TF_VAR_*`) in the command you run. Read the `tf-plan` output
before every `tf-apply`; the plan is the review.

`tf-validate`'s init is `-lockfile=readonly`: `infra/.terraform.lock.hcl` pins
one platform hash (`h1:`, darwin/arm64). On another platform the init FAILs
(exit 1) rather than rewriting the pin; fix deliberately with `terraform
-chdir=infra providers lock -platform=linux_amd64` (add the platform, keep the
existing one) then `make tf-freeze CONFIRM=yes`, in one commit.

The default apply creates only the free/near-free layer:

| Resource | What it costs left up | If it runs twice |
|---|---|---|
| BigQuery datasets `raw`, `ontime` | empty datasets free; storage ~$0.02/GB·mo (tiny ≈ $0); queries $5/TB (tiny ≈ $0) | idempotent — Terraform no-ops, no double spend |
| GCS staging bucket `<project>-ontime` (NOT the tfstate bucket) | ~$0.02/GB·mo; tiny (≈ $0); noncurrent versions reaped by a lifecycle rule | idempotent |
| Service account + IAM (+ WIF only when `enable_ci_wif=true`) | free | idempotent |
| Budget (50 / 150 alerts, in the billing account's currency — $ on a USD account) | free (notifies only — see below) | idempotent |
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
the WIF pool if `enable_ci_wif` was on), and the budget. The bootstrap
**tfstate bucket is not managed and is not removed** (it holds the state). The **API enablements stay on**
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
Phase 9a Done-when. That holds for 9b's tables too only because the dbt build
lands inside `ontime` (`generate_schema_name`; the SA cannot create a dataset,
so a per-folder `ontime_<folder>` layout would fail, not sprawl — Amendment I).
That control covers the SA only: your own ADC is project Owner and CAN create
datasets — so every BigQuery build runs **as the SA** (below), and
`generate_schema_name` (9b) makes even an Owner build land in `ontime`.

## Building on BigQuery (Phase 9b) — as the SA, ask-first

1. Apply with `operator_principal` set, as a `-var` (never a tfvars — T):
   `make tf-apply PROJECT=<id> CONFIRM=yes` with
   `TF_VAR_operator_principal="user:<you>"` in the same command — Terraform
   grants you `serviceAccountTokenCreator` ON `ontime-pipeline` (Amendment Q).
2. Impersonate it for ADC — the ONE credential the landing's clients, dbt and
   the parity test all use (no `bq`/`gsutil` on the data path, no second
   impersonation setting, no keyfile):
   `gcloud auth application-default login --impersonate-service-account=<pipeline_service_account output>`
3. Land + build: `make dbt-build TARGET=bigquery PROFILE=tiny PROJECT=<id> CONFIRM=yes`
   — `loader/cli.py` validates `PROJECT`, exports it to dbt as `OTR_GCP_PROJECT`
   (the `bigquery` output has no default; `location: us-central1`), lands
   `fixtures/tiny` through `gs://<id>-ontime/landing/tiny/` into `raw`
   (`make bq-load` runs it alone), then `dbt build --target bigquery` into
   `ontime`; prints `dbt-build OK: tiny/bigquery`.
4. Parity: `make test-int-bigquery PROJECT=<id> CONFIRM=yes` — the three goldens
   off the BigQuery tables byte-for-byte against `fixtures/tiny/expected/`, the
   pins, and `bq ls` = exactly `raw`, `ontime`.

Cost of a tiny run: ~1 MB of storage in `raw` + `ontime`, load jobs free,
~10 MB queried (inside the 1 TB/month free tier) — cents at most; every step
is idempotent (`WRITE_TRUNCATE`, `dbt build` rebuilds), so a second run does
not double anything. `tf-destroy` still removes it all
(`delete_contents_on_destroy`, `force_destroy`).

**CI leg (deferred — BACKLOG "Cross-warehouse dialect drift…", dated trigger).**
A CI `test-int-bigquery` needs the opt-in WIF apply, never the default one:
`make tf-apply PROJECT=<id> CONFIRM=yes` with `TF_VAR_enable_ci_wif=true
TF_VAR_github_repository=<owner>/<repo>`, then the `workload_identity_provider`
output and the SA email into a `workflow_dispatch`-only job via
`google-github-actions/auth`. Not built in 9b: the laptop run above is the
Done-when, and an unrun job would be a claim.

**Gotcha — 30-day soft-delete on re-apply (ARCHITECTURE §8).** GCP soft-deletes
a service account and a Workload Identity pool/provider and **reserves their ids
for 30 days**. Because `infra` uses fixed ids (`ontime-pipeline`,
`ontime-github-pool`), an `apply → destroy → apply` cycle **within 30 days**
fails re-creating them ("already exists, in a deleted state"). Recover with
`gcloud iam service-accounts undelete <id>` /
`gcloud iam workload-identity-pools undelete`, or wait out the window, before the
second apply. Harmless for a single demo-day apply/destroy. **Live:** the
Evidence-row-5 destroy on `ontime-rate-recovery` ran **2026-08-29**, so
`ontime-pipeline@ontime-rate-recovery` is reserved until **~2026-09-28**; a 9b
apply on that project before then runs the **undelete + import detour**
first (the pool was never created — no reservation): `gcloud iam
service-accounts undelete <unique_id> --project=ontime-rate-recovery` (the
numeric `unique_id` is in the local, gitignored `infra/terraform.tfstate.backup`
from the destroy), then — because the state is empty after a destroy and a
bare apply would try to CREATE it again — `terraform -chdir=infra import
module.iam.google_service_account.pipeline
projects/ontime-rate-recovery/serviceAccounts/ontime-pipeline@ontime-rate-recovery.iam.gserviceaccount.com`,
then `make tf-plan` (expect `17 to add`, the SA `0 to change`) → `make tf-apply`.
Or wait for the window to close.

## Spanner trial (Phase 10) and Composer (Phase 11) — teardown dates

Both stay off (`enable_spanner`/`enable_composer` default false) until their
phase, so 9a's `tf-*` targets never create them. When applied: **Spanner** starts
a 90-day free trial, then bills ~$65/mo — tear it down before day 90 by setting
`enable_spanner=false` and re-applying (Phase 10 adds a scoped
`make tf-destroy MODULE=spanner`; **no `MODULE` variable exists in 9a**, so a 9a
`make tf-destroy` destroys the whole stack). **Composer** bills ~$300+/mo —
Phase 11 applies it on demo day and destroys it the same hour (Phase 12). Record
the actual apply dates here when they land.
