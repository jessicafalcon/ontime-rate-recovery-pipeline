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
   destroy-by date; the spec's Evidence carries the actual line. The
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
   `docs/DEPLOYMENT.md` carries the trial dates written on apply day.
   *Evidence: row 5.*
6. **Every variable-taking/destructive target has a threat model with a
   pin.** The new targets' five columns are pinned in `tests/`; the existing
   targets' table below is audited against `tests/test_makefile.py` and any
   gap fixed in-phase. *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_writeback.py::test_spanner_writeback_second_run_writes_zero`, `…::test_spanner_guard_and_write_are_one_retried_transaction` (fakes that EXECUTE the SQL on in-process DuckDB; Amendment A); live: `make test-int-spanner …` output `writeback OK: <project>.ontime → spanner, 20 users, 0 written` on run 2 + equal row hash |
| 2 | `tests/test_writeback.py::test_version_orders_numerically_v10_beats_v2`, `…::test_malformed_version_refuses`, `…::test_malformed_version_refuses_on_the_insert_path_too` (Amendment B); mutation lines 1–2 |
| 3 | `tests/test_writeback.py::test_reader_relations_per_target`, `…::test_writeback_reads_only_scores_and_dim_current` (the two read statements), `…::test_fakes_execute_the_read_contract`, `…::test_columns_are_the_golden_nine_and_row_of_maps_by_name` (ONE column tuple, values by name), `tests/test_pipeline.py` (existing `SEND_SCHEDULE_SHA256_TINY` pin unchanged), `tests/test_truth_isolation.py` |
| 4 | `tests/integration/test_int_spanner.py::test_goldens_match_with_federated_dims`, `…::test_federated_view_rows_equal_seed`, `…::test_build_read_dims_through_the_federation_view` (dbt's manifest resolved the source to the view — the falsifier, Amendment C; live, behind `OTR_INT`); offline: `tests/test_dbt_sources.py` (identifier var + view SQL rendered with casts, hand edit fails), `tests/test_spanner_landing.py::test_dbt_build_admits_exactly_one_var_override` |
| 5 | `tests/test_infra.py::test_spanner_module_is_count_gated_and_default_off`, `…::test_every_declared_resource_type_is_on_the_allowlist` (the gated modules' own exact allowlists), `…::test_spanner_grants_are_scoped_to_the_one_database_and_connection`, `…::test_spanner_names_pin_the_python_constants`, `…::test_input_shape_validations_exist` (`region`) (static), live `tf-plan` outputs (default: no spanner resource; toggled: only the module's), the dated `docs/DEPLOYMENT.md` lines, teardown apply output |
| 6 | `tests/test_makefile.py::test_writeback_target_confirm_from_command_line_only`, `…::test_spanner_targets_pass_variables_as_one_literal`, `tests/test_spanner_landing.py::test_int_spanner_cli_refuses_a_non_tiny_profile`, `…::test_cloud_landings_refuse_manifest_drift`, `…::test_spanner_clients_disable_the_builtin_metrics_exporter`; the audited table in Threat model |

**Live status (2026-08-30, `ontime-rate-recovery`):** the live halves of
Done-when 1, 4 and 5 ran after the ask-first apply:
- Apply (operator ADC, after `gcloud iam service-accounts undelete
  100054726773702357820` + `terraform import module.iam.google_service_account.pipeline …`):
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
  `docs/DEPLOYMENT.md` § Spanner. **Done-when 1–6 are met, live and offline.**

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
  connection + `raw.dim_user_spanner` view + TWO scoped grants, both to the
  pipeline SA: `databaseUser` on the one database (it is also the principal
  the federated read runs as — `databaseUser ⊇ databaseReader`) and
  `bigquery.connectionUser` on the one connection (querying a view over
  `EXTERNAL_QUERY` needs use rights on its connection). Round 1 finding 19
  counted three; Amendment D removed the service-agent grant on the first
  live apply — no such identity is on the Spanner federation path.
- **The federation swap is a generated source-identifier var, default
  unchanged (item 6)** — satisfies invariant 4 and §3.3's "source-config
  swap, no model changes". Making the view the default `bigquery` source was
  rejected: it would chain every free-tier parity run to a trial-clock
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
      propagation, trial mechanics)
- [ ] `BACKLOG.md` — close the read-seam row and the `model_version` row;
      re-defer the discriminator row and the `loader/` rename row (new
      triggers, item 2/3); the trial row closed or re-dated on apply day
- [ ] `docs/DEPLOYMENT.md` — Spanner apply/teardown runbook; the dated trial
      lines (apply day); MODULE wording corrected
- [ ] README — only if a command block it shows changes; else none
- [ ] docs/RESULTS.md / docs/METRICS.md — none (no metric, no simulation
      change)

## Threat model (REQUIRED)

New/changed targets (each: one-line recipe, value single-quoted via
`$(call _Q,$(value VAR))`, unexported, validated in one Python process before
any path/client):

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make writeback TARGET=spanner PROJECT=<id> CONFIRM=yes` | empty TARGET → `duckdb` (today's behaviour); empty PROJECT with TARGET=spanner → refusal before any client | PROFILE/TARGET validated `[a-z0-9_]+`, PROJECT by GCP shape — `../x` refused, no path derived from user input | reaches Python as one literal, fails validation | TARGET/PROJECT from env reach Python (stated residual, validated the same); CONFIRM counts only command-line | required for TARGET≠duckdb, `$(origin CONFIRM)` | `tests/test_makefile.py::test_writeback_target_confirm_from_command_line_only`, `tests/test_writeback.py::test_cloud_writeback_refuses_before_any_client` |
| `make spanner-load PROFILE=<p> PROJECT=<id> CONFIRM=yes` | empty PROFILE/PROJECT → refusal | refused by shape validation | one literal, fails validation | same residual; CONFIRM command-line only | `$(origin CONFIRM)` | `tests/test_makefile.py::test_spanner_targets_pass_variables_as_one_literal` |
| `make test-int-spanner PROJECT=<id> CONFIRM=yes [PROFILE=tiny]` | empty → refusal before `OTR_INT` export | refused | one literal | same residual; CONFIRM command-line only | `$(origin CONFIRM)` | same test; gating mirrors `test-int-bigquery` (CONFIRM first, then env) |
| `make tf-apply … VARS='enable_spanner=…'` | existing target, unchanged — but while Spanner is UP, an apply that omits `VARS` IS the teardown (the toggle defaults false; `deletion_protection = false`, `-auto-approve`): the runbook says plan first (round 1 finding 12) | n/a (VARS items validated `name=value`) | refused by VARS item validation | env-origin VARS refused (`$(origin VARS)`); `TF_VAR_*` refuses every `tf-*` | `$(origin CONFIRM)` | existing `tests/test_makefile.py::test_tf_targets_pass_vars_as_one_literal`, `tests/test_infra.py` |

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
  (`databaseUser` + `connectionUser` for the SA, nothing for anyone else —
  no admin), no key material, gating before clients, the toggle-flip destroy
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
  (eventual consistency on first apply); whether the 100-PU instance is
  inside the trial's bounds; `google-cloud-spanner`'s client surface for
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

## Out of scope (deferred, recorded)

- The `computed_as_of` discriminator redesign — BACKLOG, re-deferred with the
  item-2 trigger.
- `loader/` → `landing/` + `pipeline/cli.py` — `fix/landing-package` after
  merge (BACKLOG, item 3).
- Spanner change streams / Dataflow (the prod dims path §3.3 names) — not
  demo scope; no BACKLOG row (a non-goal until a phase needs it).
- The CI WIF leg — unchanged, its dated BACKLOG row stands.
- Composer — Phase 11.
