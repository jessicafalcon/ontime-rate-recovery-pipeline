# Phase 10 — Spanner: dims and write-back (PROPOSED)

Contract for the `phase-10-spanner-writeback` branch. Source: docs/PHASES.md
Phase 10. Depends on Phase 9b (PR #13) and `fix/tf-vars-argv` merged — both are
on main (`a7e22bf`).

**Status: PROPOSED — do not start until approved.** Dependency:
`google-cloud-spanner`, the pre-approved Phase 10 package (CLAUDE.md
allowlist), declared as a direct dependency the way 9b declared the bigquery
clients. Nothing else; any other package is a STOP-and-ask. If Spanner's
federation (`EXTERNAL_QUERY` over a Cloud Spanner connection) or its type
mapping to BigQuery turns out to lack something this spec assumes, that is a
STOP-and-report, not a workaround.

## Reconciliation against main (first commit on the branch)

Main as it actually is: 9b merged (PR #13), `fix/tf-vars-argv` merged
(`a7e22bf` — toggles reach Terraform only as command-line `VARS='name=value,…'`
→ argv `-var`; any `TF_VAR_*`/`TF_CLI_ARGS*` refuses every `tf-*`; the
terraform child runs under an env allowlist). The GCP stack on
`ontime-rate-recovery` was destroyed 2026-08-30 — nothing is up; the
`ontime-pipeline` SA id is soft-deleted until ~2026-09-29, so any apply before
then runs the undelete + import detour (docs/DEPLOYMENT.md). Terraform runs on
operator ADC, never the impersonated SA (§8). Positions, numbered as posed:

1. **BACKLOG "The write-back reads DuckDB relation names on a DuckDB
   connection only" — DUE, done here.** `serving/` gains a TARGET-keyed read
   seam: target → (connection, relation names), the `Golden`-style relation
   override (`eval/golden.py::select_sql`;
   `tests/integration/test_int_bigquery.py::_rows` is the shape). ONE knob,
   two named configurations — no read×write matrix: `TARGET=duckdb` (default,
   unchanged) reads `main_scores.scores_send_time` /
   `main_marts.dim_user_current` on `data/<p>.duckdb` and writes the DuckDB
   stand-in; `TARGET=spanner` reads `<project>.ontime.scores_send_time` /
   `….dim_user_current` through an injectable BigQuery client (the
   `loader/bq.py::Clients` factory pattern — the offline suite injects fakes)
   and writes Spanner. `Candidate`, `should_replace` and the winner
   computation are shared verbatim; only the reader and the applier dispatch.

2. **BACKLOG "model_version compares as a string" — DUE;
   "computed_as_of is not a complete discriminator" — NOT pulled,
   re-deferred.** Phase 10 bumps nothing and introduces no second version:
   the model is untouched, `v1` stays the only live version. But the phase's
   own Done-when — "an older `model_version` never overwrites a newer one" —
   is a for-all claim that lexical ordering falsifies at `v10` vs `v2`, so
   the comparator row is due NOW, before a Spanner table can ever hold a
   version the guard mis-orders: `should_replace` orders `model_version` by
   its parsed numeric form (`v(\d+)` → int; any other shape is a loud refusal,
   never a lexical fallback). The contract's wording is unchanged — replace
   only on strictly greater `(model_version, computed_as_of)`, key `user_id`.
   The discriminator row stays open: no score changes without an advancing
   `computed_as_of` and no dim change enters this phase, and replacing the
   discriminator (content hash / monotonic row version) is a design change to
   the 8a contract this phase is told to keep. Re-deferred with the trigger:
   "a profile/backfill that changes a served row without advancing
   `computed_as_of`, a `tz`/dim change landing mid-schedule, or a second
   `model_version` coexisting live — a fix PR against the 8a contract".

3. **BACKLOG "`loader/` holds more than the landing" — separate `fix/` branch
   AFTER Phase 10 merges, not this reconciliation.** The rename
   (`loader/` → `landing/`, `dbt_build`/`int_bigquery` → `pipeline/cli.py`)
   is mechanical, zero-behaviour and record-heavy; folding it into a Spanner
   phase doubles the review surface and blurs the diff the agents scope to.
   Phase 10 adds one landing file (`loader/spanner.py`, the dims → Spanner
   landing — the third engine of the one landing contract, which is exactly
   why it belongs in the landing package and moves with the rename). Row
   re-deferred with the trigger "fix/landing-package, branched after Phase 10
   merges, before Phase 11".

4. **PHASES' `make tf-destroy MODULE=spanner` — no `MODULE` variable exists;
   the scoped teardown is a toggle-flip apply.** The spanner module is
   `count`-gated on `enable_spanner`, so the scoped destroy that actually
   works with `fix/tf-vars-argv`'s mechanism is
   `make tf-apply PROJECT=<id> CONFIRM=yes VARS='enable_spanner=false'` —
   count → 0, Terraform plans exactly the module's resources for destruction,
   the argv is the whole input, and the existing `$(origin CONFIRM)` gate
   already makes it prompt-unless-command-line. No `MODULE` variable and no
   `-target` (Terraform itself flags `-target` as for exceptional
   circumstances — it bypasses the dependency graph, and it would punch a new
   argv surface through the allowlisted runner for nothing the toggle doesn't
   already do). `docs/PHASES.md`'s Done-when wording and
   `docs/DEPLOYMENT.md`'s "Phase 10 adds a scoped `make tf-destroy
   MODULE=spanner`" sentence are corrected at exit — the spec and DECISIONS
   are authoritative (TEMPLATE rule).

5. **BACKLOG "Spanner 90-day trial expiry" — DUE on apply day; the teardown
   date is pinned at apply time.** The first `enable_spanner=true` apply
   starts the trial clock (~$65/mo after day 90). The runbook step in
   `docs/DEPLOYMENT.md` § "Spanner trial" is filled in the same working
   session as the apply: apply date, trial end (apply + 90 days), and the
   destroy-by date; the spec's Evidence carries the actual line. *(Corrected
   2026-08-31, Amendment M: there is no trial clock — the instance is
   `PROVISIONED` and bills from creation; the dated lines are apply/teardown
   times.)* The
   integration run's runbook tears Spanner down the same day
   (`VARS='enable_spanner=false'` re-apply), so the steady state stays
   meter-off. And PHASES names the threat-model sweep as a Phase 10 goal:
   this spec's Threat model covers every new/changed variable-taking or
   destructive target in full and audits the existing ones against their
   pins.

6. **`dim_user` federation: it lives in the spanner Terraform module; the
   offline test is fakes + a generated-SQL pin — no service in `make test`.**
   Spanner (the production dims home, §2.3) gets a `dim_user` table (SCD2,
   key `(user_id, valid_from)`); `make spanner-load` lands
   `dims/dim_user.csv` into it through an injectable Spanner client
   (`loader/spanner.py`, the `bq.py` fake pattern). The federation is a
   BigQuery `EXTERNAL_QUERY` over a `google_bigquery_connection`
   (CLOUD_SPANNER type, `us-central1`) wrapped in a view
   `raw.dim_user_spanner`, both created by the spanner module (count-gated
   with everything else; ~~the connection's service agent gets a scoped
   `spanner.databaseReader`~~ — Amendment D: the federated read runs as the
   querying SA, no agent). The source swap §3.3 promises is real and
   proven with no model change: the generated `sources.yml` gives the
   `dim_user` source `identifier: "{{ var('dim_user_identifier',
   'dim_user') }}"` (default = today's landed table, every existing build
   byte-identical), and the integration run builds BigQuery with
   `dim_user_identifier: dim_user_spanner` and reproduces the three goldens.
   Offline: the view SQL and the Spanner DDL are rendered from
   `generator/models.py` columns and pinned by equality tests
   (`gen-sources`-style — hand edits fail); the landing and write path run
   against fakes; no cloud call, no network. The view's `select` casts each
   column to the generated BigQuery landing schema's type (the Spanner→
   BigQuery type map is a stack risk, verified live first).

## Why

Phase 8a's write-back proved the serving contract on a DuckDB stand-in;
Phase 9 put the pipeline's read side on BigQuery. The serving story ends at a
real serving store: Spanner holds `send_schedule` (write target) and
`dim_user` (the production dims home BigQuery federates from). Without this
phase the write-back is wired to one local file and §3.3's last two stub rows
stay stubs. A fix PR can't carry it: it needs Terraform resources, a new
cloud write path, and a landing — a phase's worth of invariants.

## The central constraint

**The serving contract does not move while the store under it changes.**
Replace only on strictly greater `(model_version, computed_as_of)`, key
`user_id`; two runs over the same scores write 0; `written_at =
computed_as_of`; the write-back reads only `scores_send_time` +
`dim_user_current` and re-derives nothing. Every DuckDB gate stays
byte-identical (`SEND_SCHEDULE_SHA256_TINY` and every Phase 3–9 pin
unchanged), and a default `tf-plan`/`tf-apply` still creates zero Spanner
resources — the meter stays off until the one ask-first apply, and off again
after the same-day teardown.

## DONE command

```
make test && make lint && make review-gate SPEC=specs/phase-10-spanner-writeback.md
```

- `make test` — the offline suite: the seam's fakes (BigQuery read, Spanner
  write, dims landing), the numeric version order, the generated DDL/view/
  sources pins, the static Terraform property checks, every existing pin
  byte-identical. No service, no network.
- `make lint` — ruff clean.
- `make review-gate SPEC=…` — every Evidence id and target exists; Record
  updates present in the diff.
- The live gate — `make test-int-spanner PROJECT=<id> CONFIRM=yes` after an
  ask-first `tf-apply … VARS='enable_spanner=true'` (undelete + import detour
  first while the SA id is reserved, until ~2026-09-29) — is cloud-cost,
  ask-first, run as in 9b and recorded in Evidence; teardown the same day.

