# fix/ci-bigquery-parity — the DuckDB≡BigQuery parity run in CI (PROPOSED)

Contract for the `fix/ci-bigquery-parity` branch. Source: `docs/ROADMAP.md`
item 8 (a post-plan `fix/` branch, cited as two BACKLOG rows — **"Cross-warehouse
dialect drift is caught only on DuckDB in CI"** and the remaining item of
**"The public-repo GitHub-side settings are outside the tree"**, the public
`<owner>/<repo>` slug the first `enable_ci_wif=true` apply must trust). Depends
on `fix/prune-live-proof` merged (on `main`, PR #31).

**Status: PROPOSED — do not start until approved.** No new dependencies (the
job runs the existing `make test-int-bigquery`; `google-github-actions/auth` and
GitHub-hosted actions are CI infrastructure, not Python packages; `pyyaml` — a
locked transitive — is what the offline pin test parses the workflow with). The
WIF layer already exists behind `enable_ci_wif` (Phase 9a, `infra/modules/iam`,
default false, no default `github_repository` — Amendments H, K); this branch is
expected to move **no `.tf`** — the apply is a `VARS=` toggle, and `tf-freeze`
runs only if a `.tf` actually changes.

This branch does NOT re-freeze a fixture and does NOT change a data structure, a
write path, or who-writes-what in the pipeline. It adds one CI workflow and its
offline pin test, and performs one persisting `enable_ci_wif=true` apply. Under
CLAUDE.md "Fix amendments" it therefore needs no data-structure amendment; it is
a wording/config change plus a live apply. It IS on a sensitive surface
(`.github/`, an `infra/` apply, WIF/credentials), so the security-reviewer is
mandatory.

## Why

Cross-warehouse dialect drift — a macro whose DuckDB and BigQuery bodies diverge,
a `partition_by` that renders on one adapter and not the other, a `safe_divide`
that types differently — is caught today only by hand: `make test-int-bigquery`
runs on a laptop as the SA, never in CI, so a merge can pass green on DuckDB and
silently break BigQuery until the next manual run. The parity suite already
exists (`tests/integration/test_int_bigquery.py`, extended live by
`fix/prune-live-proof`: full-build byte parity + the incremental source-scan
prune, `6 passed`). What is missing is the CI leg: a `workflow_dispatch`-only
GitHub job that authenticates via Workload Identity Federation (no key at rest)
and runs the same suite against the demo project on demand.

This is the row's stated trigger, verbatim: *"the first `enable_ci_wif=true`
apply: add the `workflow_dispatch` bigquery-parity job on
`google-github-actions/auth` + the `workload_identity_provider` output, run it
once, then strike."* It also discharges the last remaining item of the
public-release row: the public `<owner>/<repo>` slug is what that apply trusts.

## The central constraint

**No secret is ever downloaded, committed, logged, or admitted into the
pipeline's environment; CI authenticates by short-lived OIDC only.** The job
mints a short-lived credential by exchanging a GitHub OIDC token through the
existing WIF provider (repo- AND ref-scoped), and the pipeline's existing
cloud-env allowlist (`infra.cli.CLOUD_ENV_ALLOW`) must pass **unwidened** — so
the credential reaches `make test-int-bigquery` as Application Default
Credentials discovered via `CLOUDSDK_CONFIG` (the one identity-bearing setting
the allowlist admits), never as a `GOOGLE_*` credential-file override the gate
refuses. No warehouse pin, golden, model, or `.tf` moves; the parity suite's own
byte-diffs against `fixtures/tiny/expected/` are the proof that dialect drift is
caught.

## DONE command

```
make test && make lint && make check-docs && make review-gate
```

- `make test` — the offline suite, including the new
  `tests/test_ci_parity_workflow.py` (the workflow is `workflow_dispatch`-only,
  minimally permissioned, SHA-pinned, references identity only through
  `${{ vars.* }}`).
- `make lint` — ruff (read-only in the gate).
- `make check-docs` — every link/target resolves; **check 5** finds no bare
  project-id / repo-slug in the new tracked workflow (only `${{ vars.* }}`
  refs); the BACKLOG count in CLAUDE.md equals BACKLOG.md's un-struck rows.
- `make review-gate` — the offline gate green (test + ruff + check-docs).
- **Live gate (ask-first, the whole point of the branch):** the
  `bigquery-parity` workflow dispatched once on `main` → a green run whose log
  ends with `test-int-bigquery` passing (the three goldens read back off
  BigQuery byte-for-byte + the pins). This is Evidence row 1; a green dispatched
  run is the row's stated Done-when.

## Done-when

1. **A green dispatched parity run.** The `bigquery-parity` workflow, dispatched
   on `main`, authenticates via WIF and runs `make test-int-bigquery` to a pass
   — the parity suite's own byte-diffs prove no DuckDB≡BigQuery drift. *Evidence:
   row 1.*
2. **`workflow_dispatch`-only, never fork-reachable.** The workflow's `on:` is
   exactly `workflow_dispatch` — no `pull_request`, no `push` — so no fork PR and
   no branch push can trigger it or reach the cloud credential. *Evidence: row
   2.*
3. **Least-privilege token, OIDC only.** `permissions:` is exactly
   `{id-token: write, contents: read}`; every `uses:` is pinned to a 40-hex
   commit SHA; the credential is minted by `google-github-actions/auth` from the
   WIF provider — no `secrets`-borne key, no service-account JSON. *Evidence:
   rows 2, 3.*
4. **Identity lives outside the tree.** The provider name, SA email, and project
   id are read from GitHub repo variables (`${{ vars.* }}`), never literals in
   the workflow, so `check-docs` check 5 keeps `<owner>/<repo>` / `<project_id>`
   placeholders in every tracked file. *Evidence: rows 3, 4.*
5. **The credential reaches the pipeline as ADC via `CLOUDSDK_CONFIG`, the
   allowlist unwidened.** The `make` step carries no cloud-env name the gate
   refuses (no `GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_GHA_CREDS_PATH`); ADC
   resolves from `$CLOUDSDK_CONFIG/application_default_credentials.json`.
   `infra.cli.CLOUD_ENV_ALLOW` is unchanged. *Evidence: rows 1, 5.*
6. **Records updated; the two rows struck.** The `enable_ci_wif=true` apply
   showed only WIF adds (no `.tf` moved, so no `tf-freeze`); DEPLOYMENT's CI-leg
   note becomes "built and run"; ROADMAP item 8 landed; the two BACKLOG rows
   struck. *Evidence: row 6.*

(6 items. `docs/PHASES.md` is the phase plan and is untouched by a `fix/` branch;
`docs/ROADMAP.md` item 8 carries the "as landed" note at exit.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1, 5 | The dispatched `bigquery-parity` run's log: `google-github-actions/auth` succeeds, then `make test-int-bigquery PROJECT=… CONFIRM=yes` ends `… passed` (the three `Golden` diffs + pins). Link/screenshot in the PR body and DEPLOYMENT (dated). |
| 2 | `tests/test_ci_parity_workflow.py::test_dispatch_only` — the parsed `on:` mapping has exactly the key `workflow_dispatch`, no `pull_request`/`push`. |
| 2, 3 | `tests/test_ci_parity_workflow.py::test_permissions_are_minimal` — top-level `permissions` is exactly `{id-token: write, contents: read}`. |
| 3 | `tests/test_ci_parity_workflow.py::test_every_action_is_sha_pinned` — every `uses:` value matches `<action>@<40-hex>`. |
| 3, 4 | `tests/test_ci_parity_workflow.py::test_auth_uses_wif_and_no_literal_identity` — the auth step is `google-github-actions/auth` with `workload_identity_provider`/`service_account` sourced from `${{ vars.* }}`; no literal project-id/SA-email/repo-slug anywhere in the file. |
| 4 | `make check-docs` → check 5 passes over the new `.github/workflows/bigquery-parity.yml` (RECORD_GLOBS includes CI): no bare identifier in any value position. |
| 5 | `tests/test_ci_parity_workflow.py::test_make_step_carries_no_refused_cloud_env` — the step running `make test-int-bigquery` sets, via `env:`/exports it controls, no name in `infra.cli.in_cloud_namespace` that `CLOUD_ENV_ALLOW` refuses; it sets `CLOUDSDK_CONFIG`. (The live proof that the gate passed is row-1's green run — `refuse_cloud_env` runs first inside `test-int-bigquery`.) |
| 6 | `git diff main...HEAD` over the §Record-updates list; the apply's `Plan:` line (WIF adds only); `make tf-plan` after apply shows `0 to change` (no drift, no `tf-freeze` needed). |

## Invariants (REQUIRED)

Properties, not mechanisms.

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all triggers, the parity job runs on `workflow_dispatch` alone — no `pull_request` or `push` event reaches the cloud credential. | `tests/test_ci_parity_workflow.py::test_dispatch_only` |
| For all steps, the workflow grants exactly `id-token: write` + `contents: read` and pins every action to a commit SHA — never a broader permission or a floating tag. | `tests/test_ci_parity_workflow.py::test_permissions_are_minimal`, `::test_every_action_is_sha_pinned` |
| For all identity references, the workflow names the provider/SA/project only through `${{ vars.* }}` — no literal id in a tracked file. | `tests/test_ci_parity_workflow.py::test_auth_uses_wif_and_no_literal_identity`; `make check-docs` check 5 |
| For all environments the `make` step sees, no name in `infra.cli.in_cloud_namespace` outside `CLOUD_ENV_ALLOW` is present, so the pipeline's existing `refuse_cloud_env` passes without widening the allowlist. | `tests/test_ci_parity_workflow.py::test_make_step_carries_no_refused_cloud_env` (static: the step sets no refused name and does set `CLOUDSDK_CONFIG`); the row-1 green run (live: `refuse_cloud_env` runs first and did not refuse) |
| For all WIF token exchanges, only the named repo on the trusted ref (`refs/heads/main`) can impersonate the SA — unchanged from Phase 9a. | `tests/test_infra.py::test_wif_provider_condition_is_the_repo_and_ref_conjunction` (already on `main`; this branch does not touch the `.tf`) |

**No production Python changes on this branch.** The change is a yaml workflow
pinned by a new test plus records; the credential enforcement is the EXISTING
`infra.cli.refuse_cloud_env`, already mutation-covered by its own spec. So there
is no new mutable production function and `make mutate SPEC=…` has no lines — it
is not run, per the fix-branch review path (the `review-round` skill: tooling/fix
branches run the agents directly, not the full `/review-round` machinery; `make
review-gate` does not invoke `mutate`). The workflow's invariants are pinned by
`tests/test_ci_parity_workflow.py`; the WIF trust is pinned by the existing
`tests/test_infra.py`. If the developer wants the sweep run anyway, there is no
target for it — this is stated so approval is with eyes open, not a silent skip.

## Pinned decisions (do not re-litigate)

- **The credential reaches the pipeline as ADC via `CLOUDSDK_CONFIG`; the
  `make` step carries no `GOOGLE_*` credential var; `CLOUD_ENV_ALLOW` is
  unwidened.** `google-github-actions/auth` by default exports
  `GOOGLE_APPLICATION_CREDENTIALS` (and `GOOGLE_GHA_CREDS_PATH`) into
  `$GITHUB_ENV` — both `GOOGLE_`-prefixed, both refused by
  `infra.cli.in_cloud_namespace` and absent from `CLOUD_ENV_ALLOW`, so
  `test-int-bigquery` would REFUSE to run (the boundary contract working as
  designed). The workflow instead sets `CLOUDSDK_CONFIG` to a run-scoped dir and
  places the auth credential at
  `$CLOUDSDK_CONFIG/application_default_credentials.json` (google-auth's ADC
  search resolves it when `GOOGLE_APPLICATION_CREDENTIALS` is unset), and the
  `make` step runs with the `GOOGLE_*` names unset for that step. Satisfies the
  central constraint and invariant 4. Rejected: widening `CLOUD_ENV_ALLOW` to
  admit `GOOGLE_APPLICATION_CREDENTIALS` — the credential standard names a
  credential-file override a secret, and admitting a `GOOGLE_`-domain name is a
  denylist-style concession the Boundary contract forbids (the allowlist is the
  security boundary); relocating ADC into the one already-admitted
  identity-bearing setting removes nothing and widens nothing.
- **`workflow_dispatch`-only, `permissions: {id-token: write, contents: read}`,
  every action SHA-pinned.** A manual trigger cannot be reached by a fork PR or a
  branch push; `id-token: write` is the minimum for OIDC; `contents: read` for
  checkout; SHA pins prevent a compromised tag from running attacker code with
  the token. Satisfies invariants 1–2. Rejected: `on: schedule` (the row wants
  on-demand; a cron parity run is a separate later item if a case appears —
  BACKLOG); `pull_request` with an environment gate (a fork could still race the
  approval; `workflow_dispatch` closes the surface entirely).
- **Identity through GitHub repo variables (`${{ vars.* }}`), set outside the
  tree.** The provider name, SA email, and project id are operator-set repo
  variables; the committed workflow holds only refs. Satisfies invariant 3 and
  keeps `check-docs` check 5's placeholders. Rejected: hardcoding the values
  (leaks the demo project id/slug into a tracked record — check 5 red); GitHub
  *secrets* for non-secret identifiers (the provider/SA/project are not secrets;
  variables are the right store, and secrets would be masked from the diagnostic
  log for no benefit).
- **The apply is `enable_ci_wif=true` + `github_repository=<owner>/<repo>`,
  toggle-only, and it PERSISTS.** `make tf-apply PROJECT=<project_id>
  VARS='enable_ci_wif=true,github_repository=<owner>/<repo>' CONFIRM=yes` builds
  the free WIF pool/provider/binding trusting the public slug. Unlike
  Spanner/Composer, WIF is free and must stay up for CI to authenticate, so
  there is **no same-session teardown** — this is the first "stays up between
  sessions" apply, now safe because state lives on the GCS remote backend
  (`fix/tf-remote-state`). The standing Spanner/Composer `Listed 0 items.` exit
  check still runs. Rejected: applying then destroying (defeats the purpose — CI
  could not authenticate afterward).
- **No `.tf` change expected; `tf-freeze` only if one moves.** The WIF resources
  are already written and frozen (Phase 9a); the apply supplies runtime `VARS`,
  which do not edit tracked files. If the apply or a plan reveals a needed `.tf`
  edit, that is a STOP (a design change → its own commit + `tf-freeze` in the
  same commit). Rejected: pre-emptively re-freezing (nothing changed to pin).

## Scope (files)

- `.github/workflows/bigquery-parity.yml` — NEW: the `workflow_dispatch`-only
  parity job (auth via WIF, ADC into `CLOUDSDK_CONFIG`, `make test-int-bigquery`).
- `tests/test_ci_parity_workflow.py` — NEW: the offline pins (dispatch-only,
  minimal permissions, SHA-pinned actions, WIF auth, no literal identity, no
  refused cloud-env in the make step).
- No `.tf` file (the WIF layer is already written and frozen). No pin, golden,
  model, macro, or fixture.

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — the `fix/ci-bigquery-parity` entry (CI parity via WIF; ADC
      via `CLOUDSDK_CONFIG` not `GOOGLE_APPLICATION_CREDENTIALS`, allowlist
      unwidened; `workflow_dispatch`-only; the persisting apply; identity in repo
      variables; each alternative rejected)
- [ ] `docs/DEPLOYMENT.md` — the CI-leg note (currently "deferred … not built in
      9b") becomes built-and-run: the exact apply command, the repo variables to
      set, the dispatch step, and the dated green run; the persisting-apply note
      (WIF stays up)
- [ ] `docs/ROADMAP.md` — item 8 marked landed with the "as landed" note
- [ ] `CLAUDE.md` — Current status (the CI parity leg is live); the CI workflow
      mention in the Repo map / tooling if warranted; BACKLOG count
- [ ] `BACKLOG.md` — strike **"Cross-warehouse dialect drift is caught only on
      DuckDB in CI"** with "DONE fix/ci-bigquery-parity"; strike the remaining
      item of **"The public-repo GitHub-side settings are outside the tree"** (the
      WIF slug passed); open any deferred finding (e.g. a scheduled parity run, or
      the parity job not yet gating merges — with a trigger)
- [ ] `docs/ARCHITECTURE.md` §8 Gotchas — only if the live run surfaces a stack
      surprise (e.g. the `auth`-action ADC relocation); else no change

No change (a `fix/` branch touches none): the phase plan (`docs/PHASES.md`), the
front door (`README.md`), METRICS / RESULTS / AB_DESIGN / INSIGHT (no metric,
demo, or number moves), and every `.tf` (the WIF layer is unchanged).

## Threat model (REQUIRED)

No new Makefile target takes a variable, deletes, or reads input; the branch adds
a CI workflow, not a `make` target. The relevant surface is the workflow itself
and its one cloud invocation:

- **Fork PR / untrusted trigger.** `on: workflow_dispatch` only — a fork PR
  cannot run it and never sees the OIDC token (invariant 1; the public-repo
  Actions approval policy is already "require approval for all external
  contributors", set at the visibility flip). Pinned:
  `test_ci_parity_workflow.py::test_dispatch_only`.
- **Token scope.** `permissions: {id-token: write, contents: read}` — the
  workflow token can mint an OIDC token and read the repo, nothing else; the WIF
  provider only trusts `<owner>/<repo>` on `refs/heads/main` (Phase 9a, pinned by
  `test_infra.py`), so even the minted token impersonates the SA only from a
  dispatch on `main`. Pinned: `::test_permissions_are_minimal`,
  `::test_every_action_is_sha_pinned`; `test_infra.py::test_wif_provider_condition…`.
- **Supply chain.** Every `uses:` is SHA-pinned — a compromised action tag cannot
  run with the token. Pinned: `::test_every_action_is_sha_pinned`.
- **Credential handling.** No key file, no `secrets`-borne credential; auth is
  WIF (short-lived). ADC is relocated into `CLOUDSDK_CONFIG` and the refused
  `GOOGLE_*` vars are unset for the `make` step; `refuse_cloud_env` is the live
  enforcement (invariant 4). Pinned: `::test_make_step_carries_no_refused_cloud_env`
  + the row-1 green run.
- **`CONFIRM=yes` origin.** The workflow passes `CONFIRM=yes` on the `make`
  command line, so `$(origin CONFIRM)` is "command line" — the same gate a laptop
  run satisfies; there is no environment-exported `CONFIRM` in the job.
- **Cloud cost / what a re-dispatch does.** The suite lands `tiny`
  (`_drop_raw_events` + re-land, idempotent) and builds on BigQuery — cents per
  run; a second dispatch is idempotent (the write-back writes 0). It DOES mutate
  the shared demo `raw`/`ontime` datasets (stated, accepted: idempotent, cents,
  the demo project is not production). It destroys nothing.

## Review & stack risk

- **code-reviewer** (triggered — `.github/`, `tests/` in Scope): the workflow is
  `workflow_dispatch`-only, minimally permissioned, SHA-pinned; the new test
  pins a closed set (dispatch key, permission map, action-pin shape) exactly; no
  determinism/truth-isolation/dbt surface is touched.
- **security-reviewer** (MANDATORY — `.github/`, an `infra/` apply, WIF /
  credentials, a `CONFIRM`/cloud invocation): no committed secret; no credential
  in a log; identity via repo variables not literals; the token is minimally
  scoped and OIDC-only; the WIF condition is repo+ref; the ADC relocation admits
  no refused cloud-env name (allowlist unwidened).
- **functionality-tester** (triggered): the DONE command + the new test's
  assertions exist and exercise the claims; confirms the parity suite is what the
  job runs.
- **coherence-auditor** at exit (MANDATORY, `fix/` branch exit): the stale
  DEPLOYMENT/ROADMAP/BACKLOG "deferred / not built / manual only" sentences are
  gone; the Record-updates list matches the diff; Spanner/Composer still
  `Listed 0 items.` at exit (WIF is the one thing intentionally left up).
- **Stack risk (verify in the first hour, STOP + report before any workaround;
  findings → ARCHITECTURE §8):** (a) the exact env vars
  `google-github-actions/auth` exports at its pinned SHA (`GOOGLE_APPLICATION_
  CREDENTIALS`, `GOOGLE_GHA_CREDS_PATH`, and whether it also touches
  `CLOUDSDK_*`), and its `credentials_file_path` output name — the ADC
  relocation depends on these; (b) that google-auth in the pinned client
  resolves ADC from `$CLOUDSDK_CONFIG/application_default_credentials.json` when
  `GOOGLE_APPLICATION_CREDENTIALS` is unset; (c) the OIDC `ref` a
  `workflow_dispatch` from `main` presents (`refs/heads/main`) matches the
  provider's `github_ref` default — a dispatch from any other ref is correctly
  refused at the token exchange; (d) the apply's `Plan:` shows ONLY WIF adds and
  a post-apply `tf-plan` is `0 to change` (else a `.tf` moved → STOP).

## Out of scope (deferred, recorded)

- A **scheduled** parity run (`on: schedule`) — the row wants on-demand; a cron
  cadence is a later BACKLOG row if a case appears.
- The parity job **gating merges** (a required check) — it is a manual,
  cloud-cost run, not a per-PR check; making it required would meter every PR.
  BACKLOG if wanted.
- Composer running the parity/landing on a schedule — ROADMAP item 7
  (`fix/composer-cosmos`), unrelated.
