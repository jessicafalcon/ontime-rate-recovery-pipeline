# Phase 11 — Composer module (written, not applied) (PROPOSED)

Contract for the `phase-11-composer-module` branch. Source: docs/PHASES.md
Phase 11. Depends on Phase 10 (PR #15) and `fix/landing-package` (PR, merged to
main `891c898`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
Composer is provisioned by the already-pinned `hashicorp/google ~> 6.0`
provider (`google_composer_environment`), and the DAG it runs
(`orchestration/dags/pipeline_dag.py`) was authored in Phase 8b — this phase
uploads it, it does not re-author it. `apache-airflow` stays Docker-only and out
of `uv.lock` (CLAUDE.md allowlist), unchanged. If `google_composer_environment`
(v6 provider) turns out to lack a field this spec assumes — the smallest
environment size, the DAG-bucket prefix output, an env service-account input —
that is a STOP-and-report, not a workaround (ARCHITECTURE §8).

## Reconciliation against main (first commit on the branch)

Main as it actually is (`891c898`): Phase 10 merged (PR #15) — Spanner module
filled, the plan-first apply gate (`SAFE_ACTIONS`), the cloud-env allowlist
(`CLOUD_ENV_ALLOW` / `in_cloud_namespace` / `REDIRECTION_NAMES`), the env
allowlist (`ENV_ALLOW`) all live in `infra/cli.py`; `fix/landing-package`
merged — `loader/` → `landing/`, `pipeline/cli.py` split out. The GCP stack on
`ontime-rate-recovery`: the free-tier layer (two datasets, bucket, SA + grants,
budget) is UP; nothing billable is up (Spanner torn down 2026-08-31, `Listed 0
items.`). Terraform runs on operator ADC, never the impersonated SA (§8). The
`ontime-pipeline` SA is live and in state (Phase 10's apply imported it — no
undelete detour until the next full `tf-destroy`).

What the composer module already is (stubbed since Phase 9a, region-validation
added round 2): `infra/modules/composer/` = an empty shell —
`main.tf` a comment only, `variables.tf` declares `project_id` + a
shape-validated `region`, `outputs.tf` empty; the root wires it count-gated
(`module "composer" { count = var.enable_composer ? 1 : 0 }`, main.tf) with
`enable_composer` default false (variables.tf); `tests/test_infra.py`'s
`GATED_ALLOWED_RESOURCE_TYPES[composer] = set()` pins it to declare **no**
resource today. So a default plan already creates zero Composer resources —
half of the Done-when holds on main with nothing added.

What Phase 11 fills: the module BODY (the environment, its DAG-bucket upload of
the committed DAG, the API enablement, the one scoped runtime grant), the
composer resource-type allowlist (`set()` → the exact set), the root wiring of
the new inputs (`sa_email`, like Spanner's), `docs/DEPLOYMENT.md`'s Composer
bring-up / run / teardown / cost section, and the re-freeze
(`infra/MANIFEST.sha256`). **Nothing is applied** — the live half of the
Done-when is a `tf-plan` (reads GCP APIs on operator ADC, creates nothing),
captured as evidence; the offline half is the static gate/allowlist/default
tests.

BACKLOG rows that name Phase 11 as a trigger, dispositioned (phase-entry
review):

- **Row 46 — `docs/PHASES.md` Phase 10 "Delivered" narrative stops at N1–N3.**
  DUE, done here: this phase's doc pass appends O/P/Q to the Phase 10 Delivered
  narrative and corrects the amendment count (a record fix, batched in the
  wording/record commit — CLAUDE.md "Fix commits").
- **Row 37 — the offline DAG structure test cannot pin DAG↔task attachment.**
  RE-DEFERRED. Phase 11 changes no pipeline DAG code (`pipeline_dag.py` is
  uploaded verbatim) and adds no Docker to CI, so neither leg of the row's
  trigger ("CI gains Docker" / "make the offline stub track a current-dag") is
  pulled by an infra plan-only phase. Trigger unchanged; it belongs to Phase 12
  (the live Composer run, where the container test is the killer) or a CI-Docker
  change.
- **Row 44 — the cloud-env redirection gate is not fully closed.** RE-DEFERRED.
  Phase 11 makes NO cloud-env gate change — no new cloud command, no
  `infra/cli.py` env-handling edit (the `enable_composer` toggle already reaches
  Terraform through the existing `VARS='name=value,…'` → argv `-var` path from
  `fix/tf-vars-argv`; DAG upload is a Terraform resource on `tf-apply`, not a
  new target). Folding the proxy-spelling / exact-pin / entry-point close into a
  plan-only infra phase is scope the phase does not earn. Re-deferred with a
  fresh trigger: **"the next edit to `infra/cli.py`'s cloud-env or ENV
  allowlist, or Phase 12's live Composer command surface, whichever first"**.

If, in implementation, a Composer input forces an `infra/cli.py` change (it
should not — no new make target, no new env name), row 44 is pulled and this
reconciliation is amended before the change lands (CLAUDE.md "Fix amendments").

## Why

The plan calls Composer last-before-demo and applied exactly once (PROJECT_BRIEF
§ demo-day; ~$300+/mo floor, billed continuously), so the module is authored and
proven **plan-clean** now, and applied only on demo day (Phase 12) with the
local-Docker-Airflow → real-BigQuery fallback rehearsed. Writing it as its own
plan-only phase keeps the meter off while the review surface (a billable
resource, a runtime identity, a DAG upload) gets the same scrutiny the Spanner
module got, with zero cost exposure.

## The central constraint

**The meter stays off: `enable_composer` defaults false, a default `tf-plan`
adds zero Composer resources, and nothing in this phase is applied.** The
free-tier layer and every DuckDB pin, golden and the Spanner contract are
untouched — this phase adds only count-gated `.tf` that a default plan never
instantiates. No `.py` on any pipeline path changes; no golden, no `pins.py`
value, no `MANIFEST.sha256` under `fixtures/` moves.

## DONE command

```
make test && make lint && make tf-validate
```

- `make test` — the offline suite green (474+): the static half of the
  Done-when — composer is count-gated (`test_optional_modules_are_count_gated`),
  default off (`test_enable_toggles_default_false`), declares exactly the
  allowlisted resource types (`test_composer_module_resource_types`), runs as
  the pipeline SA on a scoped grant (`test_composer_runtime_grant_scope`), and
  its DAG upload sources the committed DAG (`test_composer_uploads_the_committed_dag`);
  the `.tf` tree matches the re-frozen manifest (`test_tf_tree_matches_manifest`).
- `make lint` — ruff clean over the (doc/test) Python touched.
- `make tf-validate` — `terraform validate` + `fmt -check` over the filled
  module: the HCL parses and is canonically formatted, offline, no GCP call.
- **Live gate (ask-first, plan-only — captured as Evidence, nothing applied):**
  `make tf-plan PROJECT=<id>` shows **zero** Composer resources, and
  `make tf-plan PROJECT=<id> VARS='enable_composer=true'` shows **exactly** the
  module's resources (`Plan: N to add`, no destroy/change to the free-tier
  layer). Reads GCP APIs on operator ADC; creates nothing.

## Done-when

1. **Default plan is Composer-free.** For the default toggle, `tf-plan` adds
   zero `google_composer_*` (and zero of the module's other) resources; the
   module is count-gated and `enable_composer` defaults false. *Evidence: rows
   1, 5.*
2. **The toggled plan is exactly the module.** `tf-plan
   VARS='enable_composer=true'` adds exactly the module's resource set and
   nothing else — no change or destroy to the free-tier layer. Every resource
   type the module declares is on its exact allowlist. *Evidence: rows 2, 6.*
3. **The environment runs as the least-privilege pipeline SA.** The Composer
   environment's runtime identity is the existing pipeline SA
   (`module.iam.service_account_email`, passed like Spanner's `sa_email`),
   granted exactly `roles/composer.worker` — its documented minimum — and no
   other new grant; no Composer-default SA is used. *Evidence: row 3.*
4. **The DAG uploaded is the committed 8b DAG, unmodified.** The module uploads
   `orchestration/dags/pipeline_dag.py` (and `orchestration/tasks.py`) into the
   environment's own DAG bucket; the object's source is the committed file, not
   an inline copy that could drift. Phase 11 does not edit `pipeline_dag.py`.
   *Evidence: row 4.*
5. **Nothing is applied; nothing billable is left up.** The phase's only writes
   are count-gated `.tf`, the re-frozen manifest, tests and docs; the live
   evidence is a plan, never an apply. *Evidence: rows 1, 7.*

(5 items. `docs/PHASES.md` Phase 11 row carries the same clauses; the spec and
DECISIONS are authoritative if the landing diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_infra.py::test_optional_modules_are_count_gated`, `::test_enable_toggles_default_false`; live `make tf-plan PROJECT=<id>` → no `google_composer_*` in the plan |
| 2 | `tests/test_infra.py::test_composer_module_resource_types` (the exact `GATED_ALLOWED_RESOURCE_TYPES[composer]` set); live `make tf-plan PROJECT=<id> VARS='enable_composer=true'` → `Plan: N to add`, `0 to change`, `0 to destroy` |
| 3 | `tests/test_infra.py::test_composer_runtime_grant_scope` — the env's `service_account` is `var.sa_email` and the only grant is `roles/composer.worker` to that member |
| 4 | `tests/test_infra.py::test_composer_uploads_the_committed_dag` — the `google_storage_bucket_object` `source` points at `orchestration/dags/pipeline_dag.py`; `tests/test_dag_structure.py` unchanged (DAG not edited) |
| 5 | `tests/test_infra.py::test_tf_tree_matches_manifest` (re-frozen `infra/MANIFEST.sha256`); `make tf-validate` → `tf-validate OK` |
| 6 | `tests/test_infra.py::test_every_declared_resource_type_is_on_the_allowlist` / `::test_every_data_source_type_is_on_the_allowlist` (the module reads no `data` source) |
| 7 | grep: no `.py` under `landing/ pipeline/ dbt/ serving/ eval/ generator/` changed (git diff main...HEAD); DONE command adds no live apply |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all plans with `enable_composer` at its default, zero Composer-module resources are planned. | `tests/test_infra.py::test_optional_modules_are_count_gated` + `::test_enable_toggles_default_false` — the gate is `count = var.enable_composer ? 1 : 0` and the default is `false` (a plan-parity live check confirms it) |
| For all resources the composer module declares, the resource type is in the module's exact allowlist. | `tests/test_infra.py::test_composer_module_resource_types` — a new/removed type in `modules/composer/*.tf` makes the declared set ≠ the pinned set |
| For all Composer runtime access, it is the pipeline SA carrying exactly `roles/composer.worker` — no Composer-default SA, no second grant. | `tests/test_infra.py::test_composer_runtime_grant_scope` — the env `service_account = var.sa_email`, the single grant role is `roles/composer.worker`, member is that SA |
| For all DAG code Composer runs, it is the byte-for-byte committed `pipeline_dag.py` — the upload sources the file, never an inline copy. | `tests/test_infra.py::test_composer_uploads_the_committed_dag` — the object `source` is the repo path; `tests/test_dag_structure.py` still passes (DAG unedited) |
| For all files Terraform loads under `infra/`, the content equals the re-frozen manifest. | `tests/test_infra.py::test_tf_tree_matches_manifest` — any edited `.tf` without a matching `tf-freeze` fails |

Rules: the composer module is HCL, not Python — its invariants 2/3/4 are pinned
by the static `test_infra.py` property checks (the same kind that pin the Spanner
module), which no mutation operator addresses (the Phase 7/9a/10 treatment of
`.tf`). Phase 11 changes no pipeline Python. Invariant 5 (the `.tf` tree equals
the re-frozen manifest) is the one invariant upheld by Python — `infra/cli.py`'s
manifest gate — so the `mutations` block names it (the 9a precedent: `manifest_diff`
neutered reddens `tests/test_infra.py`). No new Python is added; if
implementation adds a helper upholding an invariant, a line is added for it.

```mutations
infra/cli.py::manifest_diff        constant-return:[]
```

## Pinned decisions (do not re-litigate)

- **The environment runs as the existing least-privilege pipeline SA, granted
  exactly `roles/composer.worker` inside the module.** The root passes
  `sa_email = module.iam.service_account_email` (the Spanner wiring pattern),
  the module sets the env's `service_account` to it and adds one
  `google_project_iam_member` for `roles/composer.worker` (the documented
  minimum an environment's service account needs). Alternative rejected: letting
  Composer create/use the default Compute SA (broad, unmanaged, un-scoped) —
  satisfies invariant 3. (A tighter custom role like the Spanner `data_user`
  role is deferred: `composer.worker` is a large, provider-recommended set no
  documented custom role replaces cleanly — BACKLOG if review pushes.)
- **The DAG-bucket upload is a `google_storage_bucket_object` whose `source` is
  the committed `orchestration/dags/pipeline_dag.py` (+ `tasks.py`), into the
  environment's own `dag_gcs_prefix` bucket.** The env creates its DAG bucket;
  Terraform references its prefix output and drops the file in. Alternative
  rejected: an inline `content =` heredoc (drifts from the real DAG) — satisfies
  invariant 4. Whether Composer's workers can EXECUTE the `make` targets (repo,
  `uv`, dbt present on the workers) is Phase 12's live concern with the
  Docker-Airflow → BigQuery fallback; Phase 11 proves the upload PLANS, not that
  a run succeeds.
- **The smallest documented environment size, minimal node config.** Composer
  has a hard cost floor (~$300+/mo) that no config removes — the size is pinned
  to the smallest the provider offers so demo-day cost is the minimum, not so it
  is cheap. Alternative rejected: a larger default for headroom the demo does
  not need — satisfies the central constraint (meter minimised, applied once).
- **The composer resource-type allowlist is filled to the exact declared set;
  the module reads no `data` source.** `GATED_ALLOWED_RESOURCE_TYPES[composer]`
  goes from `set()` to exactly the types the body declares
  (`google_project_service`, `google_composer_environment`,
  `google_storage_bucket_object`, `google_project_iam_member`), so a new billable
  type dropped in later is caught the same way Spanner's is. Alternative
  rejected: exempting the gated module (Phase 10 round 1 #11 rejected this
  repo-wide) — satisfies invariant 2.
- **Plan-only: the Done-when's live half is `tf-plan`, gated ask-first, nothing
  applied; the `.tf` tree is re-frozen in the same commit.** `make tf-freeze
  CONFIRM=yes` re-pins `infra/MANIFEST.sha256` over the filled module in the same
  commit as the `.tf` change (CLAUDE.md: the manifest hunk lands with the `.tf`
  change). Alternative rejected: applying to observe the real resource set
  (Phase 12's job; violates the central constraint) — satisfies invariant 5.

## Scope (files)

- `infra/modules/composer/main.tf` — the module body (API enablement, the
  environment, the DAG-bucket upload, the one scoped grant).
- `infra/modules/composer/variables.tf` — add `sa_email` (+ any input the body
  needs). `region` keeps the shape check (round 2 #6 — interpolated into the
  environment config); `sa_email` follows the Spanner `sa_email` precedent — it
  is root-derived (`module.iam.service_account_email`), never caller/tfvars-set,
  so it carries no validation block (the tfvars-bypass check that motivates the
  `region`/`project_id` regexes does not apply to a derived value).
- `infra/modules/composer/outputs.tf` — the env name / DAG-bucket prefix (for the
  runbook), if useful.
- `infra/main.tf` — pass `sa_email = module.iam.service_account_email` to the
  count-gated `module "composer"` (the Spanner wiring shape).
- `infra/MANIFEST.sha256` — re-frozen (`make tf-freeze CONFIRM=yes`), same commit
  as the `.tf`.
- `tests/test_infra.py` — fill `GATED_ALLOWED_RESOURCE_TYPES[composer]`; add
  `test_composer_module_resource_types`, `test_composer_runtime_grant_scope`,
  `test_composer_uploads_the_committed_dag`.
- Records: `DECISIONS.md`, `docs/PHASES.md`, `docs/DEPLOYMENT.md`, `CLAUDE.md`,
  `BACKLOG.md`, `docs/ARCHITECTURE.md` (§8 if a live plan surprises).

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 11 entry: SA-as-runtime-identity + `composer.worker`
      minimum; DAG upload sources the committed file; smallest env size;
      plan-only, no apply.
- [ ] `docs/PHASES.md` — Phase 11 row: Done-when as landed + "Delivered"
      paragraph; **and (row 46) the Phase 10 "Delivered" narrative extended
      with O/P/Q and the amendment count corrected.**
- [ ] `CLAUDE.md` — Current status (Phase 11 in flight → done); Repo map
      `infra/` line (composer module filled, count-gated); BACKLOG count.
- [ ] `docs/DEPLOYMENT.md` — the Composer bring-up / run / teardown / cost
      section (mirrors the Spanner runbook shape; ~$300+/mo, applied and
      destroyed the same demo-day session); the cost-table Composer row stays.
- [ ] `BACKLOG.md` — row 37 re-deferred (trigger unchanged), row 44 re-deferred
      (fresh trigger), row 46 struck ("DONE Phase 11").
- [ ] `docs/ARCHITECTURE.md` §8 — only if a live `tf-plan` surprises (e.g. a
      required field or a service-agent edge, the Spanner-style lesson).
- [ ] README — none (no demo or command surface changes).
- [ ] `docs/RESULTS.md` / `docs/METRICS.md` — none (no metric or run output).

## Threat model (REQUIRED when the phase adds a Makefile target that takes a variable, deletes anything, touches cloud resources, or takes user input)

None — no new Makefile target. The phase adds only count-gated `.tf`, tests and
docs. The one variable the live evidence uses, `VARS='enable_composer=true'`,
already exists (`fix/tf-vars-argv`): it reaches Terraform as a validated
command-line `-var`, refuses an env-origin `VARS` (`$(origin VARS)`) and refuses
while any `TF_VAR_*`/`TF_CLI_ARGS*` or an auto-loaded tfvars is present
(Amendment T), and `tf-freeze`/`tf-apply` keep their existing `$(origin CONFIRM)`
gates and the plan-first `SAFE_ACTIONS` apply — all covered by
`tests/test_makefile.py` / `tests/test_infra.py`, unchanged. No apply runs in
this phase; the live half is a `tf-plan` that creates nothing.

## Review & stack risk

- **code-reviewer** (triggered — `infra/**/*.tf` and `tests/` in Scope): the
  count-gate, the exact resource-type allowlist, the runtime-identity scope
  (least-privilege, no default SA, one grant), the DAG upload sourcing the
  committed file, no pipeline `.py` touched, the manifest re-frozen in the same
  commit.
- **security-reviewer** (mandatory — `infra/` + IAM + a cloud-cost/toggled
  resource touched): the `composer.worker` grant is the minimum and scoped to
  the one SA; no service-agent grant (the Spanner §8 lesson — a federated/Composer
  service agent is not ours to grant); the toggle stays default-false and the
  meter cannot turn on without a command-line `VARS` + `CONFIRM`; no secret in
  any `.tf` or the env; the env `service_account` is not the default Compute SA.
- **functionality-tester** (triggered — after code-reviewer): the DONE command,
  the new `test_composer_*` tests exercise the claims, `tf-validate` passes, the
  live `tf-plan` (ask-first) shows zero at default and exactly-the-module at
  `true`.
- **coherence-auditor** at exit (mandatory, phase exit): the composer stub
  comments ("empty shell", "land in Phase 11", "composer is still a stub") are
  gone or corrected; `DEPLOYMENT.md`'s "Record the actual apply dates here when
  they land" is reconciled with the plan-only reality; the Phase 10 Delivered
  narrative (row 46) is extended; diffs the Record-updates list against the diff.
- Stack risk: verify against the v6 provider docs in the first hour, STOP and
  report before any workaround — the smallest `google_composer_environment` size
  and its `config` shape (env size / node config fields differ across Composer 2
  vs 3), the `dag_gcs_prefix`/DAG-bucket output name, and the env
  `service_account` input; findings go under ARCHITECTURE §8. No apply, so any
  surprise is caught at `tf-plan`, cost-free.

## Out of scope (deferred, recorded)

- Applying Composer, triggering a DAG run, capturing the run + row counts, and
  `terraform destroy` — Phase 12 (docs/PHASES.md), with the Docker-Airflow →
  BigQuery fallback rehearsed first.
- A tighter custom runtime role than `roles/composer.worker` — BACKLOG if the
  security review pushes; `composer.worker` is the documented minimum.
- Row 37 (offline DAG↔task attachment fidelity) and row 44 (cloud-env
  redirection gate close) — re-deferred, BACKLOG (dispositions above).
- Whether Composer workers can execute the `make` targets (repo/uv/dbt on the
  workers) — proven live in Phase 12; the DAG upload here proves the plan only.
