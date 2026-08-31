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
Admin for the SA grants), four `tf-apply` mechanisms need a specific permission — the
first is inside Owner/Editor, the two billing ones are not, the custom role's
is in Owner but not in Editor + Project IAM Admin — and one post-apply path
(the last row) needs a grant Terraform makes:

| Mechanism | Permission | Minimal predefined role |
|---|---|---|
| `user_project_override` (every API call is quota'd on `project_id`) | `serviceusage.services.use` on the project | `roles/serviceusage.serviceUsageConsumer` (in Owner/Editor) |
| `data.google_billing_account` (the budget's currency) | `billing.accounts.get` on the billing account | `roles/billing.viewer` on the billing account |
| `google_billing_budget` create/delete | `billing.budgets.create` / `.delete` on the billing account | `roles/billing.costsManager` on the billing account |
| `google_project_iam_custom_role` (the spanner module's data-plane role, Amendment E — only on an `enable_spanner=true` apply) | `iam.roles.create` / `.update` / `.delete` on the project, plus `iam.roles.undelete` for the 7-day detour | `roles/iam.roleAdmin` on the project (NOT inside `projectIamAdmin`; before granting, check whether the operator's base role already carries them — `gcloud iam roles describe roles/editor` and look for `iam.roles.create` — and grant only if it does not) |
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

Toggles reach Terraform ONLY as `VARS='name=value,…'` from the make COMMAND
LINE (`$(origin VARS)`, like `CONFIRM` — an exported `VARS` is refused;
`fix/tf-vars-argv`): `infra/cli.py` parses each item into an argv `-var`
(a bracketed numeric list is one item: `budget_alert_thresholds=[50,150]`),
refuses a malformed item, whitespace, or `project_id` (PROJECT's), refuses
to run while ANY `TF_VAR_*` / `TF_CLI_ARGS*` is in its environment or an
auto-loaded `infra/terraform.tfvars` / `*.auto.tfvars{,.json}` exists
(Amendment T), and gives the terraform child an ALLOWLISTED environment
(`ENV_ALLOW`, ten exact names: `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`,
`CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT`, `SSL_CERT_FILE`, `NO_PROXY`,
`HTTPS_PROXY` — never a credential name, `TF_WORKSPACE`, `TF_DATA_DIR`,
`TF_LOG*`; and any other `GOOGLE_*`/`GCLOUD_*`/`CLOUDSDK_*` name in your
shell refuses the command outright, names only — Phase 10 Amendment N2:
the Google namespace is an allowlist, `infra.cli.CLOUD_ENV_ALLOW`), so the argv is the whole input by
construction and the `tf-plan` you read is the `tf-apply` you get:

```
make tf-plan  PROJECT=<id> VARS='operator_principal=user:<you>'
make tf-apply PROJECT=<id> VARS='operator_principal=user:<you>' CONFIRM=yes
```

Read the `tf-plan` output before every `tf-apply`; the plan is the review.

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
| **Spanner** (`enable_spanner=false`) | **not created** — a `PROVISIONED` 100-PU instance bills from creation, ~$0.09/h (~$65/mo); it is not a free-trial instance (Amendment M) | — |

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

1. Apply with `operator_principal` as a `VARS` item (the only toggle path;
   never a tfvars or a `TF_VAR_*`):
   `make tf-apply PROJECT=<id> VARS='operator_principal=user:<you>' CONFIRM=yes`
   — Terraform grants you `serviceAccountTokenCreator` ON `ontime-pipeline`
   (Amendment Q).
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
5. **Switch ADC back before any `tf-*`:** `gcloud auth application-default
   login` (no `--impersonate-service-account`), and pick the GCP account in
   the browser — a login as any other Google account fails the next `tf-*`
   at refresh with the same 403 shape as the SA case below (round 4's first
   teardown attempt; nothing changed). Verify before a `tf-*`:
   `curl -s "https://oauth2.googleapis.com/tokeninfo?access_token=$(gcloud auth application-default print-access-token)"`
   → `"email"` is the operator. The impersonated SA has no
   `serviceusage` permission, so `tf-plan`/`tf-apply`/`tf-destroy` fail at
   refresh with `Permission denied to list services for consumer container`
   — found live on the first `tf-destroy` after 9b (ARCHITECTURE §8). No
   resource and no state file is changed by that failure (refresh runs before
   any plan); re-auth and re-run.

Cost of a tiny run: ~1 MB of storage in `raw` + `ontime`, load jobs free,
~10 MB queried (inside the 1 TB/month free tier) — cents at most; every step
is idempotent (`WRITE_TRUNCATE`, `dbt build` rebuilds), so a second run does
not double anything. `tf-destroy` still removes it all
(`delete_contents_on_destroy`, `force_destroy`).

**CI leg (deferred — BACKLOG "Cross-warehouse dialect drift…", dated trigger).**
A CI `test-int-bigquery` needs the opt-in WIF apply, never the default one:
`make tf-apply PROJECT=<id> VARS='enable_ci_wif=true,github_repository=<owner>/<repo>'
CONFIRM=yes`, then the `workload_identity_provider`
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

## Spanner (Phase 10) and Composer (Phase 11) — apply and teardown dates

Both stay off (`enable_spanner`/`enable_composer` default false), so a default
apply never creates them. **Composer** bills ~$300+/mo — Phase 11 applies it on
demo day and destroys it the same hour (Phase 12). Record the actual apply
dates here when they land.

### Spanner: bring-up, run, teardown (Phase 10)

The module creates a `PROVISIONED` 100-processing-unit instance, which bills
from its first minute (~$0.09/h, ~$65/mo) — it is NOT a Spanner free-trial
instance (a separate kind, console/gcloud-created, one per project; Phase 10
Amendment M corrected the "90-day trial clock" this section carried). Every
step is ask-first, and the teardown belongs to the same working session as
the apply: never leave it up.

1. **Apply** (your operator ADC — Terraform never runs as the SA, §8. The
   SA detour applies only when the SA is NOT in state: after a full
   `tf-destroy` within its 30-day reservation. After a toggle-flip teardown
   the SA stays live and in state — no detour. The custom role has the same
   shape with a 7-day window: after a toggle-flip teardown within 7 days,
   `gcloud iam roles undelete ontimeSpannerDataUser --project=<id>` then
   `terraform -chdir=infra import 'module.spanner[0].google_project_iam_custom_role.data_user' projects/<id>/roles/ontimeSpannerDataUser`
   before the re-apply):
   `make tf-apply PROJECT=<id> CONFIRM=yes VARS='enable_spanner=true'` —
   adds exactly the spanner module's 9 resources (2 kept-on API enablements,
   instance, database with the `dim_user` + `send_schedule` DDL, the BigQuery
   connection + `raw.dim_user_spanner` federation view, the custom
   data-plane role `ontimeSpannerDataUser` — read/write, no DDL — and 2
   scoped grants, both to the pipeline SA, which is the principal the
   federated read runs as; §8). `tf-apply` plans first and shows you the
   saved plan it applies.
   **The same session, fill in the dated lines below.**
   **While Spanner is up, EVERY `make tf-apply` carries
   `VARS='enable_spanner=true'`** — the toggle defaults false and the
   database has no deletion protection (the toggle-flip is the sanctioned
   destroy). An apply that omits it would plan the teardown — and `tf-apply`
   now REFUSES any plan that destroys something unless `ALLOW_DESTROY=yes`
   is on the command line (it prints the addresses; Amendment F), refuses a
   plan it cannot read back (`the saved plan could not be read back`) and
   refuses any action outside `{no-op, read, create, update, delete}`
   ALWAYS — `forget`, a state drop, included (Amendment N1) — so the
   mistake stops before the cloud. `make tf-plan PROJECT=<id>
   VARS='enable_spanner=true'` first is still the habit. **Live
   2026-08-31:** an apply whose `VARS` carried `enable_spanner=true` but
   omitted the applied `operator_principal` planned `9 to add, 0 to change,
   1 to destroy` and stopped — `tf-apply: refused — the plan destroys
   module.iam.google_service_account_iam_member.operator_token_creator[0]
   …`, exit 2, nothing created, state unchanged (Amendment F's live proof;
   carry EVERY applied toggle in `VARS`).
2. **Land the dims** (as the SA):
   `make spanner-load PROFILE=tiny PROJECT=<id> CONFIRM=yes`.
3. **Prove it**: `make test-int-spanner PROJECT=<id> CONFIRM=yes` — the
   federated view returns the seed's rows, the swapped build
   (`dim_user_identifier: dim_user_spanner`) reproduces the three goldens,
   and the Spanner write-back is idempotent with the DuckDB-pinned row hash
   (its OK line reads `writeback OK: <id>.ontime → spanner, 20 users, 0
   written` on the second run — the read is the warehouse's, not a
   PROFILE's build). `PROFILE` is `tiny` only (a CLI refusal otherwise).
   **Live 2026-08-30:** `spanner-load OK: tiny — 22 dim rows`; `4 passed
   in 221.01s`; `writeback OK: ontime-rate-recovery.ontime → spanner, 20
   users, 0 written`. **Live 2026-08-31, under the custom role
   `ontimeSpannerDataUser` (Amendment E; the live role's permission set is
   the module's 11, no `updateDdl`; the database's one binding):**
   `spanner-load OK: tiny — 22 dim rows`; `4 passed in 239.42s`;
   `writeback OK: ontime-rate-recovery.ontime → spanner, 20 users, 0
   written`.
4. **Tear down the same day** — the SCOPED destroy is the toggle flipped
   back: `make tf-apply PROJECT=<id> CONFIRM=yes VARS='enable_spanner=false'
   ALLOW_DESTROY=yes` (count → 0 destroys exactly the module's resources —
   the plan-first apply lists them and needs the explicit `ALLOW_DESTROY`;
   the two API enablements stay on — free, like the root set). There is no
   `MODULE` variable and no `-target`; a full `make tf-destroy …
   CONFIRM=yes` also removes Spanner along with everything else.

Dated lines (fill on apply day — the BACKLOG Spanner row's trigger):

- `enable_spanner=true` applied: **2026-08-30** (23:37 UTC, `ontime-rate-recovery`,
  operator ADC after the SA undelete + `terraform import` detour; 26/27 on
  the first apply — Amendment D dropped the failed service-agent grant — then
  `No changes` on the toggled re-plan).
- *(Corrected 2026-08-31, Amendment M: the "trial ends 2026-11-28" and
  "destroy-by" lines written here on apply day were wrong — the instance is
  `PROVISIONED`, there was never a trial clock; it billed for the ~13 minutes
  it was up.)*
- Destroyed (`enable_spanner=false` re-applied): **2026-08-30** (23:50 UTC,
  operator ADC): plan `0 to add, 0 to change, 8 to destroy` — exactly the
  module's — then `Apply complete! Resources: 0 added, 0 changed, 8
  destroyed`; `gcloud spanner instances list` → `Listed 0 items.`; state
  keeps the 21 free-tier entries (two datasets, bucket, SA + grants, budget,
  API enablements). **Nothing billable is up.** The two Spanner-side API
  enablements stay on (free, like the root set).
- Second apply (Amendments E–F's live proof): **2026-08-31** (02:30 UTC,
  operator ADC, no detour — the SA was in state): `Plan: 9 to add, 0 to
  change, 0 to destroy` → `Apply complete! Resources: 9 added, 0 changed,
  0 destroyed`; toggled re-plan `No changes`. Instance type `PROVISIONED`
  (100 PU, `regional-us-central1`).
- Second teardown (`enable_spanner=false … ALLOW_DESTROY=yes`):
  **2026-08-31** (02:48 UTC): `Plan: 0 to add, 0 to change, 9 to destroy`
  (the module's 8 + the custom role) → `Apply complete! Resources: 0 added,
  0 changed, 9 destroyed`; `Listed 0 items.`; state 21; default plan `No
  changes`. The custom role is soft-deleted; the third apply (below)
  re-created it with NO detour — the google provider undeletes a
  soft-deleted custom role on create — so step 1's undelete + import
  detour is the SA's alone.
- Third apply (round 4, Amendment N3's live re-proof — the Spanner read
  path changed): **2026-08-31** (06:07 UTC, operator ADC): `Plan: 9 to add,
  0 to change, 0 to destroy` → `Apply complete! Resources: 9 added, 0
  changed, 0 destroyed`; toggled re-plan `No changes`; `INSTANCE_TYPE
  PROVISIONED`, `STATE READY`. As the SA: `spanner-load OK: tiny — 22 dim
  rows`; `test-int-spanner` `4 passed in 248.70s`; `writeback OK:
  ontime-rate-recovery.ontime → spanner, 20 users, 0 written`.
- Third teardown: a first attempt at 06:38 UTC failed at refresh with 403
  `serviceusage.services.use` — the ADC browser login had picked the
  git-only Google account, not the GCP one (nothing changed; ARCHITECTURE
  §8, step 5 below) — then, re-logged-in as the operator, **2026-08-31**
  (06:42 UTC): `Plan: 0 to add, 0 to change, 9 to destroy` → `Apply
  complete! Resources: 0 added, 0 changed, 9 destroyed`; `Listed 0 items.`;
  state 21; re-plan `No changes`. ~35 minutes up ≈ 5¢. **Nothing billable
  is up.**