## Done-when

1. **Idempotent on Spanner.** Two `make writeback TARGET=spanner` runs over
   the same scores leave `send_schedule` unchanged (row hash over the nine
   columns sorted by `user_id`); the second prints `0 written`. *Evidence:
   row 1.*
2. **An older `model_version` never overwrites a newer one — numerically.**
   `v2 > v10` lexically, yet the `v10` row survives a `v2` candidate and a
   `v10` candidate replaces a `v2` row; a malformed version refuses loudly.
   *Evidence: row 2.*
3. **One read seam, both targets.** The write-back on any target reads
   exactly `scores_send_time` + `dim_user_current` for that target and
   nothing else; `TARGET=duckdb` output is byte-identical to Phase 8a's
   (`SEND_SCHEDULE_SHA256_TINY` holds). *Evidence: row 3.*
4. **Federation reproduces the goldens.** The BigQuery build with the
   `dim_user` source swapped to the federated view
   (`dim_user_identifier: dim_user_spanner`) reproduces the three goldens
   byte-for-byte; the view returns exactly the seed's rows. *Evidence:
   row 4.*
5. **Meter off by default; scoped teardown works and is dated.** A default
   plan creates zero Spanner resources; `VARS='enable_spanner=true'` plans
   exactly the module's; the toggle-flip re-apply destroys them all and
   `docs/DEPLOYMENT.md` carries the apply/teardown dates written on apply day.
   *Evidence: row 5.*
