# Phase 9a — GCP foundation (Terraform) (PROPOSED)

Contract for the `phase-9-gcp-foundation` branch (9a). Source: `docs/PHASES.md`
Phase 9 — the infra half (Terraform, datasets, bucket, IAM, budgets); the
BigQuery dialect + landing + pin parity is **9b**, a separate spec finalized
after 9a merges. Depends on Phase 8 merged (8a write-back `serving/` PR #10, 8b
Airflow DAG `orchestration/` PR #11 — both in main at `f916949`).

**Status: PROPOSED — do not start until approved.** No new pip dependency:
Terraform and gcloud are CLI tools, not packages (CLAUDE.md allowlist). Verified
installed in the first hour: **terraform v1.15.8, gcloud 578.0.0, bq 2.1.36**
(darwin_arm64) — a STOP-and-ask under ARCHITECTURE §8 only if a required
provider or a `terraform validate` feature turns out unsupported. `dbt-bigquery`
is 9b's allowlist entry, not 9a's.

## Reconciliation against main (first commit on the branch)

Drift between the plan and what main actually is, and the carry-overs due at
Phase 9. Items marked **design change** were **approved 2026-08-26**. Facts are
read off main (`f916949` — Phase 8 complete: 8a write-back, 8b Airflow DAG).
Rebased onto that main 2026-08-28 (was drafted against `e766e27`, pre-Phase-8).

1. **Some Phase 9 scaffolding pre-exists — fact.** `.gitignore` already ignores
   `.terraform/`, `*.tfstate*`, `*.tfvars` with `!*.tfvars.example` (no change
   needed). `dbt/profiles.yml` already carries a skeletal `bigquery` target
   (`method: oauth`, env-var project, `dataset: ontime`) — its completion
   (`location`, `priority`) is **9b**, not 9a. The `dbt-build TARGET != duckdb`
   cloud-cost guard (`CONFIRM=yes $(origin)`) already exists (Phase 2 Amendment
   2). 9a touches none of these.

2. **The `infra/` tree and the meter-off posture — design change.** A new
   top-level `infra/` (`main.tf`, `variables.tf`, `outputs.tf`,
   `modules/{bigquery,gcs,iam,budget,composer,spanner}`). `enable_composer` and
   `enable_spanner` default **false**; their modules are written but count-gated,
   so a default plan creates zero of them (Composer is Phase 11, Spanner Phase
   10). `project_id` is the only required var; `region` defaults `us-central1`;
   the budget's billing account is **derived** from `project_id` via a data
   source (not a second required var); the WIF repo slug has **no default**
   (K) — CI WIF is opt-in (H).
   `infra/` is already in the Repo map and the truth-isolation exemption set
   (Phase 0). **Approved.**

3. **Least-privilege SA + WIF, ADC/WIF only — design change.** One service
   account with BigQuery data/job roles on the two datasets and object-admin on
   the bucket, never `roles/owner|editor`; a Workload Identity Federation pool +
   provider bound to the GitHub repo, so CI authenticates by short-lived OIDC
   token, **never a committed key**. This also writes — opt-in behind
   `enable_ci_wif` (H) — the WIF the BACKLOG row
   "Cross-warehouse dialect drift is caught only on DuckDB in CI" needs; the row
   closes at 9b exit when the parity job runs on it. **Approved.**

4. **Budget alerts 50 / 150 in the billing account's currency; the kill-switch is documented, not built —
   design change + carry-over.** DUE BACKLOG row "Budget alerts do not stop
   spend": alerts notify only; the real guardrail is an optional Pub/Sub → Cloud
   Function that disables billing. Per the row ("built only if the author wants
   it") it is **documented as optional** in `docs/DEPLOYMENT.md` and NOT built.
   Closes the row (documented in Phase 9). **Approved (document-only).**

5. **The four `make` targets (five with `tf-freeze`, Amendment P) — design change.** `tf-validate` (offline: `init
   -backend=false -lockfile=readonly` + `validate` + `fmt -check`), `tf-plan`, `tf-apply`,
   `tf-destroy`, driven by a thin `infra/cli.py` that validates `PROJECT`
   (GCP-project-id shape) and derives the `-var`. `tf-apply` and `tf-destroy`
   are cloud-cost / destructive → `CONFIRM=yes` with command-line origin
   (`$(origin)`), ask-first, security-reviewer mandatory. Each new variable takes
   `$(call _Q,$(value VAR))`, `unexport`, a threat-model row, a
   `tests/test_makefile.py` literal test. **Approved.**

6. **The state backend is bootstrap-documented, not applied — fact
   (ARCHITECTURE §6).** A GCS bucket cannot cleanly create the backend it stores
   its own state in; `docs/DEPLOYMENT.md` documents the one-time bootstrap
   (create the bucket, then `terraform init -migrate-state`). The default
   backend for `tf-validate`/`tf-plan` from a fresh clone is local; the GCS
   backend block is present but commented with the bootstrap note.

7. **Drift to correct at exit — facts.** CLAUDE.md: Repo map `infra/` (real
   now), Commands (`tf-validate|tf-plan|tf-apply|tf-destroy`), Current status,
   BACKLOG count; `docs/ARCHITECTURE.md` §6 (state backend / WIF as landed), §8
   if a surprise lands; `docs/PHASES.md` Phase 9 "Delivered" (9a half);
   `DECISIONS.md` Phase 9a appendix + a note on the in-force Infra line;
   `docs/DEPLOYMENT.md` new (bootstrap, cost, teardown). **BACKLOG:** "Budget
   alerts do not stop spend" struck (documented); "Spanner 90-day trial expiry"
   re-checked and **re-deferred** (module written behind `enable_spanner=false`;
   the trial clock starts only on a Phase 10 apply). The 9b-triggered rows
   (conflicting-dup guard, dialect denylist, cross-warehouse drift, THROUGH
   calendar, the DAG's build owning its landing) stay open until 9b, each
   annotated with its 9a re-check. Count 14 → **15** after 9a (one struck, one
   new DUE row from Amendment I, one new dated row for the SA-id reservation —
   round 6 #20).

8. **Phase 8 landed under 9a — fact (post-rebase).** main now carries `serving/`
   (8a write-back) and `orchestration/` (8b Airflow DAG), plus `make writeback |
   pipeline | test-int-airflow` and a `THROUGH` knob on `make dbt-build`. None is
   9a's surface (9a is `infra/` only), and 9a's `make` additions merge cleanly
   beside them. Phase 8b opened **BACKLOG "The DAG's build owns its landing in
   one task"** whose trigger names **Phase 9 spec reconciliation**: `loader/cli.py
   ::dbt_build` calls the DuckDB `load()` even for a non-`duckdb` `TARGET`, which
   is wrong once the landing is `bq load` GCS→BigQuery. This is a **9b** fix (split
   a build-only path from the landing; thread `TARGET`) — 9a touches no dbt/DAG
   code, so the row is **re-deferred to 9b** here, acknowledged in the 9b
   reconciliation-to-come. The related 8b row "`computed_as_of` is not a complete
   discriminator" and "`model_version` compares as a string" are Phase 10
   triggers, not 9a/9b.

Design changes — items 2, 3, 4, 5 — **approved 2026-08-26**. Two specs, not one:
9a is infra with no data and a mandatory security review; 9b is the dbt/dialect
half with the pin-parity proof. 9a merges before 9b's reconciliation.

## Amendments (review round 1, approved 2026-08-29)

Four design changes from round 1 (findings in parentheses). Each names the
invariant it restores; the 15 test/wording fixes are applied without amendments.

- **A — the Terraform-managed bucket is not the state backend, and destroy is
  total (#1 BLOCKER, #3, #10, #15).** The `gcs` module named its bucket
  `<project_id>-tfstate` — the same bucket the bootstrap docs create by hand for
  the backend — so apply collided and destroy would delete the store holding its
  own state, and the SA held `objectAdmin` on it. Restores **invariant 5**: the
  state bucket is bootstrap-only and unmanaged; the module manages a distinct
  staging bucket (`<project>-ontime`), the SA's `objectAdmin` is on *that*, both
  datasets gain `delete_contents_on_destroy` (so destroy works once 9b lands
  tables), and the bucket gains `public_access_prevention = "enforced"` + a
  noncurrent-version lifecycle rule. Rejected: managing the state bucket (the
  chicken-and-egg the bootstrap exists to avoid).

- **B — WIF trust is branch-scoped (#2).** The provider trusted
  `attribute.repository` only, so any branch's CI could mint the SA token, and no
  test pinned the condition. Restores **invariant 7** (new): the
  `attribute_condition` requires the repo AND `assertion.ref == var.github_ref`
  (default `refs/heads/main`); the test (now
  `test_wif_provider_condition_is_the_repo_and_ref_conjunction`) reddens if either
  half is deleted. Rejected: repository-only scoping (a PR branch could
  impersonate).

- **C — `tf()` takes an injectable runner so no offline test spawns a live
  terraform (#5, #12).** `cli.tf("apply", …)` was one mutated-away guard from
  `subprocess.run(["terraform","apply","-auto-approve"])`, safe today only by
  worktree isolation. Restores **invariant 6**'s "validated/gated before the
  runner runs": `tf(cmd, …, runner=subprocess.run)`, tests inject a recording
  fake, and `require_confirm delete-call` joins the mutations block (now safe) —
  plus a test that a bad `PROJECT` dies before the runner is called. Rejected:
  leaving the destructive path un-mutation-tested (a real gap, and the sweep
  couldn't safely cover it).

- **D — APIs enabled so a fresh project applies (#13).** No
  `google_project_service`, so a fresh project's apply failed before creating
  anything. Restores **invariant 8** (new): the required services
  (bigquery/storage/iam/sts/iamcredentials/cloudbilling/billingbudgets) are
  enabled with `disable_on_destroy = false`, and the modules `depends_on` them.
  Rejected: a `gcloud services enable` runbook step (leaves the plan incomplete;
  "nothing created outside Terraform").

## Amendments (review round 2, approved 2026-08-29 — cap invoked)

Round 2 found that round 1's `test_infra.py` hardening pinned substrings /
mechanisms, so a dozen `.tf` properties survived mutation. **The cap was invoked**:
`test_infra.py`'s static checks were **re-implemented once** against one invariant
— *every property in the Invariants table has a test that reddens when that
property is removed from the `.tf`; pins are exact-string / scoped / allowlist,
never a substring or a resource-type denylist* — instead of patching each gap. A
scoped round 3 follows. Three design changes rode with it:

- **E — the managed bucket is derived, never a variable (#1 BLOCKER).** Round 1's
  `var.staging_bucket` could be set to `<project>-tfstate`, reinstating the BLOCKER
  (Terraform managing/destroying its own state) with the suite green. The var is
  **removed**; the module bucket is always `${project_id}-ontime`, and a test
  reddens if any managed `.tf` names `tfstate`. Restores invariant 5. Rejected: a
  cross-var `validation` (a footgun kept behind a guard is worse than no footgun).

- **F — WIF impersonation binds on the combined `repo@ref` (#2, #3, #4).** The
  binding was repo-only (ref lived solely in the provider condition), the test
  checked two substrings not the `&&`, and the vars were interpolated raw into
  CEL. Now: a mapped `attribute.repo_ref = repository + "@" + ref`, the member
  binds on it, the test asserts the exact `&&` conjunction and the combined
  binding, and `github_repository`/`github_ref` carry shape `validation`s
  (`TF_VAR_…='x" || true'` is rejected). Refines invariant 7.

- **G — the two bootstrap APIs (#5).** `google_project_service` and
  `data.google_project` themselves need `serviceusage` + `cloudresourcemanager`;
  a brand-new project can't even plan without them. They join `required_services`,
  and a one-time `gcloud services enable` is documented; invariant 8 / Done-when 1
  now say "with the two bootstrap APIs on". This is the irreducible manual step
  the "fresh project applies" clause always implied.

The 15 test/wording fixes (including #16 `_NO_BINARY`→`None` sentinel, #11 argv +
`-input=false`, #15 positive-threshold validation, #14 `.terraform` prune, #10
resource allowlist) landed without amendments.

**Accepted, not fixed:** #18 — `require_confirm`'s origin is a caller-supplied
string, but this is the **sanctioned repo-wide `$(origin CONFIRM)` pattern** (every
CONFIRM gate — `freeze`, `drop-db`, `dbt-build` — trusts make's closed origin
word-set; the threat model is "mistakes, not a user who controls the environment
or calls Python directly", DECISIONS Phase 0). No change.

## Amendments (review round 3, approved 2026-08-29 — scoped re-review)

Round 3 killed 9 of round 2's 10 survivors; one design change was missed in
every earlier round (the security-reviewer's durable fix, over a tfvars comment):

- **H — CI WIF is opt-in: `enable_ci_wif` (default false) count-gates the pool,
  provider and impersonation binding (#9).** `github_repository` defaults to
  THIS repo, so a fork's default apply built a WIF trust letting *this* repo's
  `main` impersonate *their* service account. Now the three WIF resources carry
  `count = var.enable_ci_wif ? 1 : 0`, so a default apply creates **no
  cross-repo trust**; CI setup flips the toggle deliberately, with its own
  `github_repository`. The least-privilege SA still exists unconditionally (9b's
  `bq load` uses it under ADC). Restores **invariant 2**'s "a default apply
  creates only what the operator asked for" and extends **invariant 7**: the
  trust is branch-scoped *and* opt-in. Rejected: a tfvars-comment warning (not a
  control); defaulting `github_repository` empty (breaks "project_id only" and
  invariant 1).

The genuine fixes (#2 `issuer_uri` pin, #7 the `tfstate` check scoped to managed
blocks so the documented backend block can be uncommented, #12 `*.tfvars.json`
ignored + scanned, #13 `project_id` shape `validation` in HCL), the seven
test-cluster completions and the five record fixes land without amendments.
**Gate item (#8) discharged 2026-08-29:** a fresh `make tf-plan` → `tf-apply` →
`tf-destroy` cycle on the post-H tree re-proved Done-when 1, 2 and 5 (Evidence
row 5). It surfaced two live gotchas, fixed in the tree and logged in
ARCHITECTURE §8: user ADC needs a quota project for `billingbudgets` (provider
`user_project_override` + `billing_project`), and a budget's currency must be
the billing account's (`data.google_billing_account.currency_code`).

## Amendments (review round 4, approved 2026-08-29 — phase-exit audit)

Round 4 (the whole-repo phase-exit union) found five design changes; the rest
are fixes and record corrections landing without amendments.

- **I — Two datasets stays the pin; 9b's dbt build must land inside them (#1,
  #18).** Restores **Done-when 5** ("none created out of band") and the "two
  datasets" pinned decision. Terraform creates exactly `raw` and `ontime`; the
  SA's dataset-scoped `dataEditor` cannot create a dataset, so an out-of-band
  dataset is impossible for the pipeline identity by IAM, not by convention
  (the operator's own ADC can — Amendment N) — 9a pins by name that no role
  in the tree grants `bigquery.datasets.create` (`dataOwner`/`admin` stay off the
  allowlist). `dbt_project.yml`'s per-folder `+schema` would otherwise make the
  first BigQuery build create `ontime_staging … ontime_scores` (five datasets,
  US multi-region) that Terraform never creates and destroy never removes. The
  9b reconciliation commit therefore MUST add `generate_schema_name` so on the
  `bigquery` target every model's custom schema resolves to `models_dataset` (no
  `ontime_<folder>` suffix); whether DuckDB keeps its per-folder schemas or
  collapses too is 9b's call (collapsing touches every schema-qualified reader
  in `serving/`, `eval/`, tests). A DUE BACKLOG row carries this, trigger "Phase
  9b spec reconciliation". Second clause of the same row: **`test-int-bigquery`
  needs `enable_ci_wif = true` applied explicitly** — a default apply builds no
  WIF (H) — so 9b's spec names that opt-in apply as a step; DECISIONS' "9a
  stands up the WIF" is corrected to "provides it behind the toggle".
- **J — The WIF provider name is a root output, and its null-guard is pinned
  (#5, #2).** Restores **invariant 1** (a default plan is clean) and makes H
  usable: `infra/outputs.tf` gains `workload_identity_provider` =
  `module.iam.workload_identity_provider`, the value `google-github-actions/auth`
  takes; the module output is `var.enable_ci_wif ? …[0].name : null` and the
  root output is a bare passthrough of it (the null propagates).
  `tests/test_infra.py::test_wif_output_is_null_guarded` pins the conditional
  on the module and the passthrough on the root (drop either → red; the
  tester's surviving hand-mutation). No
  plan-based check — offline is the contract, and a real plan needs the google
  provider's data sources.
- **K — No default trusted repository (#6).** Restores **invariant 7** (trust
  is opt-in) without prose coupling: `github_repository` defaults `null`
  (invariant 1 holds — it still has a `default =`), and the pool resource
  carries `lifecycle { precondition { condition = var.github_repository != null
  } }`, so `enable_ci_wif = true` without a repo fails at plan with a named
  message instead of trusting this repo's `main` on a fork.
  `test_stated_defaults_are_pinned` pins `null`; the shape `validation` stays for
  the non-null case; `terraform.tfvars.example` and DEPLOYMENT set the pair
  together. Rejected: a precondition against a hard-coded repo name (a fork
  would carry the name it is meant to reject).
- **L — Thresholds are in the billing account's currency; the variable says so
  (#23).** `budget_alert_thresholds_usd` → `budget_alert_thresholds` (the
  module's var was already `alert_thresholds`); every record reads "50 / 150 in
  the billing account's currency ($50/$150 on a USD account)". Restores the
  record ↔ code agreement the currency fix (`5d08729`) broke.
- **M — No tracked Claude settings file (#9).** Restores CLAUDE.md's rule that
  hook wiring lives only in the gitignored `settings.local.json`: `.claude/
  settings.json` (an empty `{}`) is untracked and gitignored, and
  the Claude-config pin (round 5's key scan; re-implemented in round 6 as
  `tests/test_infra.py::test_tracked_claude_config_is_prose_and_hook_scripts_only`,
  a path allowlist) asserts no tracked `.claude/settings*.json` exists at all
  and no `.mcp.json` is tracked (round 5 #12 widened it from `hooks` alone). Repo hygiene outside
  9a's Scope, landed here as a security finding at the phase exit (one-line
  diff) rather than on a `fix/` branch.

## Amendments (review round 5, approved 2026-08-29 — confirmation round)

- **N — Amendment I's IAM claim narrowed to the pipeline identity (#6).**
  Restores **Done-when 5** ("none created out of band") as a control over every
  path, not a convention. "Impossible by IAM" holds for the SA only: no role in
  the tree grants `bigquery.datasets.create`
  (`tests/test_infra.py::test_no_role_can_create_a_dataset`). An operator
  running `make dbt-build TARGET=bigquery CONFIRM=yes` on their own ADC is
  project Owner and CAN create `ontime_<folder>` datasets out of band until 9b
  lands `generate_schema_name`. 9a therefore (a) rewrites the claim in this
  spec, DECISIONS, the DUE BACKLOG row, `docs/DEPLOYMENT.md` and
  `docs/PHASES.md` as "the SA cannot; the operator can"; (b) DEPLOYMENT tells
  the operator not to run a BigQuery build before 9b, and that 9b's manual
  builds impersonate the SA (`gcloud auth application-default login
  --impersonate-service-account=<sa>`), so the IAM control covers the human
  path too. No `.tf` change, no new resource. Rejected: removing the operator's
  `datasets.create` (Owner is the bootstrap role; DEPLOYMENT's permissions
  table already names it).

- **O — Two more clauses on the DUE 9b row: dataset location and the project
  env var (#10, #11).** Record-only; `dbt/profiles.yml` is Phase 2 code and
  the `bigquery` target is 9b's surface, so 9a names the defects and 9b fixes
  them in its reconciliation. (1) Terraform pins both datasets to
  `location = var.region` (`us-central1`) but the `bigquery` output sets no
  `location`, so dbt-bigquery defaults to the US multi-region and the first
  build fails "Dataset … not found in location US" — 9b sets
  `location: us-central1` from the same value Terraform uses. (2) The output
  reads `env_var('OTR_GCP_PROJECT', '')`, which nothing sets or documents — 9b
  has `make dbt-build TARGET=bigquery` set it from the validated `PROJECT`
  (the `PROJECT_RE` gate `infra/cli.py` uses), never from an unvalidated
  environment; an empty value is a refusal. Both clauses go on the DUE
  BACKLOG row, DECISIONS (round 5) and PHASES' 9a "Delivered" paragraph (9b's
  reconciliation moves them onto its own line). Rejected: fixing
  `profiles.yml` here (mixes phases; no BigQuery build can run until 9b).

## Amendments (review round 6, approved 2026-08-29 — pin closure)

- **P — The `.tf` tree is pinned by a content manifest, not by one property
  at a time (#3, #4, #5; the round-2 test-design invariant applied to the
  HCL).** Restores **invariant 2/4/5's falsifiability**: "for all edits to
  `infra/**/*.tf` or `infra/.terraform.lock.hcl`, the offline suite is red
  until the manifest is deliberately rewritten." Three rounds of hand-mutation
  each found 3–5 unpinned attributes (threshold denominator, `budget_filter`,
  IAM `member`, …) because a property-by-property pin is an unbounded
  denylist over ~150 attributes. `infra/MANIFEST.sha256` (the
  `fixtures/` format — `generator/manifest.py` `render`/`parse`, paths
  relative to `infra/`, `.terraform/` and untracked `*.tfvars`/`*.tfstate*`
  outside its glob) is asserted byte-for-byte by
  `tests/test_infra.py::test_tf_tree_matches_manifest`; its ONLY writer is
  `make tf-freeze CONFIRM=yes` (`infra/cli.py freeze`, `$(origin CONFIRM)`
  like `freeze`; the `.tf` diff itself is the review, so no DECISIONS entry
  per re-freeze — the manifest hunk in the same commit is the declaration).
  The semantic pins stay for readability and add the three named attributes
  (`threshold_percent = … / local.budget_amount`, `budget_filter.projects` =
  the project number, every non-WIF IAM `member` = the SA email); the
  manifest, not the pins, is the mutation gate. Rejected: `python-hcl2`
  (a package — STOP-and-ask, and still a property list); a `terraform
  show -json` golden (needs init + provider, not offline).

- **Q — Operator impersonation is a real grant (#8, #9, #10).** Restores
  Amendment N's claim as a **control**: the human BigQuery path from 9b on
  runs as the SA, and the permission that makes impersonation possible
  (`iam.serviceAccounts.getAccessToken`) is Terraform-managed, not assumed.
  New root var `operator_principal` (`default = null`; shape
  `^(user|group|serviceAccount):[^\s,]+$` validated — interpolated into a
  member) threads into `modules/iam`; when set, one count-gated
  `google_service_account_iam_member.operator_token_creator` grants
  `roles/iam.serviceAccountTokenCreator` **on the SA** (resource-scoped, not
  project-level) to that principal. Default null → zero resources, so the
  default plan stays `18 to add` and invariant 1 holds (`null` is a default).
  DEPLOYMENT's permissions table gains the row; the "so the human path is
  under the same IAM" sentence becomes "with `operator_principal` set, the
  operator can impersonate the SA; a build on raw Owner ADC is the operator's
  choice and outside the control" — no overclaim. `roles/iam.
  serviceAccountTokenCreator` joins the role allowlist as the one role granted
  ON the SA rather than TO it (`test_sa_roles_are_least_privilege` splits the
  two). Rejected: documenting the permission only (#9 — a convention, the
  finding); granting it to the project's Owner group (no such group exists;
  a named principal is least privilege).

- **R — The manifest is the allowlist's closure (round 7 #1, #3, #4, #5, #6).**
  Restores invariant 9 (the pinned `.tf` tree is one allowlist). Round 7 showed
  four holes in P's gate: the assertion function had no mutation line (a
  neutered `manifest_diff` stays green), Terraform also loads `*.tf.json`
  outside the glob, a platform-dependent `init` can rewrite the pinned lock
  file, and `freeze` silently drops a vanished file. Mechanism: (a)
  `infra/cli.py` drops its own `manifest_diff` and calls
  `generator.manifest.diff` over a path predicate covering `*.tf`, `*.tf.json`
  and `.terraform.lock.hcl` (pruning `.terraform/`) — one diff implementation,
  one glob, shared by `pinned_files`, `freeze` and the test; (b) `tf-validate`
  runs `init -backend=false -input=false -lockfile=readonly`, so no platform
  can mutate the pin (a missing platform hash is a reported FAIL, fixed by a
  deliberate `terraform providers lock` + `tf-freeze`); (c) `freeze` refuses
  when a path the committed manifest lists is absent on disk (the `make
  freeze` rule), printing the missing paths; (d) the mutations block gains
  `manifest_diff constant-return:[]` and `pinned_files constant-return:[]`,
  `test_tf_tree_matches_manifest` asserts directly on the diff list, and a
  scratch-tree test proves an extra `x.tf.json` reads `extra`. Rejected:
  keeping the local copy with a mutation line only (fixes #1, leaves #3–#6);
  ignoring `*.tf.json` via `.gitignore` (a tracked one would still apply).
  Scope: `infra/cli.py`, `tests/test_infra.py`, this spec, a re-freeze of
  `infra/MANIFEST.sha256` carrying #9 (`group:` dropped from
  `operator_principal`'s shape) and #25 (the budget comment reflow).

- **S — No BigQuery build before 9b (round 7 #7).** Restores Done-when 5 and
  Amendment N: the docs claimed `TARGET=bigquery` was not runnable before 9b,
  but `dbt/profiles.yml` only defaulted `OTR_GCP_PROJECT` to `''`, so an
  exported value gave a runnable build that creates per-folder datasets
  outside Terraform. Mechanism: `loader/cli.py dbt-build` refuses
  `TARGET=bigquery` outright (`dbt-build: TARGET=bigquery lands in Phase 9b
  (generate_schema_name)`), before `load()` and before any dbt call;
  `profiles.yml` keeps `env_var('OTR_GCP_PROJECT')` without a default so a
  missing export is a dbt parse error too. 9b's reconciliation lifts the
  refusal in the same commit that adds `generate_schema_name` (the DUE BACKLOG
  row gains this clause). Test: `TARGET=bigquery CONFIRM=yes` exits 1 with
  that line and no runner call. Rejected: a doc-only correction ("runnable,
  but don't") — Done-when 5's "cannot appear" would stay a claim, not a
  property.

## Teaching notes (first appearance in this project)

- **Terraform state, modules, and backends.** Terraform records what it created
  in a *state* file so it can compute the diff between the config and reality on
  the next run; a *backend* is where that state lives (local file by default, or
  a shared GCS bucket so a team and CI see one truth). A *module* is a reusable
  folder of resources called with inputs — here one module per cloud concern
  (`bigquery`, `gcs`, `iam`, `budget`, and the toggled `composer`/`spanner`), so
  a concern can be enabled, reviewed, or destroyed on its own.

- **Workload Identity Federation vs service-account keys.** A service-account
  *key* is a long-lived JSON secret: leak it and anyone is that account until it
  is revoked — which is why it is never committed. *WIF* instead lets an external
  identity (a GitHub Actions run, or a local `gcloud` login) exchange a
  short-lived OIDC token for a scoped, minutes-long GCP credential, with no
  secret at rest. This project authenticates by ADC (local `gcloud`) and WIF (CI)
  only.

- **IAM least privilege.** IAM grants an identity *roles* on a resource. Least
  privilege means the pipeline's service account gets exactly the roles it uses —
  read/write the two BigQuery datasets, run BigQuery jobs, read/write the bucket
  — and never a broad `roles/owner` or `roles/editor` that would let a compromised
  credential touch the whole project. The grant is scoped to the dataset/bucket,
  not the project, wherever the API allows it.

- **Budget alerts vs a billing kill-switch.** A GCP *budget* is a monitoring
  object: it emails (or publishes to Pub/Sub) when spend crosses a threshold, but
  it does **not** cap or stop spend — a runaway job keeps billing past $150. The
  only thing that actually stops spend is disabling billing on the project, which
  a Pub/Sub-triggered Cloud Function can do; that kill-switch is documented as the
  optional real guardrail and left unbuilt here (the meter is off by default, so
  there is nothing to run away yet).

## Why

Phases 0–8 run entirely on a laptop (DuckDB, local Airflow). Phase 9 is the
first cloud phase, and 9a is its foundation: the Terraform that provisions the
BigQuery datasets, bucket, service account, and budgets 9b will build against —
all behind toggles that keep nothing billable up by default, with `project_id`
the only input a fresh clone needs. A fix PR cannot carry it: it is a new
top-level `infra/` tree, new cloud-cost and destructive `make` targets, and the
IAM / budget / WIF surface that makes the security review mandatory.

## The central constraint

**Nothing is billable by default, and nothing needs a secret to plan.**
`terraform plan` runs from a fresh clone with only `project_id`; every
`enable_*` toggle defaults false, so a default plan creates zero Spanner /
Composer resources; no service-account key, tfstate, or tfvars is ever tracked
(ADC / WIF only); and `terraform destroy` returns the project to zero billable
resources. A required var beyond `project_id`, a committed secret, a toggle
defaulting true, or a billable resource outside a toggle is a STOP.

## DONE command

```
make test && make lint && make review-gate SPEC=specs/phase-9a-gcp-foundation.md && make tf-validate
```

- `make test` — the offline suite, including `tests/test_infra.py` (static
  checks over the `.tf` tree: `enable_*` default false, `project_id` the only
  required var, no tracked secret/state/tfvars, SA roles least-privilege,
  toggled modules `count`-gated) and `tests/test_makefile.py` (the four tf
  targets' literal quoting and `$(origin)` gates). No terraform binary, no
  network.
- `make lint` — ruff over `infra/cli.py` and the tests.
- `make review-gate SPEC=…` — the offline gate + this spec's Evidence ids and
  Record-updates files; security-reviewer surface flagged.
- `make tf-validate` — `terraform -chdir=infra init -backend=false && validate
  && fmt -check -recursive`: config is syntactically valid and canonically
  formatted. Downloads the google provider once from the registry (a setup step,
  outside the offline `make test`); no GCP auth, no cloud call.

The **plan-clean** and **destroy-leaves-nothing-billable** Done-when items are
proven by the manual cloud steps in Evidence (`make tf-plan` / `tf-apply` →
`tf-destroy`), run by the developer against their project — ask-first, every
time, like every cloud-cost command.

## Done-when

1. **Plans from `project_id` alone.** From a fresh clone, `make tf-plan
   PROJECT=<id>` runs clean with no other required input; every var except
   `project_id` has a default (a brand-new project first needs the two bootstrap
   APIs enabled once — `docs/DEPLOYMENT.md`). *Evidence: row 1.*
2. **Meter off by default.** With `enable_composer` and `enable_spanner` at their
   false defaults, the plan creates zero Composer / Spanner resources; both
   modules are `count`-gated. With `enable_ci_wif` at its false default, the plan
   creates no WIF pool/provider/binding (no cross-repo trust — H). *Evidence:
   row 2.*
3. **No secret in the tree.** No service-account key, `*.tfstate`, or `*.tfvars`
   is tracked; auth is ADC / WIF (`method` is oauth/WIF, never a keyfile path).
   *Evidence: row 3.*
4. **Least privilege.** The pipeline service account holds only BigQuery
   data/job roles on the two datasets and object-admin on the bucket — never
   `roles/owner` or `roles/editor`. *Evidence: row 4.*
5. **Destroy leaves nothing billable.** After `make tf-apply` then `make
   tf-destroy`, no dataset, bucket, service account, or budget remains; every
   billable resource is Terraform-managed — the SA cannot create one out of
   band (an operator's own ADC can; N, Q). *Evidence: row 5.*
6. **Cloud/destructive targets gated.** `tf-apply` and `tf-destroy` require
   `CONFIRM=yes` from the command line; `PROJECT` is validated before any
   terraform runs; empty / `../x` / `"; ` values are refused. *Evidence: row 6.*

(≤ 6. `docs/PHASES.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_infra.py::test_project_id_is_the_only_required_var` (brace-matched: exactly one variable without a `default =`), `::test_required_apis_are_enabled_and_survive_destroy`, `::test_modules_depend_on_the_service_enablement`, `::test_stated_defaults_are_pinned` (round 4: `github_repository` default `null` + the pool precondition — K), `::test_wif_output_is_null_guarded` (J as corrected in round 5: the MODULE output is `enable_ci_wif ? … : null`, the root output a bare passthrough, so a default plan never indexes an empty tuple); manual `make tf-plan PROJECT=<id>` (with the two bootstrap APIs on) → a clean plan, no prompt for a second var |
| 2 | `tests/test_infra.py::test_enable_toggles_default_false`, `::test_optional_modules_are_count_gated`, `::test_every_declared_resource_type_is_on_the_allowlist` (ANY provider's type — a `google_spanner_instance`/`null_resource` at root → red), `::test_required_providers_is_hashicorp_google_only`, `::test_ci_wif_is_opt_in_and_count_gated` (H: drop the `count` from any of the three WIF resources → red), `::test_every_data_source_type_is_on_the_allowlist` (round 4 #7: `google_project`, `google_billing_account` exactly), `::test_no_role_can_create_a_dataset` (I: no `dataOwner`/`admin`/`user`/owner/editor anywhere; exactly two `google_bigquery_dataset` blocks), `::test_budget_amount_is_the_smallest_threshold` (`min` → `max` red), `::test_budget_currency_is_the_billing_accounts` (`currency_code` from the data source, never a literal), `::test_budget_scope_and_threshold_denominator_are_pinned` (round 6 #3/#4: `budget_filter.projects` = this project; `/ local.budget_amount`, not `/ 100`); `::test_tf_tree_matches_manifest` (P — every attribute, via the content manifest); manual `make tf-plan` → `0 to add` for the toggled modules and no WIF resource |
| 3 | `tests/test_infra.py::test_no_tracked_secret_state_or_tfvars` (`git ls-files` matches no `*.tfstate*`/`*.tfvars`/private-key filetypes; EVERY tracked text file **content-scanned** for a SA-key body — a `type` of `service_account`, a `private_key` member, a PEM `PRIVATE KEY` header, whitespace-insensitive so a minified key matches (round 4 #8) — the content scan, not the name, is what catches a gcloud-shaped key), `::test_auth_is_adc_or_wif_never_keyfile` (also pins the provider's `user_project_override` + `billing_project = var.project_id` — the quota project, §8), `::test_tracked_claude_config_is_prose_and_hook_scripts_only` (M, re-implemented round 6 #1/#2/#6/#7/#21 as a PATH allowlist: only `.claude/{agents,commands}/*.md` and `.claude/hooks/*.py` may be tracked — a tracked settings*.json / `.mcp.json` / lock file is red whatever it contains, and each is ignored by the repo's OWN `.gitignore`, `core.excludesFile` disabled), `::test_wif_provider_condition_is_the_repo_and_ref_conjunction` (also pins `issuer_uri` and the `repo_ref` mapping composition), `::test_wif_impersonation_binds_on_combined_repo_and_ref` (exact `${repo}@${ref}` member); `*.tfvars.json`/`*.auto.tfvars*` gitignored and scanned |
| 4 | `tests/test_infra.py::test_sa_roles_are_least_privilege` (roles in ANY `.tf` ⊆ allowlist; owner/editor absent), `::test_project_level_grant_is_only_bigquery_jobuser` (whole tree: the only `google_project_iam_member` role anywhere is `jobUser`; `objectAdmin` bucket-scoped, `dataEditor` dataset-scoped — a project-wide move → red), `::test_every_grant_member_is_pinned` (round 6 #5: every grant TO the SA has `member` = the SA; the grants ON it bind the exact WIF principalSet or the validated `operator_principal`, count-gated — Q; `allAuthenticatedUsers` anywhere → red) |
| 5 | manual cycle **2026-08-29 on `ontime-rate-recovery` (post-Amendment-H tree, `71e30ce`)**: `make tf-plan` → `Plan: 18 to add, 0 to change, 0 to destroy` (9 API enablements, 2 datasets, 1 bucket, 1 SA + 4 scoped grants, 1 budget — **no WIF pool/provider/binding, no Composer/Spanner**); `make tf-apply … CONFIRM=yes` → `Apply complete! Resources: 18 added` (after two live fixes: quota project, budget currency — §8 Gotchas); `bq ls` = `ontime`,`raw`; buckets = `ontime-rate-recovery-ontime`; SA = `ontime-pipeline@…`; `gcloud iam workload-identity-pools list` = **empty**; budget `ontime-ontime-rate-recovery`; `make tf-destroy … CONFIRM=yes` → `Destroy complete! Resources: 18 destroyed`; state list 0; `bq ls` / buckets / SAs / WIF pools / our budget all **empty** (the API enablements stay on, `disable_on_destroy = false`, free); `tests/test_infra.py::test_every_resource_is_destroyable`, `::test_bucket_is_hardened`, `::test_no_staging_bucket_variable_and_the_managed_bucket_is_derived` (round 4 #4: header labels of managed blocks scanned too), `::test_region_and_dataset_location_are_us_central1`. **Live consequence:** this destroy reserved the `ontime-pipeline` SA id on that project until ~2026-09-28 (DEPLOYMENT). **Re-planned on the post-J/K/L tree (round 5 #5, `a8d2967`, read-only):** `make tf-plan PROJECT=ontime-rate-recovery` → `Plan: 18 to add, 0 to change, 0 to destroy`, the same 18 resources, no WIF/Composer/Spanner, and the outputs diff lists `models_dataset`, `pipeline_service_account`, `raw_dataset`, `staging_bucket` only — `workload_identity_provider` is null on a default plan (J's guard, live). Not re-planned since: `623a05a` changed `infra/` comments only, and round 6 (Q) added one resource count-gated on `operator_principal = null` (`0 : 1`) — `18 to add` holds by construction; a live re-plan is 9b's first `tf-plan` |
| 6 | `tests/test_makefile.py::test_tf_apply_and_destroy_confirm_from_command_line_only` (fake runner), `::test_tf_targets_pass_project_as_one_literal`; `tests/test_infra.py::test_cli_validates_project`, `::test_cli_requires_confirm_origin`, `::test_cli_validates_before_running`, `::test_cli_builds_the_expected_argv`, `::test_cli_missing_terraform_is_a_clean_fail` (FAIL lines asserted), `::test_cli_validate_argv_is_offline` (`-backend=false`); `::test_project_id_validation_mirrors_the_cli_regex` (the HCL `validation` equals `PROJECT_RE`, so a tfvars/direct apply is shape-checked too), `::test_input_shape_validations_exist`; `::test_tf_freeze_requires_confirm_origin_and_writes_the_manifest` + `tests/test_makefile.py::test_tf_freeze_confirm_from_command_line_only` (P: the manifest's only writer is CONFIRM-gated); mutations `require_confirm invert-guard`/`delete-call`, `validate_project invert-guard`, `tf constant-return:0` all KILLED |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Only `project_id` is required.** For all fresh clones, every ROOT Terraform variable (`infra/variables.tf` — module vars are wired from the root, never user input) except `project_id` has a `default =` assignment, so a plan needs only that one input (brace-matched, so a nested `validation {}` can't hide a required var; keyed on the assignment, so a description containing "default" doesn't read as one). | `tests/test_infra.py::test_project_id_is_the_only_required_var` (a second default-less var → red) |
| 2. **Meter off by default; a default apply creates only what was asked for.** For all default applies, no billable resource is created outside the count-gated composer/spanner modules and no WIF trust outside the count-gated `enable_ci_wif` resources; `enable_*` default false, the gated modules/resources are `count = var.enable_* ? 1 : 0`, every declared resource type AND data-source type (any provider) is on an explicit allowlist, the only provider is `hashicorp/google ~> 6.0`, exactly two datasets exist and no role granted TO the SA can create another (I; the operator's ADC is outside this control — N), the budget filters on this project with `threshold_percent = value / amount`, and the budget's amount is `min(thresholds)` in the billing account's currency. | `tests/test_infra.py::test_enable_toggles_default_false`, `::test_optional_modules_are_count_gated`, `::test_ci_wif_is_opt_in_and_count_gated`, `::test_every_declared_resource_type_is_on_the_allowlist` (a `google_spanner_instance`/`null_resource` at root → red), `::test_every_data_source_type_is_on_the_allowlist`, `::test_required_providers_is_hashicorp_google_only`, `::test_no_role_can_create_a_dataset`, `::test_budget_amount_is_the_smallest_threshold`, `::test_budget_currency_is_the_billing_accounts`, `::test_budget_scope_and_threshold_denominator_are_pinned`; manual `tf-plan` zero toggled resources |
| 3. **No secret at rest.** For all commits, no tfstate/tfvars/`tfvars.json`/private-key filetype is tracked (and Terraform's auto-loaded `*.auto.tfvars*`/`*.tfvars.json` are gitignored), and EVERY tracked text file is content-scanned for a SA-key body — whitespace-insensitive `type`/`service_account`, a `private_key` member, a PEM `PRIVATE KEY` header (the **content scan**, not the filename, is what catches a gcloud-shaped `<project>-<keyid>.json` or a key pasted into a `.md`); no `.tf`/`profiles.yml` sets a `credentials`/`keyfile` argument; auth is ADC/WIF, and the provider sends `project_id` as the quota project (`user_project_override` + `billing_project`) so user ADC needs no per-machine step; the only tracked paths under `.claude/` are `{agents,commands}/*.md` and `hooks/*.py` — an allowlist, so any tracked settings*.json / `.mcp.json` / lock file is red whatever its content — and each local-only Claude Code file is ignored by the repo's own `.gitignore` (M; re-implemented round 6). | `tests/test_infra.py::test_no_tracked_secret_state_or_tfvars`, `::test_auth_is_adc_or_wif_never_keyfile`, `::test_tracked_claude_config_is_prose_and_hook_scripts_only` |
| 4. **Least privilege, scoped.** For all IAM role grants in ANY `.tf`, the SA gets only the BQ data/job + bucket-object + WIF roles, never owner/editor; the only PROJECT-level grant is `bigquery.jobUser` — `objectAdmin` is bucket-scoped, `dataEditor` dataset-scoped; every grant TO the SA names the SA as `member`, and the two roles ON the SA (`workloadIdentityUser`, `serviceAccountTokenCreator` — Q) appear only in `google_service_account_iam_member` blocks bound to the exact WIF principalSet / the validated `operator_principal`. | `tests/test_infra.py::test_sa_roles_are_least_privilege` (a `roles/owner` at root → red), `::test_project_level_grant_is_only_bigquery_jobuser` (a project-wide `objectAdmin` → red), `::test_every_grant_member_is_pinned` (`allUsers` → red) |
| 5. **Destroy is total; the managed bucket is not the state bucket.** For all applied resources, `terraform destroy` removes them — no `prevent_destroy`, datasets `delete_contents_on_destroy`, bucket `force_destroy` + hardened (public-access-prevention, versioning, lifecycle); the managed bucket is derived `${project_id}-ontime` (never a var), and no MANAGED block (`resource|data|module|variable|output|locals` — header labels included, not the `terraform {}` backend block, which may legitimately name it) mentions `tfstate`. | `tests/test_infra.py::test_every_resource_is_destroyable`, `::test_bucket_is_hardened`, `::test_no_staging_bucket_variable_and_the_managed_bucket_is_derived`; manual apply→destroy→empty listing |
| 6. **Cloud/destructive targets gated, validated first, correct argv — and the HCL re-validates.** For all `tf-apply`/`tf-destroy`, `CONFIRM=yes` from the command line is required; `PROJECT` is validated before the runner runs; the argv carries `-var project_id=…` + `-input=false` (+ `-auto-approve` for the mutating pair); `tf-validate`'s init carries `-backend=false`; (injected fake runner) no test spawns real terraform; and `variable "project_id"` carries the same shape as a `validation`, so a tfvars / direct `terraform apply` cannot bypass it (#13). | `tests/test_infra.py::test_cli_requires_confirm_origin`, `::test_cli_validates_project` (rejects `my-proj\n`), `::test_cli_validates_before_running`, `::test_cli_builds_the_expected_argv`, `::test_cli_validate_argv_is_offline`, `::test_cli_missing_terraform_is_a_clean_fail`, `::test_project_id_validation_mirrors_the_cli_regex`; `tests/test_makefile.py::test_tf_apply_and_destroy_confirm_from_command_line_only` |
| 7. **WIF trust is opt-in and branch-scoped, at both the provider and the binding.** For all CI token exchanges, the WIF resources exist only under `enable_ci_wif = true` (H) AND a named `github_repository` (K — no default repository; the pool's precondition refuses the toggle without one); the provider name reaches the operator as the root `workload_identity_provider` output, null otherwise (J); the provider's issuer is GitHub's OIDC endpoint exactly, its `attribute_condition` requires repo AND ref (`&&`, not `||`), `attribute.repo_ref` is composed from both claims, and the impersonation binds on the exact `attribute.repo_ref/${repo}@${ref}` — not repo-only. | `tests/test_infra.py::test_ci_wif_is_opt_in_and_count_gated`, `::test_stated_defaults_are_pinned` (a default repo slug → red; the precondition dropped → red), `::test_wif_output_is_null_guarded`, `::test_wif_provider_condition_is_the_repo_and_ref_conjunction` (`&&`→`||` red; issuer → `evil.example.com` red), `::test_wif_impersonation_binds_on_combined_repo_and_ref` (widening to `attribute.repository/*` red). **Static-pinned only**: no WIF resource has been applied (Evidence row 5 built none); the first live proof that the slash-bearing `attribute.repo_ref/<owner>/<repo>@refs/heads/main` member binds as written is 9b's opt-in apply (the DUE row) |
| 8. **A fresh project applies (with the two bootstrap APIs on).** For all required APIs, `google_project_service` enables them (`disable_on_destroy = false`) and the modules `depends_on` it; the two bootstrap APIs (`serviceusage`, `cloudresourcemanager`) that `google_project_service`/`data.google_project` themselves need are a documented one-time `gcloud services enable` on a brand-new project. | `tests/test_infra.py::test_required_apis_are_enabled_and_survive_destroy`, `::test_modules_depend_on_the_service_enablement` |
| 9. **The `.tf` tree is one allowlist.** For all edits to any file Terraform loads under `infra/` (`*.tf`, `*.tf.json`) or `infra/.terraform.lock.hcl` (which `tf-validate` may never rewrite: `-lockfile=readonly`), the offline suite is red until `infra/MANIFEST.sha256` is rewritten by `make tf-freeze CONFIRM=yes` (its only writer, `$(origin)`-gated, refusing a vanished pinned file) — so every attribute is mutation-lethal, not only the ones a property pin names (P, R). | `tests/test_infra.py::test_tf_tree_matches_manifest` (any byte of any `.tf` → red), `::test_tf_freeze_requires_confirm_origin_and_writes_the_manifest`, `tests/test_makefile.py::test_tf_freeze_confirm_from_command_line_only` |

Rules — the Terraform HCL is configuration no mutation operator addresses (the
four Python operators act on `.py`; the two SQL operators on `case` arms). It is
pinned by `infra/MANIFEST.sha256` (round 6, P — every byte of every `.tf`, the
mutation gate) plus the static `tests/test_infra.py` property checks (content-
based, whole-tree — re-implemented in round 2 against the pinning invariant;
they document WHICH properties matter),
`terraform validate` in the DONE command, and the manual plan/apply/destroy
Evidence — the same treatment Phase 7 gave SQL predicates.
Every Python guard (`validate_project`, `require_confirm`, the `tf` dispatch)
gets a mutation line; the unmutated suite runs first and must be green. `tf` now
takes an injectable runner, so `require_confirm delete-call` is a SAFE line — the
test's fake runner stands in, and a mutated-away confirm gate is caught by the
missing `SystemExit` without spawning a real `terraform apply`.

```mutations
infra/cli.py::validate_project      invert-guard
infra/cli.py::require_confirm       invert-guard
infra/cli.py::require_confirm       delete-call
infra/cli.py::tf                    constant-return:0
infra/cli.py::manifest_diff         constant-return:[]
infra/cli.py::pinned_files          constant-return:[]
```

Equivalent-mutant / refused exclusions, named up front and verified once at
implementation on a scratch copy (the Phase 6/7 pattern):

- `infra/cli.py::validate_project constant-return:'x'` — REFUSED: `validate_project`
  returns the validated string, and a constant `'x'` is itself a valid
  project-id shape, so no test distinguishes it. `invert-guard` (skip the regex
  check) is the killing operator and is in the block.
- The `tf-plan`/`tf-validate` recipes take `PROJECT` but no `CONFIRM` — they are
  not destructive; the `$(origin)` gate is on `tf-apply`/`tf-destroy` only, and
  both `require_confirm` operators (`invert-guard`, `delete-call`) kill via
  `test_cli_requires_confirm_origin`.

## Pinned decisions (do not re-litigate)

- **`infra/` is one tree, one module per cloud concern, `enable_*` toggles
  default false (reconciliation item 2)** — satisfies invariants 1, 2.
  `main.tf` wires `modules/{bigquery,gcs,iam,budget}` unconditionally (all
  free/near-free — ARCHITECTURE §6) and `modules/{composer,spanner}` behind
  `count = var.enable_* ? 1 : 0`. `region` defaults `us-central1`; the budget's
  billing account and project number are a `google_project` data source on
  `project_id`, so no second required var. Rejected: a flat `main.tf` (a concern
  can't be toggled or destroyed alone); a required `billing_account` var
  (breaks "project_id only").
- **One least-privilege service account + WIF, ADC/WIF only (item 3)** —
  satisfies invariants 3, 4. `modules/iam` grants BQ `dataEditor`/`jobUser` on
  the two datasets and `objectAdmin` on the bucket, and — **only when
  `enable_ci_wif` is true (H)** — a WIF pool/provider whose condition is repo AND
  ref (B) with impersonation bound on the combined `attribute.repo_ref/
  <repo>@<ref>` (F); no key resource, no keyfile path anywhere. Rejected: `roles/editor` on the project (broad); a downloaded SA key
  (a secret at rest — the thing the rule forbids).
- **Two BigQuery datasets, `raw` and `ontime`, at `us-central1`** — the landing
  dataset 9b's `bq load` targets and the models' dataset (`profiles.yml`'s
  `dataset: ontime`). Empty datasets are free to leave up (§6). Rejected: one
  dataset (the source `schema: raw` maps to a distinct dataset; mixing landing
  and models loses the boundary).
- **State backend bootstrap-documented, local by default (item 6)** — satisfies
  invariant 1 (a fresh clone plans with no backend setup). The GCS backend block
  is present but commented; `docs/DEPLOYMENT.md` gives the one-time bootstrap
  (create the versioned bucket, `terraform init -migrate-state`). Rejected: a GCS
  backend required from clone one (a chicken-and-egg the bucket can't resolve in
  its own apply).
- **Budget alerts 50 / 150 in the billing account's currency ($50/$150 on USD);
  the kill-switch documented, not built (item 4)** — `modules/budget` sets
  threshold rules at 50 and 150 (`budget_alert_thresholds`, Amendment L) on a
  monthly amount;
  `docs/DEPLOYMENT.md` documents the optional Pub/Sub → Cloud Function billing
  disable as the real guardrail. Closes BACKLOG "Budget alerts do not stop
  spend". Rejected: building the kill-switch now (nothing billable runs by
  default — no runaway to catch yet).
- **`infra/cli.py` validates `PROJECT`, gates `tf-apply`/`tf-destroy`, and runs
  terraform through an injectable runner; four `make` targets — five with
  `tf-freeze` (item 5, Amendment C; P)** — satisfies invariant 6 (and, via
  `tf-freeze`, invariant 9). Mirrors `loader/cli.py`: one process
  validates `PROJECT` (`^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z` — `\Z`, not `$`, so a
  trailing newline is rejected) before deriving the `-var`, refuses
  `tf-apply`/`tf-destroy` unless `CONFIRM=yes` has command-line origin, and passes
  every terraform invocation through `runner` (default `subprocess.run`) so the
  offline tests use a fake; `tf-validate`/`tf-plan` are ungated (non-destructive).
  Rejected: `terraform` invoked straight from the Makefile (an unvalidated value
  reaches the provider; no `$(origin)` gate); a non-injectable `tf` (the
  destructive path could not be mutation-tested without risking a live apply).

**The review amendments (A–S above; the first eight summarised here, I–S in
their own paragraphs) refine the decisions here** — the
managed bucket is the staging bucket not the state bucket (A) and derived, never
a var (E); the WIF condition is repo+ref (B), the binding is on `repo@ref` with
CEL-injection validations (F), and the whole WIF layer is opt-in behind
`enable_ci_wif` (H); `tf` is runner-injectable (C); a `google_project_service`
set precedes the modules (D) with the two bootstrap APIs documented (G).

## Scope (files)

- `infra/main.tf`, `infra/variables.tf`, `infra/outputs.tf`,
  `infra/terraform.tfvars.example`, `infra/.terraform.lock.hcl` (tracked — the
  provider hash pin `tf-validate`'s init writes; `.terraform/` itself is
  ignored),
  `infra/modules/bigquery/{main,variables,outputs}.tf`,
  `infra/modules/gcs/{main,variables,outputs}.tf`,
  `infra/modules/iam/{main,variables,outputs}.tf`,
  `infra/modules/budget/{main,variables,outputs}.tf`,
  `infra/modules/composer/{main,variables,outputs}.tf` (written, `count`-gated),
  `infra/modules/spanner/{main,variables,outputs}.tf` (written, `count`-gated)
- `infra/cli.py` (`validate_project`, `require_confirm`, `tf` with an injectable
  runner), `infra/__init__.py` — a new package, now a GUARDED pipeline dir
  (dropped from `test_truth_isolation`'s EXEMPT, Phase 8b instruction; its Python
  names no side-file)
- `Makefile` (`tf-validate`, `tf-plan`, `tf-apply`, `tf-destroy`, `tf-freeze`
  (P); add `PROJECT` to the `unexport` list)
- `tests/test_infra.py` (new — content-based whole-tree `.tf` checks +
  `infra/cli.py` unit tests with a fake runner), `tests/test_makefile.py` (the
  five tf targets), `tests/test_truth_isolation.py` (drop `infra` from EXEMPT)
- `docs/DEPLOYMENT.md` (new — bootstrap, cost table, teardown, kill-switch)
- Records: `DECISIONS.md`, `docs/PHASES.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`
  (§6; §8 the SA/WIF 30-day soft-delete, quota-project and budget-currency
  gotchas), `BACKLOG.md`
- `.gitignore` — `*.tfvars.json` (round 3 #12), `.claude/settings.json`,
  `.mcp.json`, `.claude/scheduled_tasks.{lock,json}` (Amendment M + round 6;
  the files themselves are untracked)
- `infra/MANIFEST.sha256` (P — written by `make tf-freeze CONFIRM=yes` only)
- `tests/conftest.py` (the `tests/integration` skip under `OTR_INT`)
- `generator/manifest.py` — `compute_file` (P) and the `select` predicate on
  `compute`/`diff` (R): the fixtures' manifest code is the infra pin's
  implementation, so the two cannot drift. Byte-identical output for the
  fixtures (no predicate → every file). `loader/cli.py` + `dbt/profiles.yml`
  — the pre-9b `TARGET=bigquery` refusal (S)
- Untouched by contract: `dbt/models`, `eval/`, `serving/`, `orchestration/`,
  `fixtures/`, `tests/pins.py`, `pyproject.toml`, `uv.lock`

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 9a entries: `infra/` layout + `enable_*` toggles;
      least-privilege SA + opt-in WIF (ADC/WIF only; superseded-by pointer to B/F/H/J/K);
      two datasets + region; state backend bootstrap-documented; budget 50/150
      in the account's currency (amount = `min`) + kill-switch documented;
      `infra/cli.py` + the five targets (two gated + `tf-freeze`); the review-round entries (A–D,
      E–G, H, I–M, N–O). Note on the in-force
      Infra line (the toggles are real now).
- [ ] `docs/PHASES.md` — Phase 9 "Delivered" paragraph (9a half); Done-when as
      landed for the infra clauses (incl. "with the two bootstrap APIs on" — G)
- [ ] `CLAUDE.md` — Current status; Commands (`tf-validate|tf-plan|tf-apply|
      tf-destroy`); Repo map (`infra/` real); `unexport` list (`PROJECT`); Open
      BACKLOG rows: **15**; Commands `tf-freeze` + the `dbt-build` "not before
      9b" note (round 6)
- [ ] `docs/ARCHITECTURE.md` — §6 (state backend bootstrap, WIF, budget as
      landed); §8 Gotchas: the SA/WIF pool 30-day soft-delete on
      apply→destroy→apply; user ADC's quota project (`user_project_override`);
      the budget's currency is the billing account's
- [ ] `BACKLOG.md` — "Budget alerts do not stop spend" struck (`DONE Phase 9a` —
      documented as optional); "Spanner 90-day trial expiry" re-checked,
      re-deferred (module written, `enable_spanner=false`, no apply); the DUE
      9b row (I, N, O); the four 9b-triggered rows annotated with their 9a
      re-check; a dated row for the `ontime-pipeline` SA-id reservation
      (~2026-09-28); count 14 → 15
- [ ] `docs/DEPLOYMENT.md` — new (bootstrap, cost table, teardown, optional
      kill-switch; Spanner/Composer teardown dates; CI WIF opt-in via
      `enable_ci_wif`); operator permissions table + billing preflight
      (round 4 #10); the 2026-08-29 SA-id reservation (#27); the WIF pair
      (`enable_ci_wif` + `github_repository`) and the root output (J, K);
      no BigQuery build before 9b, then as the SA (N)
- [ ] `.gitignore` — `*.tfvars.json` (Terraform auto-loads it; #12);
      `.claude/settings.json` (M, own why-line)
- [ ] `.claude/agents/code-reviewer.md` — five dispatch macros (#16)
- [ ] `.claude/agents/coherence-auditor.md` — five dispatch macros (#16)
- [ ] `infra/terraform.tfvars.example` — the `enable_ci_wif` opt-in and
      `operator_principal` shown
- [ ] `infra/MANIFEST.sha256` — new (P); `Makefile` `tf-freeze`
- [ ] Spec amendments — none (the phase-9b spec does not exist yet; it is
      finalized after 9a merges, per the predecessor-merged rule; a merged
      phase's spec is never edited from this branch — round 6 reverted the
      Phase 0 note and the PROJECT_BRIEF edit, #13/#14)
- [ ] docs/RESULTS.md, METRICS.md, AB_DESIGN.md — none (no generated block)
- [ ] README — none (no README in the repo)

## Threat model (REQUIRED)

`tf-validate`/`tf-plan` take `PROJECT`; `tf-apply`/`tf-destroy` take `PROJECT`
and `CONFIRM`, in the settled shape (one Python process, `PROJECT` validated
`^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z`, the `-var` derived; `$(call _Q,$(value VAR))`;
`unexport`ed). `PROJECT` never becomes a path — it is passed to `terraform … -var
project_id=<validated>` via a subprocess arg list (no shell), and terraform runs
with `-chdir=infra` (a fixed dir, not user input). `tf-apply` creates cloud
resources and `tf-destroy` deletes them: both `$(origin)`-gated (`CONFIRM=yes`
command-line only), ask-first every time. Cost if run twice: `tf-apply` is
idempotent (Terraform diffs to no-op on a second run — no double spend);
`tf-destroy` is idempotent (nothing left to delete). What `tf-destroy` removes:
every resource in state — the two datasets, the bucket, the SA (+ the WIF
pool/provider/binding only if `enable_ci_wif` was on; + the
`serviceAccountTokenCreator` grant ON the SA only if `operator_principal` was
set — Q), the budget — returning the project to zero billable resources.
`operator_principal` is interpolated into an IAM member: its HCL `validation`
admits one `user:`/`serviceAccount:` principal, no whitespace or comma (no
`group:` — round 7 #9), so a `TF_VAR` cannot smuggle a second member.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make tf-plan PROJECT=<id>` | refused (`PROJECT: refused — [a-z][a-z0-9-]…`) | refused, never a path | one literal, refused | reaches Python, validated the same | n/a — not destructive | `tests/test_makefile.py::test_tf_targets_pass_project_as_one_literal`; `tests/test_infra.py::test_cli_validates_project` |
| `make tf-apply PROJECT=<id> CONFIRM=yes` | `PROJECT` refused; `CONFIRM=` refused | refused | one literal, refused | `CONFIRM=yes` from env ignored (`$(origin)` ≠ command line) | honoured only from the command line | `tests/test_makefile.py::test_tf_apply_and_destroy_confirm_from_command_line_only`; `tests/test_infra.py::test_cli_requires_confirm_origin` |
| `make tf-destroy PROJECT=<id> CONFIRM=yes` | same as apply | refused | one literal, refused | env `CONFIRM=yes` ignored | command-line only | same as apply |
| `make tf-freeze CONFIRM=yes` | `CONFIRM=` refused, nothing written | takes no path variable (the tree is `infra/`, fixed) | n/a — no value reaches a shell beyond `CONFIRM`, one literal | env `CONFIRM=yes` ignored | command-line only; a pinned file missing on disk is a refusal (R) | `tests/test_makefile.py::test_tf_freeze_confirm_from_command_line_only`; `tests/test_infra.py::test_tf_freeze_requires_confirm_origin_and_writes_the_manifest`, `::test_manifest_gate_reads_tf_json_and_vanished_files` |

## Review & stack risk

- **code-reviewer** (triggered — `infra/**`, `infra/cli.py`, `Makefile`,
  `tests/`): the `enable_*` toggles default false and gate the optional modules;
  `project_id` the only required var; no committed key/state/tfvars; SA roles
  least-privilege (no owner/editor); `infra/cli.py` validates before deriving,
  gates apply/destroy on `$(origin)`; every user variable `_Q`-quoted and
  `unexport`ed.
- **security-reviewer** (MANDATORY — `infra/`, IAM, service accounts, budgets,
  WIF, destructive/cloud targets): no secret at rest; WIF binding scoped to this
  repo; SA scoped to the datasets/bucket, not the project; `tf-apply`/`tf-destroy`
  `$(origin)`-gated; the budget threshold and the documented kill-switch;
  `terraform.tfvars.example` carries only a placeholder.
- **functionality-tester** (triggered): the DONE command; `make tf-validate`
  green; each mutation line KILLED and the exclusions reasoned;
  `tests/test_infra.py` exercises the static checks against the real tree; the
  `PROJECT`/`CONFIRM` negatives.
- **coherence-auditor** at exit (mandatory, whole repo): CLAUDE.md Commands +
  Repo map + Current status + BACKLOG count updated; ARCHITECTURE §6 matches the
  landed backend/WIF/budget; PHASES "Delivered"; DECISIONS Phase 9a; that the
  finished 9a supports 9b (datasets/bucket/SA a `bq load` + `dbt build` can use).
- Stack risk (first hour, STOP on any surprise, §8): (1) the google provider
  version `terraform init -backend=false` resolves — pin it in
  `required_providers`; (2) `terraform validate` passing without GCP auth on the
  toggled-off tree; (3) the `google_project` data source supplying the
  budget's billing account from `project_id` alone (no second required var); (4)
  the WIF provider attribute-condition syntax on the current provider.

## Out of scope (deferred, recorded)

- The BigQuery dialect (five `bigquery__` bodies), the `bq load` landing, the
  conflicting-duplicate dbt test, `profiles.yml` completion, and the DuckDB≡
  BigQuery pin-parity job — all **9b** (its spec finalized after 9a merges).
- `test-int-bigquery` — 9b (it needs the datasets 9a creates and the macros 9b
  writes).
- The Composer and Spanner module bodies beyond the `count`-gated shell —
  Phase 11 and Phase 10 (their `enable_*` toggles land here, false).
- The billing kill-switch (Pub/Sub → Cloud Function) — documented optional in
  `docs/DEPLOYMENT.md`; built only on request (BACKLOG closed as documented).
