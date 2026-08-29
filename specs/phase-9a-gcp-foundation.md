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
   source (not a second required var); the WIF repo slug defaults to this repo.
   `infra/` is already in the Repo map and the truth-isolation exemption set
   (Phase 0). **Approved.**

3. **Least-privilege SA + WIF, ADC/WIF only — design change.** One service
   account with BigQuery data/job roles on the two datasets and object-admin on
   the bucket, never `roles/owner|editor`; a Workload Identity Federation pool +
   provider bound to the GitHub repo, so CI authenticates by short-lived OIDC
   token, **never a committed key**. This also stands up the WIF the BACKLOG row
   "Cross-warehouse dialect drift is caught only on DuckDB in CI" needs; the row
   closes at 9b exit when the parity job runs on it. **Approved.**

4. **Budget alerts $50 / $150; the kill-switch is documented, not built —
   design change + carry-over.** DUE BACKLOG row "Budget alerts do not stop
   spend": alerts notify only; the real guardrail is an optional Pub/Sub → Cloud
   Function that disables billing. Per the row ("built only if the author wants
   it") it is **documented as optional** in `docs/DEPLOYMENT.md` and NOT built.
   Closes the row (documented in Phase 9). **Approved (document-only).**

5. **The four `make` targets — design change.** `tf-validate` (offline: `init
   -backend=false` + `validate` + `fmt -check`), `tf-plan`, `tf-apply`,
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
   calendar) stay open until 9b. Count 14 → **13** after 9a.

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
   `project_id` has a default. *Evidence: row 1.*
2. **Meter off by default.** With `enable_composer` and `enable_spanner` at their
   false defaults, the plan creates zero Composer / Spanner resources; both
   modules are `count`-gated. *Evidence: row 2.*
3. **No secret in the tree.** No service-account key, `*.tfstate`, or `*.tfvars`
   is tracked; auth is ADC / WIF (`method` is oauth/WIF, never a keyfile path).
   *Evidence: row 3.*
4. **Least privilege.** The pipeline service account holds only BigQuery
   data/job roles on the two datasets and object-admin on the bucket — never
   `roles/owner` or `roles/editor`. *Evidence: row 4.*
5. **Destroy leaves nothing billable.** After `make tf-apply` then `make
   tf-destroy`, no dataset, bucket, service account, or budget remains; every
   billable resource is Terraform-managed (none created out of band). *Evidence:
   row 5.*
6. **Cloud/destructive targets gated.** `tf-apply` and `tf-destroy` require
   `CONFIRM=yes` from the command line; `PROJECT` is validated before any
   terraform runs; empty / `../x` / `"; ` values are refused. *Evidence: row 6.*