6. **Every variable-taking/destructive target has a threat model with a
   pin.** The new targets' five columns are pinned in `tests/`; the existing
   targets' table below is audited against `tests/test_makefile.py` and any
   gap fixed in-phase. *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_writeback.py::test_spanner_writeback_second_run_writes_zero`, `…::test_spanner_guard_and_write_are_one_retried_transaction` (fakes that EXECUTE the SQL on in-process DuckDB; Amendment A); live: `make test-int-spanner …` output `writeback OK: <project>.ontime → spanner, 20 users, 0 written` on run 2 + equal row hash |
| 2 | `tests/test_writeback.py::test_version_orders_numerically_v10_beats_v2`, `…::test_malformed_version_refuses`, `…::test_malformed_version_refuses_on_the_insert_path_too` (Amendment B), `…::test_duckdb_writeback_is_one_transaction` + `…::test_duckdb_target_is_single_writer` (Amendment H — the DuckDB half of "across runs"), `…::test_duckdb_writeback_rolls_back_before_close` (round 3 #5 — the explicit rollback on the still-open connection), `…::test_spanner_rows_come_from_the_library_by_name` (Amendment N3 — the REAL Spanner read path, offline, into the guard); mutation lines 1–2, 9 |
| 3 | `tests/test_writeback.py::test_reader_relations_per_target`, `…::test_writeback_reads_only_scores_and_dim_current` (the two read statements), `…::test_fakes_execute_the_read_contract`, `…::test_columns_are_the_golden_nine_and_row_of_maps_by_name` (ONE column tuple, values by name), `…::test_candidates_are_read_by_column_name` (Amendment I — the read by name too), `…::test_existing_pairs_are_read_by_column_name` (round 3 #3 — the stored pair by name; a non-str cell refuses, N3), `…::test_spanner_rows_come_from_the_library_by_name` (N3 — `to_dict_list` on real `StreamedResultSet`s), `…::test_bigquery_rows_come_from_the_library_by_name` (O4 — `Row.items()` on real bigquery `Row`s; `candidate_of` refuses a wrong-typed cell), `tests/test_pipeline.py` (existing `SEND_SCHEDULE_SHA256_TINY` pin unchanged), `tests/test_truth_isolation.py` |
| 4 | `tests/integration/test_int_spanner.py::test_goldens_match_with_federated_dims`, `…::test_federated_view_rows_equal_seed`, `…::test_build_read_dims_through_the_federation_view` (dbt's manifest resolved the source to the view — the falsifier, Amendment C; live, behind `OTR_INT`); offline: `tests/test_dbt_sources.py` (identifier var + view SQL rendered with casts, hand edit fails), `tests/test_spanner_landing.py::test_dbt_build_admits_exactly_one_var_override`, `…::test_cell_refuses_instead_of_coercing` + `…::test_row_width_drift_refuses` (Amendment J — the landing refuses what the contract does not admit) |
| 5 | `tests/test_infra.py::test_spanner_module_is_count_gated_and_default_off`, `…::test_every_declared_resource_type_is_on_the_allowlist` (the gated modules' own exact allowlists), `…::test_spanner_grants_are_scoped_to_the_one_database_and_connection`, `…::test_spanner_custom_role_is_the_exact_data_plane_set` (Amendment E), `…::test_spanner_names_pin_the_python_constants`, `…::test_input_shape_validations_exist` + `…::test_region_is_validated_wherever_it_is_declared` (`region`, root and every module) (static), live `tf-plan` outputs (default: `No changes` — no spanner resource; toggled: only the module's), the dated `docs/DEPLOYMENT.md` lines, teardown apply output |
| 6 | `tests/test_makefile.py::test_writeback_target_confirm_from_command_line_only`, `…::test_writeback_passes_target_and_project_as_one_literal`, `…::test_spanner_targets_pass_variables_as_one_literal`, `…::test_tf_apply_allow_destroy_from_command_line_only`, `tests/test_spanner_landing.py::test_int_spanner_cli_refuses_a_non_tiny_profile`, `…::test_cloud_landings_refuse_manifest_drift`, `…::test_spanner_clients_disable_the_builtin_metrics_exporter` (the whole tracked tree), `…::test_every_cloud_command_refuses_a_credential_in_the_env` (Amendments G → N2 → O1: six entry points × thirteen unlisted names — illustrative; the policy is the `CLOUD_ENV_ALLOW` allowlist over the closed domain), `…::test_conftest_scrub_uses_the_cloud_env_policy` (N2/O6 — a child pytest proves the scrub, and that the probe bites without it), `…::test_int_spanner_fixture_refuses_without_the_carried_gate` (origin re-checked through `infra.cli.confirmed`, the one predicate — round 3 #4), `tests/test_infra.py::test_apply_plans_first_and_refuses_destroys_without_allow_destroy` + `…::test_apply_refuses_unknown_actions_even_with_allow_destroy` (Amendments F → K → N1: the action allowlist, `SAFE_ACTIONS` pinned exactly), `…::test_cli_refuses_a_credential_in_the_env_loudly` (N2/O3: unlisted names refuse, the three listed settings pass, the child's vendor names ⊆ the allowlist), `…::test_cloud_env_policy_covers_every_vendor_declared_name` (O1 — the closure: every name the installed libraries declare is classified exactly once); the audited table in Threat model |

**Live status (2026-08-30, `ontime-rate-recovery`; re-proven 2026-08-31 —
02:30 UTC for Amendments E–F and 06:07 UTC for N3, those amendments carry
the lines):** the live halves of
Done-when 1, 4 and 5 ran after the ask-first apply:
- Apply (operator ADC, after `gcloud iam service-accounts undelete
  <sa-unique-id>` (the numeric id, read from the local gitignored state backup — not a record) + `terraform import module.iam.google_service_account.pipeline …`):
  toggled plan `Plan: 27 to add, 0 to change, 0 to destroy` (18 root + the
  module's 9 as then written; the imported SA absent from the list); first
  apply 26/27 — the service-agent grant failed (`Service account
  service-<number>@gcp-sa-bigqueryconnection… does not exist`) → **Amendment
  D** removed it; the toggled re-plan: `No changes. Your infrastructure
  matches the configuration.` (module = 8 resources).
- As the SA (impersonated ADC): `spanner-load OK: tiny — 22 dim rows`;
  `make test-int-spanner PROJECT=ontime-rate-recovery CONFIRM=yes` →
  **`4 passed in 221.01s`** (view rows ≡ seed; dbt's manifest resolved
  `dim_user` to `raw.dim_user_spanner`; three goldens byte-identical off the
  federated build; write-back twice, second writes 0, read-back hash ==
  `SEND_SCHEDULE_SHA256_TINY`); then `make writeback TARGET=spanner
  PROJECT=ontime-rate-recovery CONFIRM=yes` → `writeback OK:
  ontime-rate-recovery.ontime → spanner, 20 users, 0 written`.
- Teardown, same session (operator ADC): `make tf-plan …
  VARS='operator_principal=user:<operator>'` → `Plan: 0 to add, 0 to change,
  8 to destroy` (exactly the module's), `make tf-apply … CONFIRM=yes` →
  `Apply complete! Resources: 0 added, 0 changed, 8 destroyed` (23:50 UTC);
  `gcloud spanner instances list` → `Listed 0 items.`; the dated lines are in
  `docs/DEPLOYMENT.md` § Spanner. The DEFAULT plan after it (toggle at its
  default, operator grant kept): `No changes. Your infrastructure matches the
  configuration.` — zero Spanner resources (Done-when 5's first clause,
  round 2 #21). **Done-when 1–6 are met, live and offline** at that HEAD.
- **Amendments E–F verified live (2026-08-31, `ontime-rate-recovery`):**
  - F first, by accident: `make tf-apply … CONFIRM=yes
    VARS='enable_spanner=true'` (the applied `operator_principal` omitted)
    planned `9 to add, 0 to change, 1 to destroy` and was refused — `tf-apply:
    refused — the plan destroys
    module.iam.google_service_account_iam_member.operator_token_creator[0]; …
    the VARS you passed omit a toggle that is currently applied`, exit 2,
    `Listed 0 items.`, state 21 — the omitted-toggle scenario the amendment
    was written for.
  - E: the apply carrying both toggles (02:30 UTC, operator ADC, no detour):
    `Plan: 9 to add, 0 to change, 0 to destroy` → `Apply complete! 9 added`;
    re-plan `No changes`; `gcloud iam roles describe ontimeSpannerDataUser`
    = the module's 11 permissions exactly (GA, no `updateDdl`); the database's
    IAM policy holds that one binding → the pipeline SA. As the SA under it:
    `spanner-load OK: tiny — 22 dim rows`; `make test-int-spanner …` →
    **`4 passed in 239.42s`**; `writeback OK: ontime-rate-recovery.ontime →
    spanner, 20 users, 0 written`.
  - Teardown (02:48 UTC, operator ADC): `make tf-apply … CONFIRM=yes
    VARS='enable_spanner=false,operator_principal=…' ALLOW_DESTROY=yes` →
    `Plan: 0 to add, 0 to change, 9 to destroy` (the module's 8 + the custom
    role) → `Apply complete! 0 added, 0 changed, 9 destroyed`; `Listed 0
    items.`; state 21; default plan `No changes`. The role is soft-deleted
    for 7 days; the third apply re-created it with no detour (the provider
    undeletes on create).

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. For all targets and score sets, running the write-back twice over the same scores leaves `send_schedule` byte-identical — the second run writes 0. | `tests/test_writeback.py::test_spanner_writeback_second_run_writes_zero` — fake Spanner store, two runs, mutation ledger empty on run 2 |
| 2. For all stored/candidate pairs, a row is replaced iff `(model_version, computed_as_of)` is strictly greater under NUMERIC version order; a version outside `v<int>` refuses before any write. | `tests/test_writeback.py::test_version_orders_numerically_v10_beats_v2` — stored `v10` vs candidate `v2` and the reverse; `…::test_malformed_version_refuses` |
| 3. For all targets, the write-back reads only `scores_send_time` and `dim_user_current` (that target's relations) and writes only `send_schedule` — never truth, never raw. | `tests/test_writeback.py::test_reader_relations_per_target` — the reader's SQL names exactly the two relations per target; `tests/test_truth_isolation.py` over `serving/`, `loader/` |
| 4. For all dims rows, the federated read returns exactly the rows the seed landed, and the view/DDL SQL is a function of `generator/models.py` (never hand-edited). | `tests/test_dbt_sources.py` extension — regenerate-and-compare goes red on a hand edit; live `tests/integration/test_int_spanner.py::test_federated_view_rows_equal_seed` |
| 5. For all default plans/applies, zero Spanner resources exist; every Spanner resource is inside the count-gated module. | `tests/test_infra.py::test_spanner_module_is_count_gated_and_default_off` — greps/parses the module + main.tf: `count = var.enable_spanner ? 1 : 0` on every resource path, default false |
| 6. For all cloud/destructive targets, validation and `CONFIRM`/`$(origin)` gating happen before any client or terraform child is constructed. | `tests/test_makefile.py::test_writeback_target_confirm_from_command_line_only`, `…::test_spanner_targets_pass_variables_as_one_literal`; `tests/test_writeback.py::test_cloud_writeback_refuses_before_any_client` |

```mutations
serving/writeback.py::should_replace        invert-guard
serving/writeback.py::version_key           constant-return:(0,)
serving/spanner.py::apply_writeback         delete-call
loader/spanner.py::load_dims                constant-return:0
loader/cli.py::dbt_vars_args                constant-return:[]
infra/cli.py::planned_changes               constant-return:[]
infra/cli.py::unsafe_changes                constant-return:[]
infra/cli.py::unlisted_cloud_env            constant-return:[]
infra/cli.py::refuse_cloud_env              delete-call
infra/cli.py::confirmed                     constant-return:True
serving/writeback.py::existing_of           invert-guard
```

(The federation view and Spanner DDL are SQL rendered by
`scripts/gen_dbt_sources.py` — upheld by the equality tests in the table,
per the TEMPLATE's SQL rule.)

## Pinned decisions (do not re-litigate)

- **One `TARGET` knob, two named configurations (reconciliation item 1)** —
  satisfies invariants 1, 3. `duckdb` = read DuckDB, write the stand-in
  (byte-identical to 8a); `spanner` = read BigQuery `ontime`, write Spanner.
  A read×write matrix was rejected: no configuration in it serves anything.
- **`version_key` parses `v<int>`; anything else refuses (item 2)** —
  satisfies invariant 2. A lexical fallback was rejected: it silently
  re-introduces the `v10 < v2` bug the clause exists to kill.
- **The Spanner write is Python-computed winners + `insert_or_update`
  mutations through an injectable client, inside ONE read-write
  transaction (Amendment A)** — satisfies invariants 1, 2, 6.
  `run_in_transaction(fn)`: `fn` reads the stored pairs → shared
  `should_replace` → batch upsert of winners only; `written_at =
  computed_as_of`. Server-side conditional DML was rejected: it would fork
  the guard's logic into a second dialect the offline suite can't run; the
  fake models the store (in-process DuckDB executing the same SQL) and
  retries `fn` once like Spanner does.
- **All Spanner resources live in the count-gated module, DDL inlined in the
  module's `.tf` (heredoc), instance at the smallest size (100 processing
  units)** — satisfies invariant 5. A separate `.sql` file was rejected:
  `tf-freeze`'s manifest pins `*.tf`/`*.tf.json` only, and a file it doesn't
  pin can drift under the frozen tree. The module also owns the BigQuery
  connection + `raw.dim_user_spanner` view + ONE custom data-plane role
  (`ontimeSpannerDataUser`: read/select/write/transactions/sessions — no
  `updateDdl`; Amendment E) + TWO scoped grants, both to the pipeline SA:
  that role on the one database (it is also the principal the federated
  read runs as) and `bigquery.connectionUser` on the one connection
  (querying a view over `EXTERNAL_QUERY` needs use rights on its
  connection). Round 1 finding 19 counted three; Amendment D removed the
  service-agent grant on the first live apply — no such identity is on the
  Spanner federation path; round 2 #1 replaced `databaseUser` (it carries
  `updateDdl`) with the custom role.
- **The federation swap is a generated source-identifier var, default
  unchanged (item 6)** — satisfies invariant 4 and §3.3's "source-config
  swap, no model changes". Making the view the default `bigquery` source was
  rejected: it would chain every free-tier parity run to a billing
  Spanner stack.
- **Scoped teardown = toggle-flip apply; no `MODULE`, no `-target`
  (item 4)** — satisfies invariants 5, 6 with zero new argv surface;
  `$(origin CONFIRM)` already gates it. PHASES/DEPLOYMENT wording corrected
  at exit.

## Scope (files)

- `serving/writeback.py` — `version_key`, reader dispatch; `serving/spanner.py`
  (new) — the Spanner client protocol, fake-injectable, `apply_writeback`;
  `serving/readers.py` or in-module reader table (implementation's call, same
  relations either way); `serving/cli.py` + `Makefile` — `writeback
  [TARGET=duckdb|spanner] [PROJECT= CONFIRM=]`, `spanner-load`,
  `test-int-spanner`.
- `loader/spanner.py` (new) — dims → Spanner landing; `loader/cli.py` —
  `spanner-load`, `test-int-spanner` entries (moves to `landing/`/`pipeline/`
  in the later fix branch, item 3).
- `infra/modules/spanner/{main,variables,outputs}.tf`, `infra/main.tf`
  (wiring), `infra/MANIFEST.sha256` (via `make tf-freeze CONFIRM=yes`).
- `scripts/gen_dbt_sources.py` + generated `dbt/models/staging/sources.yml`,
  new generated Spanner DDL/view pins; `dbt_project.yml`
  (`dim_user_identifier` default).
- `tests/test_writeback.py`, `tests/test_makefile.py`, `tests/test_infra.py`,
  `tests/test_dbt_sources.py`, `tests/test_spanner_landing.py` (new — the
  dims landing against fakes, the CLI gates, the one-var build seam),
  `tests/integration/test_int_spanner.py` (new), `tests/pins.py` (Spanner
  row-hash pin if frozen-able; else live-only).
- Records: see Record updates.

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 10 entries (seam shape, version_key, toggle-flip
      teardown, federation-as-var, DDL-in-tf)
- [ ] `docs/PHASES.md` — Phase 10 Done-when as landed (MODULE wording
      corrected); "Delivered" paragraph
- [ ] `CLAUDE.md` — Current status; Commands (`writeback TARGET`,
      `spanner-load`, `test-int-spanner`); Repo map (`serving/spanner.py`,
      `loader/spanner.py`, spanner module); allowlist note
      (google-cloud-spanner declared); BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §2.3/§3.3 rows marked delivered as landed;
      §8 Gotchas for every live surprise (type map, connection IAM
      propagation, cost mechanics)
- [ ] `BACKLOG.md` — close the read-seam row and the `model_version` row;
      re-defer the discriminator row and the `loader/` rename row (new
      triggers, item 2/3); the Spanner row re-dated on apply day (retitled round 3, Amendment M)
- [ ] `docs/DEPLOYMENT.md` — Spanner apply/teardown runbook; the dated apply/teardown
      lines (apply day); MODULE wording corrected
- [ ] README — none (no README is tracked; `check-docs` reads one only if present)
- [ ] `PROJECT_BRIEF.md` — Amendment M annotations only (a log: annotated,
      never rewritten)
- [ ] `.claude/agents/code-reviewer.md`, `.claude/agents/security-reviewer.md`,
      `.claude/agents/functionality-tester.md`, `.claude/commands/review-round.md`
      — the round-4 process rules (Boundary / Credential / Adapter contracts,
      the cap's disposition)
- [ ] docs/RESULTS.md / docs/METRICS.md — none (no metric, no simulation
      change)

## Threat model (REQUIRED)

New/changed targets (each: one-line recipe, value single-quoted via
`$(call _Q,$(value VAR))`, unexported, validated in one Python process before
any path/client):

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make writeback TARGET=spanner PROJECT=<id> CONFIRM=yes` | empty TARGET → `duckdb` (today's behaviour); empty PROJECT with TARGET=spanner → refusal before any client; empty PROFILE is allowed on the Spanner target only (the read is the warehouse's) | PROFILE/TARGET validated `[a-z0-9_]+`, PROJECT by GCP shape — `../x` refused, no path derived from user input | reaches Python as one literal, fails validation (pinned on TARGET and PROJECT — round 2 #15) | TARGET/PROJECT from env reach Python (stated residual, validated the same); CONFIRM counts only command-line; any name in the closed cloud-env domain outside `CLOUD_ENV_ALLOW` in the env refuses (Amendments G → N2 → O1) | required for TARGET≠duckdb, `$(origin CONFIRM)` | `tests/test_makefile.py::test_writeback_target_confirm_from_command_line_only`, `…::test_writeback_passes_target_and_project_as_one_literal`, `tests/test_writeback.py::test_cloud_writeback_refuses_before_any_client`, `tests/test_spanner_landing.py::test_every_cloud_command_refuses_a_credential_in_the_env` |
| `make spanner-load PROFILE=<p> PROJECT=<id> CONFIRM=yes` | empty PROFILE/PROJECT → refusal | refused by shape validation | one literal, fails validation | same residual; CONFIRM command-line only | `$(origin CONFIRM)` | `tests/test_makefile.py::test_spanner_targets_pass_variables_as_one_literal` |
| `make test-int-spanner PROJECT=<id> CONFIRM=yes [PROFILE=tiny]` | empty → refusal before `OTR_INT` export | refused | one literal | same residual; CONFIRM command-line only | `$(origin CONFIRM)` | same test; gating mirrors `test-int-bigquery` (CONFIRM first, then env) |
| `make tf-apply … VARS='enable_spanner=…' [ALLOW_DESTROY=yes]` | existing target; Amendment F: it plans FIRST and applies the saved plan, and a plan that destroys anything is refused unless `ALLOW_DESTROY=yes` — so an apply that omits a currently-applied toggle cannot tear Spanner down (round 1 #12 → round 2 #3); a plan that cannot be read back — envelope or entry — or one carrying an action outside `SAFE_ACTIONS ∪ {delete}` (`forget`, a future verb) is refused ALWAYS (K → N1); empty ALLOW_DESTROY → refusal on destroys only | n/a (VARS items validated `name=value`; ALLOW_DESTROY takes the literal `yes` only) | refused by VARS item validation; ALLOW_DESTROY compared as a literal | env-origin VARS refused (`$(origin VARS)`); env-origin ALLOW_DESTROY reads `environment` and is refused (`$(origin ALLOW_DESTROY)`); `TF_VAR_*` refuses every `tf-*`; any name in the closed cloud-env domain outside `CLOUD_ENV_ALLOW` refuses every `tf-*` (Amendments G → N2 → O1) | `$(origin CONFIRM)` | `tests/test_makefile.py::test_tf_targets_pass_vars_as_one_literal`, `…::test_tf_apply_allow_destroy_from_command_line_only`, `tests/test_infra.py::test_apply_plans_first_and_refuses_destroys_without_allow_destroy`, `…::test_cli_refuses_a_credential_in_the_env_loudly` |

Cloud cost twice / destroys: `writeback TARGET=spanner` twice is the
idempotence proof (writes 0; cents of reads); `spanner-load` twice
re-upserts identical dims (idempotent); `test-int-spanner` twice re-runs
reads + the writeback (cents); the toggle-flip apply destroys exactly the
spanner module's resources and nothing else in state.

Existing-target sweep (PHASES names it for Phase 10) — AUDITED 2026-08-30
against `tests/test_makefile.py`. Every variable-taking or destructive
target's five columns are pinned; the shared mechanics (one-line recipe,
`$(call _Q,$(value VAR))` unexpanded+single-quoted, `unexport`, Python
validation before any path/client, `$(origin CONFIRM)`) are proven per
target by:

| Target(s) | Pinned by (`tests/test_makefile.py::`) |
|---|---|
| `review-gate` / `mutate` (SPEC, BASE, DELETED) | `test_user_variable_reaches_python_as_one_literal_from_both_origins`, `test_env_exported_spec_reaches_the_recipe_and_is_validated_in_python`, `test_base_defaults_to_main_and_deleted_is_optional` |
| `seed` (PROFILE) | `test_profile_reaches_python_as_one_literal_from_both_origins` |
| `freeze` (PROFILE, CONFIRM) | `test_freeze_requires_confirm_from_the_command_line` |
| `load` (PROFILE, THROUGH) | `test_load_and_dbt_build_pass_profile_and_target_as_one_literal`, `test_load_passes_through_as_one_literal` |
| `dbt-build` (PROFILE, TARGET, CONFIRM, FULL, THROUGH, PROJECT) | the two above + `test_dbt_build_full_refresh_from_command_line_only`, `test_dbt_build_passes_through_as_one_literal`, `test_bq_targets_pass_project_as_one_literal`, `test_bq_targets_confirm_from_command_line_only` |
| `bq-load` / `test-int-bigquery` (PROFILE, PROJECT, CONFIRM) | `test_bq_targets_pass_project_as_one_literal`, `test_bq_targets_confirm_from_command_line_only` |
| `drop-db` (PROFILE, CONFIRM — the deleter) | `test_drop_db_requires_confirm_from_the_command_line` |
| `attribution-golden` / `eval` / `scores-golden` / `report` / `simulate` (PROFILE, WRITE) | `test_golden_and_eval_pass_profile_as_one_literal`, `test_scores_golden_passes_profile_as_one_literal`, `test_report_passes_profile_as_one_literal`, `test_simulate_passes_profile_as_one_literal` (WRITE takes the literal `yes` only — validated in `eval/cli.py`) |
| `power` (WRITE) | `test_power_passes_write_as_one_literal` |
| `writeback` / `pipeline` (PROFILE; Phase 10: TARGET, PROJECT, CONFIRM) | `test_writeback_and_pipeline_pass_profile_as_one_literal`, `test_writeback_target_confirm_from_command_line_only` |
| `spanner-load` / `test-int-spanner` (PROFILE, PROJECT, CONFIRM) | `test_spanner_targets_pass_variables_as_one_literal` |
| `test-int-airflow` (takes NO variable) | `test_test_int_airflow_takes_no_variable_and_exports_otr_int` |
| `tf-validate` (takes NO project) | `test_tf_validate_takes_no_project` |
| `tf-plan` / `tf-apply` / `tf-destroy` (PROJECT, CONFIRM, VARS) | `test_tf_targets_pass_project_as_one_literal`, `test_tf_targets_pass_vars_as_one_literal`, `test_tf_apply_and_destroy_confirm_from_command_line_only` (+ `tests/test_infra.py`: env-`TF_VAR_*`/`TF_CLI_ARGS*` refusal, env allowlist, auto-tfvars refusal) |
| `tf-freeze` (CONFIRM — overwrites the pin) | `test_tf_freeze_confirm_from_command_line_only` |

No gap found: every target with a variable, a delete, a cloud call, or a
confirmation knob has a one-literal pin and (where destructive/cloud) an
`$(origin CONFIRM)` pin. Stated residual, unchanged since Phase 2:
`MAKEFLAGS`/`MFLAGS` reach recipes from the environment — mistakes, not a
user who controls the environment (the threat model's standing carve-out).

## Review & stack risk

- **code-reviewer** (triggered — Python, dbt sources, Makefile, tests, `.tf`):
  seam correctness, contract unchanged, truth isolation, generated-never-
  hand-edited, no clock on the write path.
- **security-reviewer** (mandatory — `infra/`, `serving/`, IAM grants, new
  CONFIRM-gated cloud/destructive targets): scoped grants only
  (the custom data-plane role + `connectionUser` for the SA, nothing for
  anyone else — no DDL, no admin; Amendment E), no key material, gating
  before clients, the toggle-flip destroy
  path.
- **functionality-tester** (after code-reviewer): DONE command, the fakes'
  negative tests (v10/v2, malformed version, refusal-before-client), mutation
  lines.
- **coherence-auditor** at exit (mandatory — phase exit): the `MODULE=spanner`
  sentence gone from PHASES and DEPLOYMENT; §3.3's stub rows updated; BACKLOG
  rows moved exactly as item 2/3/5 say; CLAUDE.md status/commands/count.
- Stack risk, verified in the first hour live (STOP-and-report on surprise;
  findings → §8): the Spanner→BigQuery type map through `EXTERNAL_QUERY`
  (TIMESTAMP/DATE/STRING casts to the generated landing schema); the
  BigQuery connection's service-agent identity existing before the IAM grant
  (eventual consistency on first apply); the 100-PU instance's cost model
  (found round 3: `PROVISIONED`, bills from creation — Amendment M);
  `google-cloud-spanner`'s client surface for
  batch `insert_or_update` (the fake models exactly the calls used).

## Amendments (review round 1, 2026-08-30)

- **A — the stored-pair read and the winners' upsert are ONE Spanner
  read-write transaction (finding 7; a write-path change).** Restores
  **invariant 2** (replace iff strictly greater) ACROSS concurrent
  write-backs, not only within one run: a snapshot read followed by a
  separate batch commit let two overlapping runs each compare against a
  stale pair and one lose its update. Mechanism:
  `serving/spanner.py::GoogleSpannerClient.transact(fn)` =
  `database.run_in_transaction(fn)`; `fn` reads `EXISTING_SQL` on the
  transaction, computes the winners with the shared guard, and
  `insert_or_update`s them; Spanner aborts and re-runs `fn` when a read pair
  moved, so `fn` is re-runnable by construction (it recomputes from what it
  reads). The `SpannerClient` protocol is `transact` + a snapshot `read` for
  readers (the integration read-back). Pinned by
  `tests/test_writeback.py::test_spanner_guard_and_write_are_one_retried_transaction`
  (the fake aborts attempt 1 with a real rollback, re-runs, and asserts no
  read outside the transaction). Rejected: a `lifecycle`/lock-file
  serialization at the DAG level — `max_active_runs=1` already orders the
  Airflow runs, but the guard's own contract must not depend on who calls it.
- **B — the candidate's version parses BEFORE the absent-row shortcut
  (finding 1, BLOCKER).** Restores **invariant 2**'s "refuses before any
  write" on the INSERT path: `should_replace` returned `True` for an absent
  row without parsing, so a malformed `model_version` (a `--vars` override)
  would be stored and every later run would raise on the stored value.
  Mechanism: `version_key(candidate.model_version)` is computed first on
  every path. Pinned by
  `…::test_malformed_version_refuses_on_the_insert_path_too` (unit, and
  end-to-end over an empty fake store: nothing written).
- **C — the build's var seam admits exactly one var, validated (finding 5).**
  Restores **invariant 6** (validation before any client) for the internal
  seam: `loader/cli.py::dbt_build(dim_user_identifier=…)` replaces the
  free-text `dbt_vars`; the value is a `[a-z0-9_]+` relation name rendered
  by `dbt_vars_args` into `--vars {dim_user_identifier: <name>}` — no other
  var has a path in. Done-when 4 becomes falsifiable live:
  `tests/integration/test_int_spanner.py::test_build_read_dims_through_the_federation_view`
  reads dbt's own `manifest.json` for the swapped build and asserts the
  `dim_user` source resolved to `raw.dim_user_spanner` (the goldens alone
  could not tell the view from the landed table).
- Also applied in the same round, no design change: `COLUMNS`/`row_of` by
  field NAME shared by both writers (finding 2); fakes that execute the SQL
  on in-process DuckDB, naive warehouse timestamps vs aware store ones
  (findings 3, 4); the two Spanner clients set
  `disable_builtin_metrics=True` (finding 8); `region` gains a Terraform
  `validation {}` (finding 9); grant scope + the gated modules' own resource
  allowlists + the `.tf` names pinned to the Python literals (findings 10,
  11, 14); the connection ordered after its API (finding 13 — its
  service-agent half was overtaken by Amendment D); the view casts each column to the landing schema's type as this spec
  said (finding 15); the DDL/view are labelled PINNED, not generated
  (finding 16); the transitive set recorded (finding 17); three grants
  (finding 19); `writeback OK: <project>.ontime → spanner` with PROFILE
  optional there (finding 20); `test-int-spanner` refuses a non-tiny PROFILE
  at the CLI (finding 23); the `open_rows` count off `tests/pins.py`
  (finding 22); docstrings (finding 24).

## Amendments (first live apply, 2026-08-30)

- **D — no service-agent grant: the Spanner federated read runs as the
  querying principal (a design change: who-gets-what).** Restores
  **invariant 5**'s live half (the toggled apply creates exactly the
  module's resources) and keeps invariant-4's least-privilege claim honest.
  Found on the first `enable_spanner=true` apply: 26 of 27 resources
  created, then `Error 400: Service account
  service-<number>@gcp-sa-bigqueryconnection.iam.gserviceaccount.com does
  not exist` on `connection_reader` — even ordered after the connection
  (finding 13's fix), because nothing on this path ever provisions that
  agent. The docs (`bigquery/docs/spanner-federated-queries`) say why: for
  Cloud Spanner connections the USER OR SERVICE ACCOUNT RUNNING THE QUERY
  needs `roles/spanner.databaseReader` on the database and
  `roles/bigquery.connectionUser` on the connection — the delegated
  service-agent model is Cloud SQL's, not Spanner's. Mechanism: the
  `connection_reader` resource and the module's `project_number` input are
  deleted; the pipeline SA's existing `databaseUser` (⊇ `databaseReader`)
  + `connectionUser` are the whole grant set (two, both to the SA).
  Rejected: provisioning the agent (`gcloud beta services identity create`)
  to make the grant apply — it would grant a non-participating identity
  read on the database, the over-broad grant the security review exists to
  catch. Pinned by `tests/test_infra.py::
  test_spanner_grants_are_scoped_to_the_one_database_and_connection` (two
  grants, no `gcp-sa-bigqueryconnection` anywhere in the module) and the
  member/role pins. ARCHITECTURE §8 carries the gotcha; the apply resumed
  with the corrected module.

## Amendments (review round 2, 2026-08-30)

- **E — the pipeline SA's database grant is a custom data-plane role, not
  `roles/spanner.databaseUser` (finding 1; who-gets-what).** Restores the
  least-privilege claim behind **invariant 5**'s module ("no admin, no
  DDL — Terraform owns the schema"): every predefined Spanner role that can
  write also carries `spanner.databases.updateDdl`, so the SA could have
  altered or dropped `send_schedule`/`dim_user`. Mechanism:
  `google_project_iam_custom_role.data_user` (`ontimeSpannerDataUser`) in
  the spanner module with EXACTLY the data-plane set — read, select, write,
  the two transaction kinds, sessions, the two metadata reads — granted on
  the one database. It lives in the module, not the root, because a custom
  role may only carry permissions of an enabled API (docs) and the module
  enables Spanner's. Cost: a deleted custom role reserves its id for 7 days
  (the SA's 30-day shape) — the runbook carries the `gcloud iam roles
  undelete` + import detour. Pinned by
  `tests/test_infra.py::test_spanner_custom_role_is_the_exact_data_plane_set`
  (exact set, a control-plane pattern denylist, no `roles/spanner.*`
  anywhere, the role only in the module). Rejected: recording the residual
  (the review asked for the tightening); Spanner fine-grained access
  control (database roles + `databaseRoleUser` — a second access model the
  clients would have to name on every call). **Verified live 2026-08-31**
  (spec Live status: the live permission set is the module's, `4 passed`
  under it).
- *(Mechanism superseded by Amendment N1, round 4, and O2, round 5 — the gate is an action allowlist; the plan-first shape stands.)* **F — `tf-apply` plans first, applies the SAVED plan, and refuses a plan
  that destroys anything unless `ALLOW_DESTROY=yes` has command-line origin
  (finding 3; a new argv surface).** Restores **invariant 5**'s operational
  half (a default-toggle apply creates — and now DESTROYS — nothing Spanner)
  and turns round 1 #12's prose into a mechanism: an apply that omits a
  currently-applied toggle used to be a silent `-auto-approve` teardown.
  Mechanism: `infra/cli.py::_apply` = `plan -out=<tmp>` → `show -json` →
  `planned_deletes` (any `resource_changes[].change.actions` containing
  `delete`; a replace counts) → `require_allow_destroy` → `apply <tmp>`;
  the plan file holds variable values so it lives in TMPDIR and is removed
  on every path; `-auto-approve` is gone from apply (a saved plan applies
  without a prompt; the plan you were shown is the apply you get).
  `ALLOW_DESTROY` is the one new make variable: `$(origin)`-gated like
  CONFIRM, unexported, one literal (threat-model row above). `tf-destroy`
  is unchanged — destruction is its purpose and CONFIRM covers it. Pinned
  by `tests/test_infra.py::test_apply_plans_first_and_refuses_destroys_without_allow_destroy`,
  `tests/test_makefile.py::test_tf_apply_allow_destroy_from_command_line_only`.
  Rejected: `prevent_destroy` (blocks the sanctioned toggle-flip too);
  inferring intent from the VARS content (a heuristic on the toggle names).
  **Verified live 2026-08-31** (spec Live status: the omitted-toggle apply
  refused with the address printed; the `ALLOW_DESTROY=yes` toggle-flip
  destroyed exactly the module's 9).
- *(Mechanism superseded by Amendment N2, round 4, and O1, round 5 — the domain is allowlisted and closed by the vendors' declarations; the rule stands.)* **G — a credential-bearing environment variable refuses EVERY cloud
  command, loudly, before any client or child (finding 2).** Restores
  **invariant 6** for the identity, not just the confirmation: the google
  clients honour `GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CREDENTIALS`/
  access-token variables, so a key in the env would silently have been the
  SA for `spanner-load`, `bq-load`, `writeback TARGET=spanner`, the cloud
  `dbt-build` and both `test-int-*`; `infra/cli.py`'s allowlist dropped it
  from terraform SILENTLY (applying as ADC while the operator believed the
  key was in use). Mechanism: ONE policy, `infra.cli.KEYFILE_ENV_RE` +
  `refuse_keyfile_env`, matched by name SHAPE (`GOOGLE_*CREDENTIALS*`, the
  two token variables) so a new spelling is caught; called from the ONE
  gate every cloud command passes (`loader.cli.require_confirm` — the cloud
  `dbt-build` now goes through it too) and from `tf()` for
  plan/apply/destroy. `tests/conftest.py` scrubs those names so a
  developer's export never reddens the suite. Pinned by
  `tests/test_spanner_landing.py::test_every_cloud_command_refuses_a_credential_in_the_env`
  (6 entry points × 5 shapes, factories and `subprocess.run` armed to fail)
  and `tests/test_infra.py::test_cli_refuses_a_credential_in_the_env_loudly`.
- **H — the DuckDB write-back is ONE transaction (finding 8; the DuckDB
  half of Amendment A).** Restores **invariant 2**'s "across runs" on the
  stand-in: `write_back` wraps read → guard → delete+insert in
  `begin`/`commit`, rolling back on any exception, so a failure mid-apply
  leaves the rows the run started from. Across processes DuckDB is
  single-writer (a second process cannot open the file while this one holds
  it) — stated in the docstring and pinned by
  `…::test_duckdb_target_is_single_writer` (a subprocess probe) beside
  `…::test_duckdb_writeback_is_one_transaction` (a delete-then-raise
  applier; the table hash is unchanged, the next run repairs).
- **I — the READ maps by column name, like the write (finding 9).** Restores
  **invariant 3**'s "exactly these columns" for the values, not just the
  relations: `candidates_sql`'s select list is GENERATED from
  `Candidate`'s fields (tz from the dims alias, the rest from the scores
  alias) and `candidate_of(row_by_name)` builds the dataclass by name,
  refusing a missing or extra column; `QueryClient.query` returns dicts
  (BigQuery's `Row.items()`), DuckDB's cursor `description` supplies the
  names. Pinned by `…::test_candidates_are_read_by_column_name` (shuffled
  keys land by name; missing/extra refuse; the select list is the field
  list).
- **J — the dims landing refuses what the contract does not admit
  (findings 10, 11).** Restores **invariant 4** on the landing's input side
  (the seed's rows, not just its header): an empty cell in a REQUIRED
  field, a timestamp carrying an offset (the contract is naive UTC), and a
  row with the wrong number of cells are `ValueError`s naming the field (the
  width check also names the CSV line), never a coerced value or a
  positionally shifted row. Pinned by
  `tests/test_spanner_landing.py::test_cell_refuses_instead_of_coercing`,
  `…::test_row_width_drift_refuses` (the tester's surviving mutation, now
  killed).
- Also applied, no design change: the metrics pin scans every tracked
  `*.py` (#5); `region` validated in the root AND every module that
  declares it, two-digit regions admitted (#6); `carried_gate` re-checks the
  origin through the one `confirmed` predicate (`infra.cli` since round 3 #4) — the make target's own,
  never a forged literal (#7, both integration modules); the fake's
  `transact` rolls back on an exception like `run_in_transaction` (#13);
  invariant 1's ledger assertion (#14); `writeback`'s TARGET/PROJECT
  quoting pinned (#15); the SA unique id redacted from the spec and the
  BACKLOG note extended (#4); CLAUDE status de-contradicted (#16, #18,
  #19); PHASES Delivered paragraph (#17); scaling bounds recorded (#20);
  the default plan recorded (#21).

## Amendments (review round 3, 2026-08-31)

- *(Superseded by Amendment N1, round 4.)* **K — `planned_deletes` fails CLOSED (findings 1 — code-reviewer #1,
  security-reviewer #2).** Restores **invariant 6** for the plan-first
  apply: the `ALLOW_DESTROY` gate must run on EVIDENCE of no deletes, never
  on the absence of a readable plan. Amendment F parsed `show -json` with
  `json.loads(show_json or "{}")` and `.get("resource_changes", [])`, so an
  empty body, a body without `resource_changes`, or a shape change on a
  provider/terraform upgrade counted as "no deletes" and the saved plan
  applied without the gate, while a malformed body was an uncaught
  traceback. Mechanism: `planned_deletes` refuses (`die`, exit 2, the
  reason named) when the body is empty, is not JSON, is not an object, or
  has no `resource_changes` list; only a parsed list — possibly empty —
  yields `[]`. The saved plan file is still removed on that path. Pinned by
  `tests/test_infra.py::test_apply_plans_first_and_refuses_destroys_without_allow_destroy`
  (the four bad bodies refuse before `apply`; the `planned_deletes("") ==
  []` pin is inverted). Rejected: a `try/except` that falls back to
  requiring `ALLOW_DESTROY` on an unreadable plan (a gate that fires for the
  wrong reason teaches the operator to pass the flag).
- *(Superseded by Amendment N2, round 4, and O1, round 5.)* **L — the keyfile-env policy covers the whole google-auth family
  (finding 2 — code-reviewer #2, security-reviewer #1).** Restores
  **invariant 6** (the identity half, Amendment G): `KEYFILE_ENV_RE` matched
  `GOOGLE_*CREDENTIALS*` and two tokens, so `GOOGLE_CLOUD_KEYFILE_JSON`,
  `GCLOUD_KEYFILE_JSON` (the google provider's other two documented
  credential variables) and `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` fell
  through to the allowlist and were dropped SILENTLY — the exact failure G
  says it removes. Mechanism: ONE pattern still, widened by family —
  `GOOGLE_.*CREDENTIALS.*`, `(GOOGLE|GCLOUD)_.*KEYFILE.*`,
  `GOOGLE_OAUTH_ACCESS_TOKEN`, `CLOUDSDK_AUTH_(ACCESS_TOKEN|CREDENTIAL_FILE_OVERRIDE)`;
  `tests/conftest.py`'s scrub reads the same pattern. Pinned by the widened
  parametrisations in `tests/test_infra.py::test_cli_refuses_a_credential_in_the_env_loudly`
  and `tests/test_spanner_landing.py::test_every_cloud_command_refuses_a_credential_in_the_env`.
  Rejected: refusing every `CLOUDSDK_AUTH_*` (`…_IMPERSONATE_SERVICE_ACCOUNT`
  is a gcloud setting, not a credential — a false refusal on the runbook's
  own path).
- **M — records: the Spanner instance is `PROVISIONED` and bills from
  creation; there is no trial clock (session finding 11).** Not a design
  change — a correction of a false premise carried since the architecture
  review. The module creates a `PROVISIONED` 100-PU instance (live listing
  `INSTANCE_TYPE PROVISIONED`); a Spanner free-trial instance is a separate
  kind, created through the console/gcloud, one per project, and never
  what Terraform makes here (official docs, checked 2026-08-31). So the
  cost model is ~$0.09/hour while up (~$65/month), from the first minute —
  the two 2026-08-30/31 sessions cost ~5 cents — and the operating rule is
  what the runbook already does for the wrong reason: apply, prove, tear
  down in the same session; never leave it up. The "trial ends
  2026-11-28" / "free while up" sentences are replaced everywhere
  (DEPLOYMENT § Spanner and its cost row, this spec's item 5 / Done-when 5 /
  stack risk, BACKLOG's row retitled, PHASES, CLAUDE, ARCHITECTURE §6,
  DECISIONS' two mentions). The BACKLOG row stays open with the trigger
  "every phase exit: no Spanner instance is up".
- Also applied, no design change: the Spanner stored-pair read maps by
  column name like the candidate read (#3, missed in round 2 — `EXISTING_COLUMNS`
  generates `EXISTING_SQL`, `existing_of` maps the row; the Txn `read`
  returns dicts); ONE `confirmed` predicate — it lives in `infra/cli.py`
  beside the keyfile policy (loader.cli imports from infra.cli already; the
  reverse would cycle) and `drop_db`, `loader.cli.require_confirm`,
  `infra.cli.require_confirm` and the integration fixtures all call it (#4;
  the mutations line moves with it); the explicit `con.rollback()` pinned
  through an injected, still-open connection that must see the table
  unchanged before `close` (#5); the tfstate-bucket row (#6, accepted —
  BACKLOG, trigger "before the next `enable_spanner=true` apply");
  DEPLOYMENT's operator-permissions row for the custom role (#7);
  `main.tf`'s two stale comments (#8, #9); DECISIONS' round-1 #12 entry
  annotated Superseded by Amendment F (#10); the composer `region` comment
  (#12); `test_cli_builds_the_expected_argv`'s docstring (#13); Amendment
  J's "with the line" wording (#14); CLAUDE's `tf-apply | tf-destroy`
  header (#15).

## Amendments (review round 4, 2026-08-31)

Round 4 found correctness defects inside round 3's fixes for the second
round running (K → #3–#5, L → #7, the by-name read → #1/#2): the review cap.
The cause is structural, not a missed case — each fix was a longer DENYLIST
at an open-world boundary (Terraform's plan JSON, Google's env-var namespace,
Spanner's result set), and a denylist at such a boundary has no last fix.
Per the cap rule, the three boundaries are re-implemented ONCE against a
closed set or the real type; no per-case patch is applied.

- **N1 — the apply gate is an ACTION ALLOWLIST (findings 3, 4, 5 —
  code-reviewer #2, security-reviewer #1/#2/#3).** Restores **invariant 6**
  for the plan-first apply and supersedes Amendment K: the saved plan
  applies iff every `resource_changes[]` entry parses to `(address: str,
  actions: list[str])` and every action is inside `SAFE_ACTIONS = {no-op,
  read, create, update}`; `delete` (a replace is a delete) is admitted only
  by `ALLOW_DESTROY=yes` with command-line origin (through the one
  `confirmed` predicate); ANY other verb — `forget` (a state drop that
  leaves the instance billing), a verb a later Terraform adds — and any
  entry that does not parse is a refusal ALWAYS, the addresses named, the
  plan file removed. A shape we do not recognise is not safe. Mechanism:
  `infra/cli.py::planned_changes` (strict parse, refuses on any unreadable
  shape — K's envelope checks and the per-entry ones in one place) →
  `unsafe_changes(changes, allowed)` → `require_safe_plan`. Pinned by
  `tests/test_infra.py::test_apply_plans_first_and_refuses_destroys_without_allow_destroy`
  (bad envelopes AND bad entries refuse before `apply`; `SAFE_ACTIONS`
  pinned as the exact set) and
  `…::test_apply_refuses_unknown_actions_even_with_allow_destroy`; the
  mutations line becomes `unsafe_changes constant-return:[]`. Rejected: one
  more per-verb branch (the next verb is the next finding); `prevent_destroy`
  (blocks the sanctioned toggle-flip).
- **N2 — the Google env namespace is an ALLOWLIST (finding 7 —
  security-reviewer #4; the tester's b3′ survivor, finding 8).** Restores
  **invariant 6**'s identity half and supersedes Amendments G and L: a
  cloud command runs only if every environment variable whose name starts
  `GOOGLE_`, `GCLOUD_` or `CLOUDSDK_` is in `CLOUD_ENV_ALLOW` — the settings
  the runbook actually uses (`CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT`,
  `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT`, `CLOUDSDK_PYTHON`,
  `GOOGLE_CLOUD_PROJECT`). Every other name in the namespace is treated as
  a credential until listed and refused loudly, names only, before any
  client or child — so a key, token or credential-file variable under ANY
  spelling, present or future, can never become a client's identity
  silently; the standard for a new vendor is its prefix plus its allowed
  settings, one line and a DECISIONS entry. `KEYFILE_ENV_RE` is deleted;
  `refuse_keyfile_env` becomes `refuse_cloud_env` (same call sites: the one
  gate `loader.cli.require_confirm`, `tf()`); the terraform child's
  `ENV_ALLOW` may name a vendor variable only if `CLOUD_ENV_ALLOW` admits it
  (pinned); `tests/conftest.py`'s scrub calls the same `unlisted_cloud_env`
  and a test pins that it does. A false refusal on a benign new setting is
  the intended failure direction. Pinned by the widened
  `tests/test_infra.py::test_cli_refuses_a_credential_in_the_env_loudly`
  (unlisted names refuse, listed settings pass),
  `tests/test_spanner_landing.py::test_every_cloud_command_refuses_a_credential_in_the_env`
  and `…::test_conftest_scrub_uses_the_cloud_env_policy`. Rejected: a
  generic secret-shape denylist over the whole environment (`*_TOKEN`,
  `*_KEY` — false refusals on unrelated tools, and still a denylist); the
  family regex widened once more.
- **N3 — the Spanner adapter is the library's own by-name call, tested on
  the real type (findings 1, 2, 6 — code-reviewer #1/#9,
  functionality-tester #1/#2).** Restores **invariants 1 and 2** on the
  served path: `_rows_by_name` re-implemented `StreamedResultSet.to_dict_list()`
  (google-cloud-spanner 3.70.0) and executed nowhere — the DuckDB-backed
  fakes re-mapped rows themselves, and the last live run predates it.
  Mechanism: `_GoogleTxn.read` / `GoogleSpannerClient.read` return
  `execute_sql(sql).to_dict_list()`; `_rows_by_name` is deleted; the test
  builds `StreamedResultSet`s offline from `PartialResultSet` protos
  (metadata + zero rows, one row with a shuffled column order, a
  zero-response stream) and runs them through the REAL adapter classes into
  `existing_of` — the empty-table read of the first write-back after a
  fresh apply included. The DuckDB-side mapping is ONE helper
  (`writeback.rows_by_name(cursor)`) that `read_candidates`, `read_existing`
  and both fakes share (finding 27), and `existing_of` refuses a non-`str`
  `user_id`/`model_version` instead of coercing (finding 6 — J's rule on the
  read). Pinned by `tests/test_writeback.py::test_spanner_rows_come_from_the_library_by_name`;
  the mutations block gains `serving/writeback.py::existing_of invert-guard`.
  **Re-proven live 2026-08-31** (06:07–06:42 UTC, the third session, operator
  ADC for `tf-*`, the SA for the data path): toggled apply `9 added`
  (the custom role re-created with no undelete detour), re-plan `No
  changes`; `spanner-load OK: tiny — 22 dim rows`; `test-int-spanner`
  `4 passed in 248.70s`; `writeback OK: ontime-rate-recovery.ontime →
  spanner, 20 users, 0 written`; toggle-flip `9 destroyed`, `Listed 0
  items.`, state 21, re-plan `No changes` — the `to_dict_list` adapter is
  the read path that ran. (A first teardown attempt failed at refresh: the
  ADC login had picked the git-only account — DEPLOYMENT step 5, §8.)
  Rejected: a fake `StreamedResultSet` class of our own
  (the seam under review would again be ours, not the library's).
- Also applied, no design change: finding 9 is withdrawn (the plan-file
  removal IS pinned — `test_cli_builds_the_expected_argv`, hand-mutation g1
  KILLED); Amendment M's four survivors — `infra/main.tf`,
  `infra/variables.tf`, `infra/modules/spanner/main.tf` (×2) — reworded and
  re-frozen, PROJECT_BRIEF's four trial sentences annotated (a log —
  annotated, not rewritten), BACKLOG's Spanner row de-contradicted (#10–#13);
  ARCHITECTURE §6's "state in GCS" corrected to the local-state fact the
  BACKLOG row records (#14); CLAUDE's Repo map moves `confirmed()` to
  `infra/` (#15); the env-allowlist prose names the exact set (#16, CLAUDE +
  DEPLOYMENT); Evidence row 6's count and the two unlisted round-3 tests
  (#17, #18); the mutations block (#19); DEPLOYMENT's `roleAdmin` row
  carries the verification command (#20); the tfstate row's cleartext
  clause (#21); the `GOOGLE_*CREDENTIALS*` shorthand replaced by the
  allowlist wording wherever an operator would test their env against it
  (#22); K/N1's unreadable-plan refusal stated in the runbook (#23);
  `confirmed`'s docstring names its scope — the pipeline CLIs; `make
  freeze` is the generator's own literal (#24); the composer `region`
  comment (#25); `existing_sql`'s docstring (#26); the untracked README
  named as such (#28).

## Amendments (review round 5, 2026-08-31)

Round 5 was the cap's scoped re-review of N1–N3. Its correctness findings
sit inside the re-implementation, and each has the same shape: the KIND is
right (allowlist, strict parse, the library's call) but the SET was not yet
closed — the prefix tuple was still hand-picked, the parser admitted one
degenerate shape, the adapter contract was applied to one of two clients.
Amendment O closes each set by construction; nothing here is a longer list.

- **O1 — the cloud-env domain is closed by the vendors' own declarations
  (round 5 #1 — code-reviewer #1 BLOCKER, security-reviewer #1; #7).**
  Restores **invariant 6**'s identity half: `SPANNER_EMULATOR_HOST` (the
  Spanner client then uses `AnonymousCredentials()` against a named host —
  the write-back and the dims landing leave the project silently),
  `BIGQUERY_EMULATOR_HOST`, `STORAGE_EMULATOR_HOST`, `GCE_METADATA_HOST` /
  `_ROOT` / `_IP` and `NO_GCE_CHECK` all passed N2's three prefixes.
  Mechanism: the refused domain is `in_cloud_namespace(name)` = a prefix in
  `CLOUD_ENV_PREFIXES` (`GOOGLE_`, `GCLOUD_`, `CLOUDSDK_`, `GCE_METADATA_`)
  OR a suffix in `CLOUD_ENV_SUFFIXES` (`_EMULATOR_HOST`) OR a name in
  `CLOUD_ENV_NAMES` (the prefix-less inputs the installed libraries read:
  `NO_GCE_CHECK`, `APPENGINE_RUNTIME`, `API_ENDPOINT_OVERRIDE`,
  `API_VERSION_OVERRIDE`, `DATASTORE_DATASET`, the four `SPANNER_*`
  settings); `CLOUD_ENV_IGNORED` names the five `AWS_*` inputs google-auth
  reads only for an AWS external-account ADC file this project has no path
  to (a false refusal on an unrelated tool's variable). The CLOSURE is a
  test, not a claim: `tests/test_infra.py::test_cloud_env_policy_covers_every_vendor_declared_name`
  imports `google.auth.environment_vars`, `google.cloud.environment_vars`
  and the spanner / bigquery / storage client constants and asserts every
  declared name is classified exactly once — refused, an admitted setting,
  or ignored — so a library upgrade that adds an input reddens the suite
  until it is classified. `unlisted_cloud_env({}) == []` is pinned (#7).
  Rejected: adding `GCE_` and the emulator names to the prefix list by hand
  (the fourth hand-picked list at this boundary).
- **O2 — `planned_changes` refuses an empty action set (round 5 #2 —
  code-reviewer #2, functionality-tester #1).** `frozenset() <= allowed`
  was vacuously true, so `{"actions": []}` applied as safe. An entry must
  carry at least one action; pinned in the bad-bodies table.
- **O3 — `CLOUD_ENV_ALLOW` is the three settings the runbook uses (round 5
  #3 — code-reviewer #3).** `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT`
  (an identity selector; the runbook impersonates with the login FLAG) and
  `CLOUDSDK_PYTHON` (an interpreter path; nothing here spawns gcloud) are
  dropped: `CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT`, `GOOGLE_CLOUD_PROJECT`
  remain — each a real vendor input, none an identity.
- **O4 — the read boundary refuses wrong-typed cells on BOTH reads and the
  BigQuery adapter is tested on the real type (round 5 #9, #10 — missed in
  round 4).** `candidate_of` checks each cell against `Candidate`'s
  declared field type (the same rule `existing_of` gained in N3);
  `GoogleQueryClient.query`'s `dict(r.items())` runs in a test over real
  `google.cloud.bigquery.table.Row`s built offline (shuffled field order,
  an empty result) — the Adapter contract applied to the second client.
- **O5 — one origin predicate (round 5 #5 — security-reviewer #3).**
  `full_refresh_args` inlined the literal rule; it calls `confirmed` now,
  so the docstring's closure claim is true (the one carve-out is `make
  freeze`, in the generator).
- **O6 — the conftest scrub is pinned BEHAVIOURALLY (round 5 #6, #8 —
  functionality-tester #2, #4).** The source-grep test could be satisfied
  by a dead call; now a child pytest with the conftest copied beside a
  probe, run with unlisted names, an emulator host and a `TF_VAR_*`
  exported, must see none of them and still see the listed setting — and
  the same probe WITHOUT the conftest must fail. The scrub's Terraform half
  reads `infra.cli.ENV_REFUSE_PREFIXES` instead of its own literal.
- Also applied: the protobuf `struct_pb2` import in the N3 test replaced by
  the spanner proto's own `.pb().values.add` (#11 — the dependency
  allowlist); DEPLOYMENT step 5's token check moved off the argv (stdin
  POST) with the name-only alternative stated (#4, #12); the retired tfstate
  trigger corrected where three places still quoted it, and the row's
  confidentiality half given its own statement and trigger (#12, #13);
  DEPLOYMENT step 1's custom-role detour retired and the 2026-09-07
  pointers reworded (#14, #19, #20); DECISIONS' `CLOUDSDK_*` shorthand
  (#15); the F/G/K/L paragraphs annotated Superseded in place (#16); the
  mutations block gains `planned_changes` and `unlisted_cloud_env` (#17);
  Record-updates lists PROJECT_BRIEF and the `.claude/` prose (#18); the
  Makefile's `tf-apply` comment (#21); the code-reviewer rule narrowed to
  a CREDENTIAL's value (#22); the Credential standard's "never in a file"
  clause scoped to files the repo controls — gcloud's ADC file is the one
  credential at rest, outside the repo (#23); PROJECT_BRIEF's two undated
  annotations dated (#24).

## Amendments (review round 6, 2026-08-31)

Round 6 reported 17 findings; the architect's disposition was "only fix
security related issues". Amendment P covers the four security-boundary
findings (rows 1, 3, 5, 6 of the round table — the security-reviewer's two
should-fixes plus the two allowlist gaps); the round's other findings (the
surviving `unlisted_cloud_env({})` pin, the duplicated cell-type rule, the
records rows) are undispositioned and stay open.

- **P1 — the cloud-env domain is closed over what the installed libraries
  READ, not only what they declare (round 6 #1 — code-reviewer #1,
  security-reviewer #1, functionality-tester #1).** Restores **invariant
  6**'s identity half. O1's closure test harvested five hand-picked
  declaration modules, so a vendor input read as a string literal escaped
  it: `SPANNER_ENABLE_EXTENDED_TRACING` / `SPANNER_ENABLE_END_TO_END_TRACING`
  (literal `os.getenv` in the installed `spanner_v1`) and `GEMINI_API_KEY`
  (an API key the installed `google-genai` client reads — a locked
  dbt-bigquery transitive via `google-cloud-aiplatform`) all passed every
  cloud gate, while one member of the same class
  (`SPANNER_DISABLE_AFE_SERVER_TIMING`) had been hand-appended to the test —
  the append-the-case shape the cap forbids. Mechanism: the closure test
  unions the declaration modules with a SCAN of the installed `google/`
  namespace tree for literal `os.environ` / `os.getenv` reads (97 names
  today, floor pinned) and demands each classified exactly once — refused,
  admitted, or ignored with a recorded reason. `SPANNER_` joins
  `CLOUD_ENV_PREFIXES` (the spanner client's own settings namespace; its
  four hand-listed names retire into the prefix); `GEMINI_API_KEY` and
  `SSL_CERT_FILE` / `SSL_CERT_DIR` (trust-anchor overrides `google-genai`
  reads — the class P2 names) join `CLOUD_ENV_NAMES`; the ignored
  classification becomes recorded classes — `CLOUD_ENV_IGNORED_PREFIXES`
  (`AWS_` external-account ADC; `AIP_` / `CLOUD_ML_` / `VERTEX_`, set by
  Vertex inside its managed containers — aiplatform is on no path here)
  plus eleven names read by vendored test helpers, the aiplatform
  prediction server and protobuf's runtime switches. Rejected: narrowing
  the claim to "declarations" (the gate would stay open to the very names
  round 6 found); appending the two spanner names (the same shape again).
- **P2 — `ENV_ALLOW` drops `SSL_CERT_FILE`, `NO_PROXY`, `HTTPS_PROXY`
  (round 6 #3 — code-reviewer, missed in round 5).** The terraform child
  ran every Google API call under an operator-suppliable proxy endpoint
  and trust-anchor override — exactly the endpoint-redirection class the
  Credential standard names a secret — while the gate refused a harmless
  `GOOGLE_CLOUD_QUOTA_PROJECT`. Seven names remain (`PATH`, `HOME`,
  `TMPDIR`, `LANG`, `LC_ALL`, `CLOUDSDK_CONFIG`, `CLOUDSDK_CORE_PROJECT`).
  The runbook's network is direct (no proxy variable is set on the
  operator machine); a proxied network is a deliberate widening — one
  line plus a DECISIONS entry — not a default.
- **P3 — the child-env vendor pin uses `in_cloud_namespace` (round 6 #5 —
  security-reviewer, missed in round 5).** The pin at
  `tests/test_infra.py` checked `k.startswith(CLOUD_ENV_PREFIXES)` — a
  hand-picked subset of the domain O1 defined — so a prefix-less domain
  name added to `ENV_ALLOW` would never have been checked against
  `CLOUD_ENV_ALLOW`. It now applies the domain function itself; with
  P1 + P2 it proves the child sees no refused name at all.
- **P4 — `CLOUDSDK_CONFIG` is recorded as identity-BEARING, accepted
  (round 6 #6 — security-reviewer #2).** It selects the directory the ADC
  file lives in — which credential every google client and the terraform
  child use — a stronger selector of who acts than the impersonation
  setting O3 dropped for that reason. It stays admitted: ADC must live
  somewhere, and `HOME` (outside the domain by construction, inside
  `ENV_ALLOW`) redirects it identically, so refusing it removes nothing.
  The "none an identity" comment is corrected in place and the acceptance
  recorded in DECISIONS with this reason.

## Amendments (security re-review of Amendment P, 2026-08-31)

The security re-review of the Amendment P diff reported six findings; the
first three are the cap firing a third time on the cloud-env domain (rounds
4→N, 5→O, 6→P have each re-worked it). The cause is the same each time: P1's
mechanism — PROVING the domain is closed by SCANNING the installed `google/`
tree for env reads — is itself an open-world boundary. It leaves the
transport-redirection class open on the IN-PROCESS cloud paths (A1), and any
constant-keyed read escapes the scan entirely (A3). Per the cap, the boundary
is re-implemented ONCE, and the architect's call (2026-08-31) is to **narrow
the claim**: the refuse domain is a declared, enumerated, pinned CLOSED set;
the scan is demoted from a closure PROOF to a coverage aid.

- **Q — the cloud-env gate refuses a declared closed domain on every path;
  the scan is a discovery aid, not the proof (security re-review of P,
  findings A1–A6).** Restores **invariant 6**'s identity half and narrows
  P1. Retires the claim "the domain is closed over what the installed
  libraries READ" (the scan never closed the gate — the enumerated refuse
  domain does — and it silently misses the redirection class and every
  constant-keyed read) and the universal "a library upgrade reddens this
  test" over-claim.
  - **A1 (the real gap):** the transport-redirection class the Credential
    standard names a secret — `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`,
    `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`,
    `SSLKEYLOGFILE`, `OAUTHLIB_INSECURE_TRANSPORT`, and `SSL_CERT_FILE` /
    `SSL_CERT_DIR` (moved from `CLOUD_ENV_NAMES`) — becomes a declared closed
    set `REDIRECTION_NAMES` inside `in_cloud_namespace`, so it refuses on the
    IN-PROCESS cloud commands (`bq-load`, `test-int-bigquery`, `writeback
    TARGET=spanner`), not only the terraform child (P2 closed it for the
    child alone). P1 rejected "narrow to declarations" because it "leaves the
    gate open to the very names round 6 found" — Q does not: the refuse
    domain stays enumerated and now covers the redirection class the scan
    never did, so demoting the scan removes no protection. `APPDATA` (the
    Windows ADC config root — CLOUDSDK_CONFIG's sibling, the identity class)
    joins `CLOUD_ENV_NAMES` (A3's named escapee). The domain is pinned
    exactly; widening it is a visible edit.
  - **A2/A3:** the closure test splits. Its STRICT half asserts every name in
    the vendor DECLARATION modules (a bounded set) is classified exactly once;
    the SCAN of literal reads is a coverage aid whose newly-found names are
    checked against a RECORDED residual — `ENABLE_GCS_PYTHON_CLIENT_OTEL_TRACES`
    and the container / external-account prefixes (`AWS_` / `AIP_` /
    `CLOUD_ML_` / `VERTEX_`, inputs of tools on no path here) — never asserted
    to be closed. The over-claim "a library upgrade reddens this test" is
    dropped for the scanned/ignored prefixes; the residual is enumerated in
    DECISIONS, not asserted away. The test is renamed to what it does.
  - **A4/A5/A6:** the refusal message drops "would become the identity of
    every google client" for the redirection / non-identity part of the
    domain (names only, unchanged); the round-5 O1 paragraph and Evidence
    row 6 are annotated "narrowed by Q".

  Rejected: broadening the scan's ROOT to the transports (`requests` /
  `urllib3` / `grpc`) — the same open-world mechanism, one more package deep,
  with the next transitive the next finding.

## Out of scope (deferred, recorded)

- The `computed_as_of` discriminator redesign — BACKLOG, re-deferred with the
  item-2 trigger.
- `loader/` → `landing/` + `pipeline/cli.py` — `fix/landing-package` after
  merge (BACKLOG, item 3).
- Spanner change streams / Dataflow (the prod dims path §3.3 names) — not
  demo scope; no BACKLOG row (a non-goal until a phase needs it).
- The CI WIF leg — unchanged, its dated BACKLOG row stands.
- Composer — Phase 11.
