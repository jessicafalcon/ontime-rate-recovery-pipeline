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
| `google_project_iam_custom_role` (the spanner module's data-plane role, Amendment E — only on an `enable_spanner=true` apply) | `iam.roles.create` / `.update` / `.delete` on the project, plus `iam.roles.undelete` — the provider undeletes a soft-deleted role on re-create within its 7-day window | `roles/iam.roleAdmin` on the project (NOT inside `projectIamAdmin`; before granting, check whether the operator's base role already carries them — `gcloud iam roles describe roles/editor` and look for `iam.roles.create` — and grant only if it does not) |
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
(`ENV_ALLOW`, seven exact names: `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`,
`CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT` — never a credential, proxy or
trust-anchor name (P2), `TF_WORKSPACE`, `TF_DATA_DIR`,
`TF_LOG*`; and any name in the cloud-env domain (O1/P1/Q: the `GOOGLE_`/`GCLOUD_`/`CLOUDSDK_`/`GCE_METADATA_`/`SPANNER_` prefixes, the `_EMULATOR_HOST` suffix, the prefix-less names the libraries read, and the transport-redirection class `REDIRECTION_NAMES` — an enumerated closed set, the vendor scan a coverage aid) outside `CLOUD_ENV_ALLOW` in your shell refuses the command outright, names only —
Phase 10 Amendments N2/O1/P1/Q, `infra.cli.CLOUD_ENV_ALLOW`), so the argv is the whole input by
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
   — `pipeline/cli.py` validates `PROJECT`, exports it to dbt as `OTR_GCP_PROJECT`
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
   teardown attempt; nothing changed). The login prints the account it
   selected — read it. To verify without putting a token on any argv or
   URL (round 5 #4), POST it on stdin:
   `gcloud auth application-default print-access-token | sed 's/^/access_token=/' | curl -s -d @- https://oauth2.googleapis.com/tokeninfo`
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
apply never creates them. **Composer** bills ~$300+/mo — Phase 11 WROTE the
module (proven plan-clean, nothing applied); **Phase 12** applied it on demo day
(2026-09-01), ran one DAG, and destroyed it the same session. The actual apply /
teardown dates are in the Composer section below.

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
   the SA stays live and in state — no detour. The custom role needs NO
   detour either: the google provider undeletes a soft-deleted custom role
   on create (third apply 2026-08-31, `Creation complete after 2s` inside
   its 7-day window) — its `iam.roles.undelete` is why the operator
   permission row lists it):
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
- Phase 12 demo-day rehearsal: applied **2026-09-01** (02:42 UTC, operator
  ADC, `VARS='enable_spanner=true,operator_principal=…'`, `9 added`); as the
  SA `spanner-load OK: tiny — 22 dim rows`, then the Docker-Airflow → real
  BigQuery+Spanner DAG run wrote the Spanner `send_schedule` (`20 users, 20
  written`, idempotent `0` on re-run, hash == `SEND_SCHEDULE_SHA256_TINY`);
  destroyed the same session **2026-09-01** (03:32–03:41 UTC) in the combined
  Composer+Spanner toggle-flip (`14 destroyed`), `Listed 0 items.`. ~50 min up
  ≈ 8¢.

### Composer: bring-up, run, teardown (Phase 11 module; applied in Phase 12)

Phase 11 WROTE the module — nothing was applied. The module creates a Cloud
Composer environment (managed Airflow), running as the existing pipeline SA with
one `roles/composer.worker` grant, plus the DAG-bucket upload of the committed
Phase 8b DAG. Composer has a hard cost floor (~$300+/mo, billed continuously)
that no config removes, so — like Spanner — the apply and the teardown belong to
the SAME working session; never leave it up. The environment also takes 25–40+
minutes to create (rehearse the Docker-Airflow → BigQuery fallback first;
PROJECT_BRIEF demo-day risks).

**Phase 11 (plan-only, done):** offline suite green + `tf-validate OK`; the live
proof is an ask-first `tf-plan` (creates nothing):

- `make tf-plan PROJECT=<id>` → **zero** `google_composer_*` in the plan (the
  default toggle is false).
- `make tf-plan PROJECT=<id> VARS='enable_composer=true'` → **exactly** the
  module's resources (`Plan: N to add`, `0 to change`, `0 to destroy` on the
  free-tier layer). Record the observed N and date below when the plan is run.

**Phase 12 — rehearse the zero-Composer path FIRST** (local Docker Airflow → real
BigQuery + Spanner). Composer creation takes 25–40+ min and can fail; this
rehearsal is the fallback demo path AND the source of the `send_schedule`
evidence (Option A — the make-based DAG does not execute on a Composer worker;
§8). It runs the SAME committed DAG the Composer bucket gets, pointed at the cloud
by the override `orchestration/docker-compose.cloud.yml` (never used by `make
test-int-airflow`). Ask-first, cloud-cost, same session. It needs Spanner up
(not Composer):

1. **Spanner up** (operator ADC — carry every applied toggle in `VARS`):
   `make tf-apply PROJECT=<id> CONFIRM=yes VARS='enable_spanner=true'`, then
   `make spanner-load PROFILE=tiny PROJECT=<id> CONFIRM=yes` (as the SA).
2. **Impersonate the SA for ADC on the host** (the ONE credential; the container
   mounts just this ADC json read-only, not the whole gcloud dir — never a
   keyfile):
   `gcloud auth application-default login --impersonate-service-account=<pipeline_service_account output>`.