(≤ 6. `docs/PHASES.md` carries the same clauses; the spec and DECISIONS are
authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_infra.py::test_project_id_is_the_only_required_var` (parses `variables.tf`: exactly one variable without a `default`); manual `make tf-plan PROJECT=<id>` → a clean plan, no prompt for a second var |
| 2 | `tests/test_infra.py::test_enable_toggles_default_false`, `::test_optional_modules_are_count_gated` (composer/spanner modules called with `count = var.enable_* ? 1 : 0`); manual `make tf-plan` → `0 to add` for the toggled modules |
| 3 | `tests/test_infra.py::test_no_tracked_secret_state_or_tfvars` (`git ls-files` matches no `*.tfstate*`/`*.tfvars`/`*-key.json`), `::test_auth_is_adc_or_wif_never_keyfile` (no `credentials`/`keyfile` in any `.tf` or `profiles.yml`) |
| 4 | `tests/test_infra.py::test_sa_roles_are_least_privilege` (the iam module's roles ⊆ the BQ-data/job + storage-object allowlist; `roles/owner`/`roles/editor` absent) |
| 5 | manual `make tf-apply PROJECT=<id> CONFIRM=yes` then `make tf-destroy PROJECT=<id> CONFIRM=yes`, then `bq ls --project_id=<id>` / `gcloud iam service-accounts list` / `gcloud billing budgets list` all empty; `tests/test_infra.py::test_every_module_resource_is_destroyable` (no `lifecycle { prevent_destroy = true }`, no out-of-terraform resource) |
| 6 | `tests/test_makefile.py::test_tf_apply_and_destroy_confirm_from_command_line_only`, `::test_tf_targets_pass_project_as_one_literal`; `tests/test_infra.py::test_cli_validates_project`, `::test_cli_requires_confirm_origin` (over `infra/cli.py`) |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Only `project_id` is required.** For all fresh clones, every Terraform variable except `project_id` has a `default`, so a plan needs only that one input. | `tests/test_infra.py::test_project_id_is_the_only_required_var` (a second default-less var → red); mutation `infra/cli.py::validate_project invert-guard` (an unvalidated project reaches terraform) |
| 2. **Meter off by default.** For all default applies, no Spanner/Composer/other-billable resource is created; `enable_*` default false and the optional modules are `count = var.enable_* ? 1 : 0`. | `tests/test_infra.py::test_enable_toggles_default_false`, `::test_optional_modules_are_count_gated`; manual `tf-plan` zero toggled resources |
| 3. **No secret at rest.** For all commits, no key/tfstate/tfvars is tracked and no `.tf`/`profiles.yml` names a keyfile; auth is ADC/WIF. | `tests/test_infra.py::test_no_tracked_secret_state_or_tfvars`, `::test_auth_is_adc_or_wif_never_keyfile` |
| 4. **Least privilege.** For all IAM bindings, the pipeline SA gets only the BQ data/job + bucket-object roles, never `roles/owner`/`roles/editor`. | `tests/test_infra.py::test_sa_roles_are_least_privilege` |
| 5. **Destroy is total.** For all applied resources, `terraform destroy` removes them — no `prevent_destroy`, no resource created outside Terraform. | `tests/test_infra.py::test_every_module_resource_is_destroyable`; manual apply→destroy→empty listing |
| 6. **Cloud/destructive targets gated.** For all `tf-apply`/`tf-destroy` invocations, `CONFIRM=yes` from the command line is required and `PROJECT` is validated before terraform runs; a bad or env-only value is refused. | `tests/test_infra.py::test_cli_requires_confirm_origin`, `::test_cli_validates_project`; `tests/test_makefile.py::test_tf_apply_and_destroy_confirm_from_command_line_only` |

Rules — the Terraform HCL is configuration no mutation operator addresses (the
four Python operators act on `.py`; the two SQL operators on `case` arms). It is
pinned by the static `tests/test_infra.py` checks, `terraform validate` in the
DONE command, and the manual plan/apply/destroy Evidence — the same treatment
Phase 7 gave SQL predicates. Every Python invariant (the `infra/cli.py`
validators and the confirm gate) gets a mutation line; the unmutated suite runs
first and must be green.

```mutations
infra/cli.py::validate_project      invert-guard
infra/cli.py::require_confirm       invert-guard
infra/cli.py::tf                    constant-return:0
```

Equivalent-mutant / refused exclusions, named up front and verified once at
implementation on a scratch copy (the Phase 6/7 pattern):

- `infra/cli.py::validate_project constant-return:'x'` — if a
  `constant-return` on the validator is proposed, it is REFUSED: `validate_project`
  returns the validated string, and a constant `'x'` is itself a valid
  project-id shape, so no test distinguishes it. `invert-guard` (skip the regex
  check) is the killing operator and is in the block.
- The `tf-plan`/`tf-validate` recipes take `PROJECT` but no `CONFIRM` — they are
  not destructive; the `$(origin)` gate is on `tf-apply`/`tf-destroy` only, and
  `require_confirm invert-guard` kills both.

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
  the two datasets and `objectAdmin` on the bucket, and a WIF pool/provider bound
  to `attribute.repository == <repo slug var>`; no key resource, no keyfile path
  anywhere. Rejected: `roles/editor` on the project (broad); a downloaded SA key
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
- **Budget alerts $50 / $150; the kill-switch documented, not built (item 4)** —
  `modules/budget` sets threshold rules at $50 and $150 on a monthly amount;
  `docs/DEPLOYMENT.md` documents the optional Pub/Sub → Cloud Function billing
  disable as the real guardrail. Closes BACKLOG "Budget alerts do not stop
  spend". Rejected: building the kill-switch now (nothing billable runs by
  default — no runaway to catch yet).
- **`infra/cli.py` validates `PROJECT` and gates `tf-apply`/`tf-destroy`;
  four `make` targets (item 5)** — satisfies invariant 6. Mirrors
  `loader/cli.py`: one process validates `PROJECT` (`^[a-z][a-z0-9-]{4,28}[a-z0-9]$`)
  before deriving the `-var`, and refuses `tf-apply`/`tf-destroy` unless
  `CONFIRM=yes` has command-line origin; `tf-validate` and `tf-plan` are
  ungated (non-destructive). Rejected: `terraform` invoked straight from the
  Makefile with `-var project_id=$(PROJECT)` (an unvalidated value reaches the
  provider; no `$(origin)` gate on apply).

## Scope (files)

- `infra/main.tf`, `infra/variables.tf`, `infra/outputs.tf`,
  `infra/terraform.tfvars.example`,
  `infra/modules/bigquery/{main,variables,outputs}.tf`,
  `infra/modules/gcs/{main,variables,outputs}.tf`,
  `infra/modules/iam/{main,variables,outputs}.tf`,
  `infra/modules/budget/{main,variables,outputs}.tf`,
  `infra/modules/composer/{main,variables,outputs}.tf` (written, `count`-gated),
  `infra/modules/spanner/{main,variables,outputs}.tf` (written, `count`-gated)
- `infra/cli.py` (`validate_project`, `require_confirm`, `tf`) — new package
  (already in the truth-isolation exemption set and the Repo map)
- `Makefile` (`tf-validate`, `tf-plan`, `tf-apply`, `tf-destroy`; add
  `PROJECT` to the `unexport` list)
- `tests/test_infra.py` (new — the static `.tf` checks + `infra/cli.py` unit
  tests), `tests/test_makefile.py` (the four tf targets)
- `docs/DEPLOYMENT.md` (new — bootstrap, cost table, teardown)
- Records: `DECISIONS.md`, `docs/PHASES.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`
  (§6; §8 only if a surprise), `BACKLOG.md`
- Untouched by contract: `generator/`, `dbt/`, `loader/`, `eval/`, `serving/`,
  `orchestration/`, `fixtures/`, `tests/pins.py`, `pyproject.toml`, `uv.lock`,
  `.gitignore` (its Terraform block already exists)

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 9a entries: `infra/` layout + `enable_*` toggles;
      least-privilege SA + WIF (ADC/WIF only); two datasets + region; state
      backend bootstrap-documented; budget $50/$150 + kill-switch documented;
      `infra/cli.py` + the four gated targets. Note on the in-force Infra line
      (the toggles are real now).
- [ ] `docs/PHASES.md` — Phase 9 "Delivered" paragraph (9a half); Done-when as
      landed for the infra clauses
- [ ] `CLAUDE.md` — Current status; Commands (`tf-validate|tf-plan|tf-apply|
      tf-destroy`); Repo map (`infra/` real); `unexport` list (`PROJECT`); Open
      BACKLOG rows: **13**
- [ ] `docs/ARCHITECTURE.md` — §6 (state backend bootstrap, WIF, budget as
      landed); §8 Gotchas only if a stack surprise lands (provider version,
      `terraform validate` behavior, budget billing-info data source)
- [ ] `BACKLOG.md` — "Budget alerts do not stop spend" struck (`DONE Phase 9a` —
      documented as optional); "Spanner 90-day trial expiry" re-checked,
      re-deferred (module written, `enable_spanner=false`, no apply); count 14 → 13
- [ ] `docs/DEPLOYMENT.md` — new (bootstrap, cost table, teardown, optional
      kill-switch; Spanner/Composer teardown dates)
- [ ] Spec amendments — none (the phase-9b spec does not exist yet; it is
      finalized after 9a merges, per the predecessor-merged rule)
- [ ] docs/RESULTS.md, METRICS.md, AB_DESIGN.md — none (no generated block)
- [ ] README — none (no README in the repo)

## Threat model (REQUIRED)

`tf-validate`/`tf-plan` take `PROJECT`; `tf-apply`/`tf-destroy` take `PROJECT`
and `CONFIRM`, in the settled shape (one Python process, `PROJECT` validated
`^[a-z][a-z0-9-]{4,28}[a-z0-9]$`, the `-var` derived; `$(call _Q,$(value VAR))`;
`unexport`ed). `PROJECT` never becomes a path — it is passed to `terraform … -var
project_id=<validated>` via a subprocess arg list (no shell), and terraform runs
with `-chdir=infra` (a fixed dir, not user input). `tf-apply` creates cloud
resources and `tf-destroy` deletes them: both `$(origin)`-gated (`CONFIRM=yes`
command-line only), ask-first every time. Cost if run twice: `tf-apply` is
idempotent (Terraform diffs to no-op on a second run — no double spend);
`tf-destroy` is idempotent (nothing left to delete). What `tf-destroy` removes:
every resource in state — the two datasets, the bucket, the SA + WIF, the budget
— returning the project to zero billable resources.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make tf-plan PROJECT=<id>` | refused (`PROJECT: refused — [a-z][a-z0-9-]…`) | refused, never a path | one literal, refused | reaches Python, validated the same | n/a — not destructive | `tests/test_makefile.py::test_tf_targets_pass_project_as_one_literal`; `tests/test_infra.py::test_cli_validates_project` |
| `make tf-apply PROJECT=<id> CONFIRM=yes` | `PROJECT` refused; `CONFIRM=` refused | refused | one literal, refused | `CONFIRM=yes` from env ignored (`$(origin)` ≠ command line) | honoured only from the command line | `tests/test_makefile.py::test_tf_apply_and_destroy_confirm_from_command_line_only`; `tests/test_infra.py::test_cli_requires_confirm_origin` |
| `make tf-destroy PROJECT=<id> CONFIRM=yes` | same as apply | refused | one literal, refused | env `CONFIRM=yes` ignored | command-line only | same as apply |

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