3. **Build the image and run one DAG** through Docker Airflow with the cloud
   override (`OTR_DAG_PROJECT` sets both the compose guard and the DAG's rendered
   `PROJECT`; the build lands on BigQuery `ontime`, the write-back writes the
   Spanner `send_schedule`):
   ```
   BF="-f orchestration/docker-compose.yml -f orchestration/docker-compose.cloud.yml"
   OTR_DAG_PROJECT=<id> docker compose $BF build
   OTR_DAG_PROJECT=<id> docker compose $BF up -d
   docker compose $BF exec -T airflow airflow db migrate
   docker compose $BF exec -T airflow airflow dags list-import-errors   # empty — the dual-path import resolved
   docker compose $BF exec -T airflow airflow dags test pipeline <through-date>   # a date that lands all of tiny (test-int-airflow's union interval)
   ```
4. **Verify + capture:** the `writeback` task logs
   `writeback OK: <id>.ontime → spanner, 20 users, 20 written` (a re-run of the
   DAG writes `0` — idempotent); the Spanner read-back hashes to
   `SEND_SCHEDULE_SHA256_TINY` (`make test-int-spanner` pins it). Record the green
   run + the row count in `docs/RESULTS.md` (Phase 12 block).
5. **Tear down the container:** `docker compose $BF down -v`. Leave Spanner up
   only if the Composer run below reuses it the same session; otherwise flip it
   off (step below).
6. **Switch ADC back to the operator before any `tf-*`** (the git-account trap,
   step 5 of the BigQuery runbook above): `gcloud auth application-default login`
   and pick the GCP account.

**Phase 12 (apply / run / teardown — the runbook, mirrors Spanner):**

0. **Bootstrap the Composer API deps by hand once** (§8, found live Phase 12):
   `gcloud services enable compute.googleapis.com composer.googleapis.com
   --project=<id>`. Composer runs on Compute/GKE, so enabling
   `composer.googleapis.com` transitively enables `compute` — and that batch
   enable can fail with a transient `Error code 13 … failed services
   [compute.googleapis.com]` on the first `tf-apply` (nothing is created — the
   API is the module's first resource). Enabling both by hand first, then
   re-running the apply, clears it (the enablement is idempotent, so
   Terraform's `google_project_service.composer` finds it on and proceeds).
1. **Apply** (operator ADC, never the SA — §8): `make tf-apply PROJECT=<id>
   CONFIRM=yes VARS='enable_composer=true'` (carry EVERY applied toggle — while
   Composer OR Spanner is up, an apply that omits its toggle plans the teardown,
   which `tf-apply` refuses without `ALLOW_DESTROY=yes`, Amendment F/N1). Expect
   the environment + the composer.worker grant + the two DAG objects; the
   environment create takes 25–40+ min.
2. **Run one DAG** against BigQuery (+ Spanner if also up); capture the run log
   and the `send_schedule` row count (`docs/RESULTS.md`, Phase 12).
3. **Tear down the same session** — the scoped destroy is the toggle flipped
   back: `make tf-apply PROJECT=<id> CONFIRM=yes VARS='enable_composer=false'
   ALLOW_DESTROY=yes` (count → 0 destroys exactly the module's resources; the
   `composer.googleapis.com` enablement stays on — free, like the root set). A
   full `make tf-destroy … CONFIRM=yes` also removes it.

Dated lines (fill when the plan/apply run):

- `enable_composer=true` **plan** observed (Phase 12): **5 to add, 0 to change,
  0 to destroy** — 2026-09-01 (operator ADC; the plan creates nothing).
- `enable_composer=true` applied (Phase 12): **2026-09-01** (~03:00–03:30 UTC,
  `ontime-rate-recovery`, operator ADC, carrying `enable_spanner=true` +
  `operator_principal`). The FIRST apply failed at
  `google_project_service.composer` with a transient `Error code 13 … failed
  services [compute.googleapis.com]` (nothing created — the API is the module's
  first resource; §8 gotcha); `gcloud services enable compute.googleapis.com
  composer.googleapis.com` by hand, then re-apply → `Apply complete! Resources:
  5 added, 0 changed, 0 destroyed` (environment `ontime` after 23m16s + the two
  DAG-bucket objects). Environment `RUNNING`; `dags list-import-errors` → `No
  data found` (the committed DAG imports with no error on managed Airflow — the
  dual-path import); `dags list` shows `pipeline`; one `dags test pipeline
  2026-01-13` run was triggered (`state:running`) and its `dbt_build` task
  failed on the worker — `make: No rule to make target 'dbt-build'` at
  `cwd=/home/airflow` (no repo/toolchain — Option A, §8). The green DATA run is
  the Docker-Airflow rehearsal (`docs/RESULTS.md § Phase 12`).
- Destroyed (`enable_spanner=false,enable_composer=false … ALLOW_DESTROY=yes`):
  **2026-09-01** (~03:32–03:41 UTC, operator ADC): plan `0 to add, 0 to change,
  14 to destroy` (Composer 5 + Spanner 9) → `Apply complete! … 14 destroyed`
  (Composer env destroyed after 7m35s); `gcloud composer environments list` →
  `Listed 0 items.`, `gcloud spanner instances list` → `Listed 0 items.`, `bq
  ls` → `raw`, `ontime`. **Nothing billable is up.** Session spend ≈ cents (well
  under the $25 cap). The `composer.googleapis.com` / `compute` API enablements
  stay on (free); the state keeps the free-tier layer + `operator_principal`.
