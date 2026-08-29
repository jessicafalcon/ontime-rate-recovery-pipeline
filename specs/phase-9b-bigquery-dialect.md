# Phase 9b — BigQuery dialect, landing, pin parity (PROPOSED)

Contract for the `phase-9b-bigquery-dialect` branch. Source: `docs/PHASES.md`
§ Phase 9 (the two warehouse clauses of its Done-when — 9a discharged the
plan-clean and destroy-empty clauses in PR #12). Depends on Phase 9a merged
(`4f26bcb`, 2026-08-29).

**Status: PROPOSED — do not start until approved.** Dependency: **`dbt-bigquery`**
(pre-approved for Phase 9, CLAUDE.md allowlist). Its transitive clients
`google-cloud-bigquery` and `google-cloud-storage` are the landing's and the
parity test's only cloud API (reconciliation item 8) — no other package; a
pinned-version feature that turns out unsupported is a STOP-and-ask.

Four sections marked REQUIRED are mandatory; a spec without them is not
approvable (CLAUDE.md → Workflow rules). ≤ 6 pinned decisions / Done-when items.

## Reconciliation against main (first commit on the branch)

Drift between the plan and what main actually is, and the carry-overs due at
9b. Facts are read off main at `4f26bcb` (Phase 9a merged). Items marked
**design change** need approval before implementation; the rest are facts.

1. **`generate_schema_name` — the BigQuery build lands inside `ontime`; DuckDB
   keeps its per-folder schemas — design change (DUE BACKLOG row "9b's dbt
   build must land inside the two Terraform datasets", Amendment I).**
   `dbt/dbt_project.yml` sets a per-folder `+schema` and has no
   `generate_schema_name`, so dbt's default would resolve `ontime_staging …
   ontime_scores` on BigQuery — five datasets Terraform never creates. 9a's pin
   is **two datasets** (`raw`, `ontime`), and the SA cannot create a third.
   Mechanism: `dbt/macros/generate_schema_name.sql` returns `target.schema`
   (`ontime`) when `target.type == 'bigquery'` and dbt's default
   (`<target.schema>_<custom>`) otherwise. It is a dbt **hook override**, not a
   dispatch macro (no `adapter.dispatch`) — the count stays five
   (`tests/test_dbt_conventions.py::test_exactly_five_dispatch_macros` keeps
   its "no dispatch anywhere else" clause). **DuckDB does not collapse:** every
   schema-qualified reader on main hard-codes `main_<folder>` — `serving/
   writeback.py` (2), `eval/golden.py` (3), `eval/score.py` (2), `eval/
   report.py`, `eval/simulate.py`, and ~30 lines across `tests/` — and no
   invariant needs one flat schema on DuckDB; collapsing would touch every
   DuckDB gate for a target that is not the one changing. Model names are
   unique project-wide, so twelve tables in one dataset collide on nothing.
   Rejected: `+schema` removed for both (touches every reader); a
   `target.name` switch (a second `duckdb`-typed target would silently
   collapse too — `target.type` names the dialect).
2. **`profiles.yml` completion, `OTR_GCP_PROJECT` from the validated
   `PROJECT`, and Amendment S lifted in the SAME commit as item 1 — design
   change (Amendments O, S).** The `bigquery` output gains `location:
   us-central1` (the datasets' — `infra/variables.tf` `region` default) and
   keeps `method: oauth` (ADC) with `env_var('OTR_GCP_PROJECT')` and **no
   default** (the round-8 pin `tests/test_infra.py::
   test_bigquery_profile_project_has_no_default` stays). `make dbt-build
   TARGET=bigquery PROFILE=<p> PROJECT=<id> CONFIRM=yes` validates `PROJECT`
   with `infra.cli.validate_project` (`PROJECT_RE`, the one shape) and sets
   `OTR_GCP_PROJECT` **inside the process** for dbt — never read from an
   unvalidated environment; an empty `PROJECT` is a refusal. `loader/cli.py`'s
   unconditional `TARGET=bigquery` refusal (S) is deleted in the commit that
   adds `generate_schema_name`; `tests/test_loader.py::
   test_bigquery_target_is_refused_before_9b` flips to its 9b form
   `test_bigquery_target_needs_a_validated_project` (a confirmed bigquery build
   with an empty / malformed `PROJECT` exits 2 before any landing or dbt call;
   a valid one reaches the injected landing). `location` is asserted by
   `tests/test_infra.py::test_bigquery_profile_location_is_the_datasets`
   (equal to the Terraform `region` default, read from `variables.tf`).
3. **The build owns the RIGHT landing: `dbt_build(TARGET=bigquery)` never calls
   the DuckDB `load()` — design change (DUE BACKLOG row "The DAG's build owns
   its landing in one task", Phase 8b Amendment 1).** `loader/cli.py::
   dbt_build` dispatches the landing by target — `duckdb → load()` (unchanged:
   CI, `make pipeline`, every DuckDB gate keep their bytes), `bigquery →
   bq_load()` (item 4's GCS→BigQuery landing, THROUGH-filtered by the same
   file-name predicate) — then runs `dbt build --target`. A new `make bq-load`
   exposes the landing alone (the `make load` twin). `orchestration/tasks.py`
   threads `TARGET` as a literal in the `dbt_build` command (`TARGET =
   "duckdb"`, so the Docker-local DAG and `test-int-airflow`'s two tables are
   byte-identical — the command string changes, the run does not;
   `tests/test_dag_structure.py` pins the new literal). The DAG stays two
   tasks: the row's alternative — a third `load` task — buys nothing while the
   Composer target is Phase 11's, and it would reshape the container test's
   attachment pin. The row closes here; a Composer-era split gets its own row
   if Phase 11 needs one. Rejected: `dbt-build` refusing `TARGET≠duckdb` and
   pointing at two commands (the PHASES Done-when names `make dbt-build
   TARGET=bigquery` itself).
4. **`test-int-bigquery` in CI needs an explicit `enable_ci_wif = true` +
   `github_repository` apply — a runbook step, ask-first, never the default
   apply (Amendments H, K; BACKLOG "Cross-warehouse dialect drift is caught
   only on DuckDB in CI").** 9b lands `make test-int-bigquery PROJECT=<id>
   CONFIRM=yes` (behind `OTR_INT`, like `test-int-airflow`; CI never runs it
   by default) and PROVES it from the laptop as the SA. The CI leg is a
   `docs/DEPLOYMENT.md` runbook step: `make tf-apply PROJECT=<id> CONFIRM=yes
   -- -var enable_ci_wif=true -var github_repository=jessicafalcon/
   ontime-rate-recovery-pipeline` (toggles as `-var`, never a tfvars — T),
   then the `workload_identity_provider` output into the workflow. **Decision
   for the architect:** (a) 9b adds a `workflow_dispatch`-only job
   `bigquery-parity` (`google-github-actions/auth` with the provider name and
   `OTR_GCP_PROJECT` from repository *variables*, skipped when unset — PR CI
   stays free and offline), live-proved only if the opt-in apply runs in 9b;
   or (b) the job is deferred and the BACKLOG row re-deferred with the trigger
   "the first `enable_ci_wif = true` apply". **Recommended: (b)** — the SA id
   is reserved until ~2026-09-28 (item 5), so a 9b apply already needs the
   undelete detour, and a WIF job that has never run is a claim; the row keeps
   a dated trigger instead. The laptop-run `test-int-bigquery` is the 9b
   Done-when (PHASES names the target, not a CI job).
5. **Live facts on `ontime-rate-recovery` — facts.** (i) The `ontime-pipeline`
   SA id is soft-deleted since the 2026-08-29 destroy and reserved until
   ~2026-09-28 (BACKLOG dated row; ARCHITECTURE §8): a `tf-apply` before then
   fails "already exists, in a deleted state". Workaround, ask-first: `gcloud
   iam service-accounts undelete <unique_id> --project=ontime-rate-recovery`
   (the numeric `unique_id` is in the local, gitignored
   `infra/terraform.tfstate.backup`; a `gcloud logging read` on the delete
   also carries it), then `terraform -chdir=infra import
   module.iam.google_service_account.pipeline
   projects/ontime-rate-recovery/serviceAccounts/ontime-pipeline@ontime-rate-recovery.iam.gserviceaccount.com`
   (state is empty after the destroy, so a bare apply would re-create), then
   `make tf-plan` (expect `17 to add` + the imported SA `0 to change`) →
   `make tf-apply`. Alternative: wait for 2026-09-28 (a bare apply then works).
   The branch does not need the apply until the first live build; every
   offline item lands first. (ii) A local `infra/terraform.tfvars` exists and
   Amendment T refuses `tf-plan`/`tf-apply` while it does — the operator
   deletes it and passes `-var`s (or `operator_principal` as a `-var`) before
   the first plan; the tree does not change for this. (iii) `gcloud` is on
   `ontime-rate-recovery` with a user account; builds run as the SA through
   `gcloud auth application-default login --impersonate-service-account=
   ontime-pipeline@…` with `operator_principal = "user:<you>"` applied (Q) —
   never Owner ADC (N).
6. **BACKLOG rows DUE at this trigger (15 open on main) — carry-overs.** DO in
   9b: **"9b's dbt build must land inside the two Terraform datasets"** (items
   1, 2, 4 — struck at exit); **"The DAG's build owns its landing in one
   task"** (item 3 — struck); **"The BigQuery landing has no
   conflicting-duplicate guard"** (trigger: the first `TARGET=bigquery` build —
   a singular dbt test `dbt/tests/assert_no_conflicting_duplicates.sql` over
   `source('raw','events')` with the loader's predicate, so BOTH dialects run
   it; struck); **"Model dialect denylist is non-exhaustive"** (trigger: the
   first BigQuery build — the build IS the complete check; each inline form it
   rejects joins the denylist; struck when the build is green, the denylist
   kept as the offline early warning); **"Cross-warehouse dialect drift is
   caught only on DuckDB in CI"** (trigger 9b exit — item 4's decision:
   struck under (a), re-deferred with the dated trigger under (b)); **"`ontime-
   pipeline` SA id is soft-deleted until ~2026-09-28"** (struck after the
   first 9b apply, whichever path). RE-DEFER with a note: **"`THROUGH` is
   validated by shape, not calendar"** — the `bq-load` landing still applies
   `THROUGH` only as a string compare over fixture file names (the GCS object
   names come from the glob, never from `THROUGH`), so the trigger ("reaches a
   path or a query") is not pulled; **"Spanner 90-day trial expiry"** —
   re-checked, no apply, unchanged; **"The cohort-moment argmax ranges over
   opened bins"** — no `window_minutes > 60` profile; its "dialect form" note
   stays hypothetical. Untriggered, untouched: golden sort key, window
   tie-break operator, `ontime_retention` golden, `model_version` string
   compare, `computed_as_of` discriminator, DAG attachment pin. Count 15 →
   **10** under (a) (five struck, none opened) or **11** under (b); a stack
   surprise that needs a row changes this at exit.
7. **`partition_by` is a config key BOTH adapters interpret — fact, found
   reading main for this commit (stack risk, first hour).** The three
   incremental models set `partition_by='event_date'` / `'prompt_date'` as a
   plain string for OUR custom strategy (`get_incremental_partition_overwrite_sql`
   reads `config.require('partition_by')`). dbt-duckdb also reads that key
   (`duckdb__get_partitioned_by`: a string is accepted, warned and ignored for
   non-DuckLake tables; a **mapping raises** in `normalize_string_or_list`),
   and dbt-bigquery parses the same key as its native partitioning **dict**
   (`{field, data_type, granularity}`) — a bare string is a compile error
   there. So no single value of `partition_by` satisfies both. Resolution
   (design change, small): the models name the overwrite column under a key
   neither adapter reads — `overwrite_partition_col` — which the strategy
   macro reads instead; and set dbt-bigquery's NATIVE `partition_by` only on
   that dialect (`partition_by=({'field': …, 'data_type': 'date'} if
   target.type == 'bigquery' else none)` inside `config()`), so the BigQuery
   tables are date-partitioned on the same column the overwrite deletes by
   (partition pruning on both the delete and the reprocess filter — the
   teaching note). DuckDB sees `none` → no warning, no change: the goldens are
   untouched. `tests/test_dbt_conventions.py` pins the key and the
   dialect-guarded dict. Rejected: a dict for both (dbt-duckdb raises); a
   string for both (dbt-bigquery raises).
8. **Auth and clients: one ADC path for landing AND build — design change.**
   The landing does not shell out to `bq`/`gsutil`: those use gcloud's own
   credential (a second impersonation setting, `auth/impersonate_service_account`),
   while dbt uses ADC. `loader/bq.py` uses `google.cloud.storage` (upload the
   selected `events_*.jsonl` + `dim_user.csv` under
   `gs://<project>-ontime/landing/<profile>/…`, the 9a staging bucket) and
   `google.cloud.bigquery` (`load_table_from_uri`, explicit schema, `WRITE_TRUNCATE`
   — recreate, the `make load` contract) — both transitive dependencies of
   `dbt-bigquery`, so the ONE impersonated ADC covers landing, build and the
   parity read; no keyfile, no second CLI config. The BigQuery table schemas
   are GENERATED from `generator/models.py` by `make gen-sources`
   (`loader/bq_schema.json`: `varchar→STRING`, `timestamp→TIMESTAMP`,
   `date→DATE`, `json→JSON`; `tests/test_dbt_sources.py` fails on a hand edit)
   — the schema contract, second dialect. Rejected: `bq load` via
   `subprocess` (two auth paths; a CLI on the data path the offline suite can
   only fake by argv); `load_table_from_file` from the laptop (no GCS landing —
   ARCHITECTURE §3 names the bucket as the landing).
9. **Drift to correct at exit — facts.** CLAUDE.md: Commands (`dbt-build`
   `PROJECT`, `bq-load`, `test-int-bigquery`; the `TARGET=bigquery is REFUSED`
   sentence deleted), Repo map (`loader/bq.py`, `bq_schema.json`, the sixth
   macro-file that is not a dispatch macro), allowlist (dbt-bigquery landed),
   Event-model facts (`partition_by` → `overwrite_partition_col`), Current
   status, BACKLOG count; `docs/ARCHITECTURE.md` §3.2 ("raises until Phase 9"
   → the bodies as landed), §3.3 (the `bq load` swap row), §8 (item 7 and
   every live surprise); `docs/PHASES.md` Phase 9 "Delivered (9b)" + the
   Done-when as landed; `DECISIONS.md` Phase 9b appendix + the Infra in-force
   line; `docs/DEPLOYMENT.md` (the build-as-SA runbook, `bq-load`, the WIF
   opt-in step, the undelete/import detour, the cost rows for a landed tiny);
   `BACKLOG.md` per item 6; `.claude/agents/{code-reviewer,coherence-auditor}.md`
   ("raises until Phase 9" wording, if present).

Design changes — items 1, 2, 3, 7, 8 (+ 4's choice) — **await approval.**
Implementation order after approval: offline first (items 1, 2, 3, 7, the
macro bodies, the generated schema, the fakes), then the first ask-first live
build at tiny as the SA, `to_local_time` across the tz-change users first.

---

## Why

Phases 0–8 run on a laptop; 9a stood up the BigQuery datasets, bucket, SA and
budget with the meter off. 9b is the first data that touches the cloud: the
five `bigquery__` macro bodies that have raised since Phase 2, the landing
GCS→BigQuery, and the proof that the second warehouse reproduces every pin the
first one froze — byte-for-byte, off the read-only `fixtures/tiny/`. A fix PR
cannot carry it: it changes what `make dbt-build` does for a non-default
target, adds a cloud-cost landing, a package, and an integration target, and it
is the phase that decides whether "one dbt project, two targets" (ARCHITECTURE
§3.2) is true.

## The central constraint

**The BigQuery build reproduces `fixtures/tiny/expected/*.csv` and every
`tests/pins.py` number byte-for-byte, and creates nothing outside the two
Terraform datasets.** `fixtures/tiny/` is read-only: parity is proven by
diffing, never by re-freezing; a differing row is a dialect bug (or a §8
gotcha), not a new golden. A sixth dispatch macro, a `default__` body, a
dataset or table outside `raw`/`ontime`, a keyfile, or a cloud call from the
offline suite is a STOP.

## DONE command

```
make test && make lint && make review-gate SPEC=specs/phase-9b-bigquery-dialect.md && make dbt-build TARGET=bigquery PROFILE=tiny PROJECT=<id> CONFIRM=yes && make test-int-bigquery PROJECT=<id> CONFIRM=yes
```

- `make test` — the offline suite: the five bodies present and no `default__`
  (static), `generate_schema_name` collapses only on `target.type == 'bigquery'`,
  the generated BigQuery schema, the landing with FAKE storage/BigQuery clients
  (selected files, `WRITE_TRUNCATE`, schema from the contract, no network), the
  build's landing dispatch, the `PROJECT`/`CONFIRM` negatives, the DAG literal.
  DuckDB `dbt build` in-process is unchanged and green — every existing golden
  gate is byte-identical.
- `make lint` — ruff.
- `make review-gate SPEC=…` — the gate + Evidence ids + Record-updates files.
- `make dbt-build TARGET=bigquery PROFILE=tiny PROJECT=<id> CONFIRM=yes` —
  **cloud, ask-first, as the SA**: lands tiny into `raw`, `dbt build` into
  `ontime` (source tests → the twelve models → data, unit and singular tests);
  prints `dbt-build OK: tiny/bigquery`.
- `make test-int-bigquery PROJECT=<id> CONFIRM=yes` — **cloud, ask-first**:
  behind `OTR_INT`; runs the landing + build in-process, then reads the three
  golden tables from BigQuery through the same `Golden` specs and diffs them
  against `fixtures/tiny/expected/`, asserts `ONTIME_RATE`, `LABEL_ACCURACY`
  (labels vs truth — `eval` is still the only truth reader), and the
  `scores_send_time`-derived pins; asserts `bq ls` = exactly `raw`, `ontime`.

## Done-when

1. **Two datasets, still.** On the `bigquery` target every model resolves to
   the `ontime` dataset (`generate_schema_name`); on DuckDB every relation keeps
   its `main_<folder>` name, so no DuckDB reader or golden changes. *Evidence:
   row 1.*
2. **Five bodies, no default, no sixth.** Each dispatch macro has a DuckDB body
   and a BigQuery body; no `default__`; `adapter.dispatch` appears in exactly
   five macros; `make dbt-build TARGET=bigquery PROFILE=tiny` is green. *Evidence:
   row 2.*
3. **Pin parity.** The three goldens rendered from the BigQuery tables through
   the same `Golden` specs and `render` are byte-identical to
   `fixtures/tiny/expected/{attribution,ontime_rate_daily,scores_send_time}.csv`;
   `ONTIME_RATE`, `LABEL_ACCURACY`, `MAE_TINY`/`COVERAGE_TINY`,
   `COHORT_HOUR_TINY`, `COMPUTED_AS_OF_TINY` hold off the BigQuery rows.
   *Evidence: row 3.*
4. **The landing is generated-schema, idempotent, THROUGH-aware, dup-guarded.**
   `make bq-load` uploads the selected fixture files to the staging bucket and
   loads `raw.events` / `raw.dim_user` with the schema generated from
   `generator/models.py`, recreating both tables (`WRITE_TRUNCATE`); `THROUGH`
   selects files by the loader's own name predicate; a conflicting duplicate
   fails a dbt singular test on both dialects. *Evidence: row 4.*
5. **The build owns the right landing.** `dbt_build(TARGET=bigquery)` runs the
   BigQuery landing and never the DuckDB `load()`; `OTR_GCP_PROJECT` is set
   from the validated `PROJECT` inside the process; the `bigquery` output
   carries `location: us-central1`; `orchestration/tasks.py` carries `TARGET`
   as a literal; the incremental models name their overwrite column under a key
   no adapter interprets and are date-partitioned natively on BigQuery only.
   *Evidence: row 5.*
6. **Cloud targets gated; auth is impersonated ADC.** `bq-load`, `dbt-build
   TARGET≠duckdb` and `test-int-bigquery` need `CONFIRM=yes` from the command
   line and a `PROJECT` validated before any client is built; empty / `../x` /
   `"; ` values are refused as one literal; no keyfile path anywhere; no
   offline test builds a real client. *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_dbt_conventions.py::test_generate_schema_name_collapses_only_on_bigquery` (the macro text: `target.type == 'bigquery'` → `target.schema`, else dbt's default; no `adapter.dispatch`), `::test_exactly_five_dispatch_macros` (unchanged: dispatch in five files only); the in-process DuckDB build's existing relation names (`tests/test_incremental.py` `main_staging.…`, `tests/test_scores.py` `main_scores.…` — green means no collapse); live: after the build `bq ls --project_id=<id>` = `ontime`, `raw` and `bq ls ontime` lists the twelve models |
| 2 | `tests/test_dbt_conventions.py::test_each_macro_has_duckdb_and_bigquery_bodies` (the 9b form of `…_and_bigquery_stub_that_raises`: both bodies non-empty, neither raises), `::test_no_default_dispatch_body`, `::test_bigquery_bodies_are_the_named_forms` (`json_value(col, '$.key')`; `timestamp_diff(cast(end as timestamp), cast(start as timestamp), UNIT)` — end first; `safe_divide(cast(num as float64), den)`; `datetime(ts, tz)`; the overwrite is delete-in-set + insert like DuckDB); `::test_partition_overwrite_renders_delete_and_insert_on_duckdb` (unchanged); live: `make dbt-build TARGET=bigquery PROFILE=tiny PROJECT=<id> CONFIRM=yes` → `dbt-build OK: tiny/bigquery` with the test counts the DuckDB build reports |
| 3 | `tests/integration/test_int_bigquery.py::test_goldens_match_frozen` (three `Golden`s, 0 differ, via `eval.golden.diff_rows`), `::test_pins_hold_on_bigquery` (`ONTIME_RATE`, `LABEL_ACCURACY` via `eval.score.label_accuracy` over the BigQuery labels + `truth/prompts.jsonl`, `MAE_TINY`/`COVERAGE_TINY` via `eval.score`'s reachable-centre functions over the BigQuery rows, `COHORT_HOUR_TINY`, `COMPUTED_AS_OF_TINY`); offline `tests/test_eval.py::test_bigquery_rows_render_like_duckdb_rows` (a tz-aware `datetime`, a `date`, a `Decimal`/float and a `None` from a fake BigQuery row render to the same cells DuckDB's do); the pins are `tests/pins.py` unchanged (a diff in the branch is a FAIL of the central constraint) |
| 4 | `tests/test_bq_landing.py::test_selects_the_same_files_as_the_duckdb_loader` (`THROUGH` → `loader.load.event_files`), `::test_uploads_then_loads_with_the_generated_schema` (fake clients: object names under `landing/<profile>/`, `load_table_from_uri` per table with `schema` = `bq_schema.json`'s fields, `WRITE_TRUNCATE`, `source_format` NEWLINE_DELIMITED_JSON / CSV with `skip_leading_rows=1`, `null_marker` for `valid_to`), `::test_no_client_is_built_before_validation`; `tests/test_dbt_sources.py::test_bq_schema_is_generated_from_the_contract` (`make gen-sources --check` covers `loader/bq_schema.json`; a hand edit → red); `dbt/tests/assert_no_conflicting_duplicates.sql` (a unit-style pin: `tests/test_staging.py::test_conflicting_duplicate_fails_the_dbt_test` plants one in a scratch landing → the singular test fails on DuckDB); live: `bq-load OK: tiny — 10 files, 970 event rows, 22 dim rows` |
| 5 | `tests/test_loader.py::test_bigquery_build_lands_through_bq_not_duckdb` (injected landings: the DuckDB `load` fake raises if called, the bq fake records `(profile, project, through)`, `OTR_GCP_PROJECT` equals the validated id inside the process and is absent before), `::test_bigquery_target_needs_a_validated_project` (the 9b form of `test_bigquery_target_is_refused_before_9b`: empty / `../x` / `Bad Id` → exit 2, no landing, no dbt), `tests/test_infra.py::test_bigquery_profile_location_is_the_datasets` (`location` == `variables.tf` `region` default), `::test_bigquery_profile_project_has_no_default` (kept), `tests/test_dag_structure.py::test_manifest_is_make_pipelines_writing_steps` (the `TARGET=duckdb` literal), `tests/test_dbt_conventions.py::test_incremental_models_partition_config_is_dialect_safe` (`overwrite_partition_col` on the three models; the native `partition_by` guarded by `target.type == 'bigquery'`; `config.require('overwrite_partition_col')` in the strategy) |
| 6 | `tests/test_makefile.py::test_bq_targets_confirm_from_command_line_only` (`bq-load`, `test-int-bigquery`; `dbt-build` keeps `test_cloud_target_requires_confirm_from_the_command_line`), `::test_bq_targets_pass_project_as_one_literal` (`PROJECT` and `PROFILE` `_Q`-quoted, both origins, `"; echo pwned; "` / `$(shell …)` / `../x` / `''`), `tests/test_bq_landing.py::test_no_client_is_built_before_validation` (the client factory is injectable; the default factory is never called in the suite — a sentinel factory raises), `tests/test_infra.py::test_auth_is_adc_or_wif_never_keyfile` (kept; `profiles.yml` still `method: oauth`, no `keyfile`), `tests/test_truth_isolation.py` (`loader/bq.py` is pipeline code — guarded); mutations below all KILLED |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. **Every relation lands in a Terraform dataset.** For all models on the `bigquery` target, the resolved schema is `target.schema` (`ontime`); for all sources it is `raw`; for all models on any other target the resolved schema is dbt's default (`main_<folder>` on DuckDB) — the layout the DuckDB readers hard-code. | `tests/test_dbt_conventions.py::test_generate_schema_name_collapses_only_on_bigquery` (the branch dropped or keyed on `target.name` → red); the DuckDB in-process build (`tests/test_incremental.py`, `tests/test_scores.py` relation names); live `bq ls` = two datasets |
| 2. **Same rows, both dialects.** For all three frozen goldens, the rows read from BigQuery, rendered by the same `Golden` spec (columns, sort key, `render`), equal the frozen file byte-for-byte; every `tests/pins.py` number holds off the BigQuery rows. | `tests/integration/test_int_bigquery.py::test_goldens_match_frozen`, `::test_pins_hold_on_bigquery` (a `to_local_time` body off by a zone rule, a `timestamp_diff` argument order swapped, a `safe_divide` integer-dividing → a differing row); offline `tests/test_eval.py::test_bigquery_rows_render_like_duckdb_rows` (the tz-aware/`Decimal` normalisation) |
| 3. **The landing is a function of the fixture and `THROUGH`.** For all `(profile, through)`, the BigQuery landing loads exactly the files `loader.load.event_files` selects, into tables whose schema is the generated contract, recreating both (a second landing is byte-identical, never appended). | `tests/test_bq_landing.py::test_selects_the_same_files_as_the_duckdb_loader`, `::test_uploads_then_loads_with_the_generated_schema` (`WRITE_APPEND` → red; a hand-typed schema → red via `tests/test_dbt_sources.py::test_bq_schema_is_generated_from_the_contract`) |
| 4. **The build's landing matches its target.** For all `dbt_build(profile, target)`, the landing that runs is the target's — the DuckDB `load()` on `duckdb`, `bq_load()` on `bigquery`, never both, never the other — and `OTR_GCP_PROJECT` reaches dbt only as the validated `PROJECT`. | `tests/test_loader.py::test_bigquery_build_lands_through_bq_not_duckdb`, `::test_bigquery_target_needs_a_validated_project`, `::test_cloud_target_requires_confirm_from_the_command_line` (kept) |
| 5. **Five dispatch macros, two bodies each, no default, no sixth.** For all macro files, `adapter.dispatch` appears in exactly the five; each has non-empty `duckdb__` and `bigquery__` bodies and no `default__`; `generate_schema_name` and the strategy macro dispatch nothing. | `tests/test_dbt_conventions.py::test_exactly_five_dispatch_macros`, `::test_each_macro_has_duckdb_and_bigquery_bodies`, `::test_no_default_dispatch_body`, `::test_bigquery_bodies_are_the_named_forms` |
| 6. **A conflicting duplicate never lands silently on either dialect.** For all landings where one `insert_id` + clock triple carries two payloads, `dbt build` fails (a singular test over the source) — the loader's Python predicate, restated in SQL both dialects run. | `tests/test_staging.py::test_conflicting_duplicate_fails_the_dbt_test` (a planted conflict in a scratch DuckDB landing → the singular test red; the DuckDB loader's own refusal is bypassed by inserting after `load()`) |
| 7. **Cloud targets are gated and validated before any client exists; nothing offline reaches the network.** For all of `bq-load`, `dbt-build TARGET≠duckdb`, `test-int-bigquery`: `CONFIRM=yes` must have command-line origin and `PROJECT` must match `PROJECT_RE` before a storage/BigQuery client is constructed; the client factory is injectable and the offline suite injects fakes only. | `tests/test_makefile.py::test_bq_targets_confirm_from_command_line_only`, `::test_bq_targets_pass_project_as_one_literal`, `tests/test_bq_landing.py::test_no_client_is_built_before_validation` |
| 8. **The incremental partition config is dialect-safe.** For all three incremental models, the overwrite column is named under `overwrite_partition_col` (a key no adapter reads), the native `partition_by` dict exists only under `target.type == 'bigquery'`, and the strategy reads the neutral key — so DuckDB's build and goldens are unchanged and BigQuery partitions on the overwrite column. | `tests/test_dbt_conventions.py::test_incremental_models_partition_config_is_dialect_safe` (a bare `partition_by='…'` string → red; an unguarded dict → red); the DuckDB in-process build (unchanged goldens) |

Rules — the SQL bodies have no mutation operator (the two SQL operators act on
`case` arms in models); they are pinned by the static form tests (invariant 5),
the DuckDB in-process build, and the live parity run (invariant 2) — the same
treatment Phase 7 gave the DuckDB `partition_overwrite` body. Every Python
guard gets a mutation line; the landing and the build dispatch take injectable
clients/landings so no line can spawn a cloud call.

```mutations
loader/cli.py::validate_project      delete-call
loader/cli.py::require_confirm       invert-guard
loader/cli.py::land                  constant-return:0
loader/cli.py::land                  invert-guard
loader/bq.py::selected_files         constant-return:{'events': [], 'dim_user': []}
loader/bq.py::load_job_config        constant-return:{}
eval/golden.py::normalize_cell       constant-return:'x'
```

Equivalent-mutant / refused exclusions, verified once at implementation on a
scratch copy:

- `loader/cli.py::validate_project` is imported from `infra.cli` (one
  `PROJECT_RE`); its `invert-guard` line already lives in the 9a spec and is
  killed there — 9b's `delete-call` pins that `dbt_build` CALLS it (a build
  with an unvalidated project reaching the env is the failure).
- `loader/cli.py::land delete-call` — REFUSED: `land` is called inside an
  `if` (`if land(...): return 1`), not as a statement, so the operator finds
  no call; `constant-return:0` (no landing before the build) is the killing
  line, alongside `invert-guard` (the other target's landing).
- `loader/bq.py::bq_load constant-return:(0, 0, 0)` — REFUSED: with the fake
  clients the return value is not what the test asserts (the recorded calls
  are); `selected_files constant-return:{'events': [], 'dim_user': []}` and
  `load_job_config constant-return:{}` are the killing lines (nothing
  uploaded / no schema or disposition).

## Pinned decisions (do not re-litigate)

- **`generate_schema_name` collapses on `target.type == 'bigquery'` only
  (reconciliation item 1)** — satisfies invariant 1. A dbt hook override, not
  a dispatch macro (invariant 5 keeps five). DuckDB keeps `main_<folder>`;
  every reader stays as it is. Rejected: collapsing both (touches every DuckDB
  gate for no invariant); keying on `target.name`.
- **The five `bigquery__` bodies are the named forms, type-explicit, no
  `default__` (item 7 of the plan; the bodies)** — satisfies invariants 2, 5.
  `json_value(col, '$.key')` (SQL NULL for a JSON null or a missing key —
  the DuckDB `->>` contract); `timestamp_diff(cast(end as timestamp),
  cast(start as timestamp), UNIT)` — end first, both cast so DATE/DATETIME
  callers (`prompt_date`, the retention midnights) type the same way on both
  engines; `safe_divide(cast(num as float64), den)` (BigQuery's native
  `SAFE_DIVIDE` is NULL on zero — the cast keeps integer/integer from
  truncating, matching DuckDB's `/`); `datetime(ts_utc, tz)` (a naive local
  wall time — the `DATETIME` type is BigQuery's naive timestamp, so
  `client_event_time_local` casts to `date`/`extract(hour …)` as on DuckDB);
  `partition_overwrite` = the same delete-in-set + insert as DuckDB, in one
  BigQuery script (two DML statements), so the strategy macro is untouched.
  Rejected: BigQuery's `insert_overwrite` strategy (a second mechanism for the
  same seam; the DuckDB form already exists and the goldens pin its
  semantics); a `default__` fallback (the rule).
- **`overwrite_partition_col` names the overwrite column; the native
  `partition_by` dict is dialect-guarded (item 7)** — satisfies invariant 8.
  Rejected: either single value (one adapter raises — the finding).
- **The landing is `loader/bq.py` on the transitive google clients, generated
  schema, `WRITE_TRUNCATE`, GCS staging under `landing/<profile>/` (item 8)**
  — satisfies invariants 3, 6, 7. `make bq-load PROFILE=<p> PROJECT=<id>
  CONFIRM=yes [THROUGH=…]`; `dbt_build` dispatches to it on `bigquery` (item
  3). The conflicting-duplicate guard is a singular dbt test on the source
  (both dialects). Rejected: `bq`/`gsutil` subprocesses (two auth paths);
  an append-only landing (a re-run would double rows — the `make load`
  contract is recreate).
- **Pin parity is the three goldens through the same `Golden` specs, off
  BigQuery rows, behind `OTR_INT` (`make test-int-bigquery`)** — satisfies
  invariant 2. `eval/golden.py` gains a row-source seam (`rows_from(iterable)`
  + `normalize_cell` for tz-aware timestamps, dates, `Decimal`) so the
  renderer is one function for both engines; `tests/pins.py` is untouched.
  Rejected: a BigQuery-specific CSV writer (two renderers can agree by
  accident); re-freezing (forbidden — the central constraint).
- **Auth is the SA via impersonated ADC; `PROJECT` validated by `PROJECT_RE`
  before any client; every cloud target `CONFIRM`-gated (items 2, 5, 8)** —
  satisfies invariants 4, 7. Rejected: `impersonate_service_account:` in
  `profiles.yml` (a second env var, and it would not cover the landing's
  clients — the impersonated ADC covers all three).

## Scope (files)

- `dbt/macros/{json_extract,timestamp_diff,safe_divide,to_local_time,partition_overwrite}.sql`
  (the five `bigquery__` bodies replace the raises; the strategy macro reads
  `overwrite_partition_col`), `dbt/macros/generate_schema_name.sql` (new)
- `dbt/models/{staging/stg_events,staging/stg_prompts,attribution/attribution}.sql`
  (`config()`: `overwrite_partition_col` + the dialect-guarded native
  `partition_by`; SQL bodies untouched), `dbt/tests/assert_no_conflicting_duplicates.sql`
  (new), `dbt/profiles.yml` (`location`)
- `loader/bq.py` (new — the landing: `selected_files`, `upload`,
  `load_job_config`, `bq_load`, injectable client factory), `loader/bq_schema.json`
  (GENERATED), `loader/cli.py` (`land` dispatch, `PROJECT` → `OTR_GCP_PROJECT`,
  Amendment S's refusal deleted, `bq-load` subcommand), `scripts/gen_dbt_sources.py`
  (the BigQuery type map + the third output)
- `eval/golden.py` (`rows_from`, `normalize_cell`), `orchestration/tasks.py`
  (`TARGET` literal)
- `Makefile` (`bq-load`, `test-int-bigquery`; `dbt-build` gains `--project`;
  `unexport` unchanged — `PROJECT` is already listed)
- `tests/test_bq_landing.py` (new), `tests/integration/test_int_bigquery.py`
  (new), `tests/test_loader.py`, `tests/test_makefile.py`,
  `tests/test_dbt_conventions.py`, `tests/test_dbt_sources.py`,
  `tests/test_infra.py`, `tests/test_dag_structure.py`, `tests/test_eval.py`,
  `tests/test_staging.py`
- `pyproject.toml`, `uv.lock` (`dbt-bigquery`)
- Records: `DECISIONS.md`, `docs/PHASES.md`, `CLAUDE.md`,
  `docs/ARCHITECTURE.md` (§3.2, §3.3, §8), `docs/DEPLOYMENT.md`, `BACKLOG.md`,
  `.claude/agents/*.md` if they carry "raises until Phase 9"
- Under item 4 (a) only: `.github/workflows/ci.yml` (a `workflow_dispatch` job)
- Untouched by contract: `fixtures/`, `tests/pins.py`, `infra/**/*.tf`,
  `infra/MANIFEST.sha256` (no `.tf` change is planned; if one lands, `make
  tf-freeze CONFIRM=yes` is in the same commit), `serving/`, `generator/`
  (the schema render reads `models.py`, it does not change it)

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 9b entries: `generate_schema_name` on
      `target.type`; the five bodies (forms and casts); `overwrite_partition_col`
      + dialect-guarded native partitioning; the landing on the transitive
      clients (GCS staging, `WRITE_TRUNCATE`, generated schema); parity through
      one renderer; impersonated ADC; item 4's choice; the Infra in-force line
      ("BigQuery by profile switch" is realised)
- [ ] `docs/PHASES.md` — Phase 9 "Delivered (9b)" paragraph; the two warehouse
      Done-when clauses as landed
- [ ] `CLAUDE.md` — Current status; Commands (`dbt-build` `PROJECT`, `bq-load`,
      `test-int-bigquery`; the "REFUSED before 9b" sentence deleted); Repo map
      (`loader/bq.py`, `bq_schema.json`, `generate_schema_name.sql`); allowlist
      (dbt-bigquery landed); Event model facts (`overwrite_partition_col`);
      Determinism (BigQuery job ids already carved out); BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §3.2 (bodies landed; "raises until Phase 9"
      gone), §3.3 (the landing row), §8 Gotchas (item 7 + every live surprise)
- [ ] `BACKLOG.md` — per reconciliation item 6
- [ ] `docs/DEPLOYMENT.md` — the build-as-SA runbook (`operator_principal`,
      impersonated ADC), `bq-load`, the WIF opt-in apply step for CI, the
      undelete + import detour, the cost rows with tiny landed
- [ ] Spec amendments — none (no later spec exists; Phase 10's is finalized
      after 9b merges)
- [ ] docs/RESULTS.md, METRICS.md, AB_DESIGN.md — none (no generated block
      changes; the DuckDB blocks are untouched)
- [ ] README — none (no README in the repo)

## Threat model (REQUIRED)

Three targets take variables and touch the cloud, in the settled shape (one
Python process validates every value before any path, env var or client is
derived; `$(call _Q,$(value VAR))`; `unexport`ed — `PROJECT`, `PROFILE`,
`TARGET`, `THROUGH`, `CONFIRM` are already on the list). `PROJECT` never
becomes a path: it becomes `OTR_GCP_PROJECT` (dbt) and the client's `project=`
(a keyword argument, no shell) only after `PROJECT_RE`; `PROFILE` becomes the
GCS prefix `landing/<profile>/` only after `[a-z0-9_]+`; `THROUGH` stays a
string compare over fixture file names. **Cost if run twice:** `bq-load` is
`WRITE_TRUNCATE` on two tables (~1 MB storage, load jobs free — idempotent,
no double spend); the build re-runs queries on tiny (~10 MB scanned, inside
the 1 TB/month free tier); `test-int-bigquery` is both. **What is destroyed:**
`bq-load` replaces `raw.events` / `raw.dim_user` (re-creatable from the
fixture — the `make load` contract); nothing deletes a dataset, bucket or
object (`tf-destroy` remains the only teardown). **Auth:** impersonated ADC
(`--impersonate-service-account`), so a build cannot exceed the SA's
dataset-scoped grants; a build on raw Owner ADC is the operator's choice,
outside the control (N, Q). **No offline network:** every client is built by
an injectable factory; the suite injects fakes, and the default factory is a
sentinel in the offline run.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make bq-load PROFILE=<p> PROJECT=<id> CONFIRM=yes [THROUGH=…]` | `PROFILE=`/`PROJECT=` refused; `CONFIRM=` refused; `THROUGH=` = all files | refused (`PROFILE` `[a-z0-9_]+`, `PROJECT` `PROJECT_RE`), never a path or prefix | one literal, refused | reaches Python, validated the same; env `CONFIRM=yes` ignored | command-line only | `tests/test_makefile.py::test_bq_targets_confirm_from_command_line_only`, `::test_bq_targets_pass_project_as_one_literal`; `tests/test_bq_landing.py::test_no_client_is_built_before_validation` |
| `make dbt-build PROFILE=<p> TARGET=bigquery PROJECT=<id> CONFIRM=yes` | `PROJECT=` refused (exit 2, before any landing); `TARGET=` = duckdb (no `PROJECT` needed) | refused | one literal, refused | validated the same; env `CONFIRM=yes` ignored | command-line only (kept from Phase 2) | `tests/test_loader.py::test_bigquery_target_needs_a_validated_project`, `::test_cloud_target_requires_confirm_from_the_command_line`; `tests/test_makefile.py::test_bq_targets_pass_project_as_one_literal` |
| `make test-int-bigquery PROJECT=<id> CONFIRM=yes` | refused (the target's Python entry validates before `pytest`) | refused | one literal, refused | validated the same; env `CONFIRM=yes` ignored; `OTR_INT=1` is set in-recipe, never by the user | command-line only | `tests/test_makefile.py::test_bq_targets_confirm_from_command_line_only`, `::test_bq_targets_pass_project_as_one_literal` |

## Review & stack risk

- **code-reviewer** (triggered — `dbt/**`, `loader/`, `eval/`, `orchestration/`,
  `Makefile`, `tests/`): five dispatch macros, no `default__`, no sixth;
  `generate_schema_name` on `target.type`; the landing recreates; no clock; no
  pandas; `tests/pins.py` and `fixtures/` untouched; the `overwrite_partition_col`
  key; every user variable `_Q`-quoted and validated in Python.
- **security-reviewer** (MANDATORY — a cloud-cost landing and build, `PROJECT`
  → env/client, `profiles.yml`, `pyproject`/`uv.lock`, `test-int-*`): no keyfile;
  impersonated ADC documented; `CONFIRM` `$(origin)`; no client before
  validation; no offline network; nothing written outside `raw`/`ontime` and
  `landing/<profile>/`; the WIF opt-in step never the default apply.
- **functionality-tester** (triggered): the DONE command's offline half; the
  fake-client landing tests; each mutation line KILLED; the `PROJECT`/`CONFIRM`
  negatives; the DuckDB in-process build still green with unchanged goldens.
  The live half (`dbt-build TARGET=bigquery`, `test-int-bigquery`) is the
  developer's ask-first run, reported in Evidence rows 2–4.
- **coherence-auditor** at exit (mandatory, whole repo, ONCE): "raises until
  Phase 9" gone from ARCHITECTURE §3.2, CLAUDE.md, the agent files; the
  `TARGET=bigquery is REFUSED` sentence gone; PHASES "Delivered (9b)"; the
  BACKLOG count; that 9b supports Phase 10 (the write-back reads
  `scores_send_time` from either warehouse by relation name).
- Stack risk (first hour, STOP on any surprise, §8): (1) **`to_local_time`
  across tiny's tz-change users** (`u-000008`, `u-000010`: Tokyo → London
  mid-window) and the Tokyo previous-UTC-day prompts — `datetime(ts, tz)`
  must match DuckDB's `timezone(tz, timezone('UTC', ts))` for every event;
  the attribution golden is the proof, row by row; (2) `partition_by` key
  collision (item 7 — resolved by design, verified on the first compile);
  (3) `timestamp_diff` on DATE/DATETIME arguments (BigQuery `TIMESTAMP_DIFF`
  is TIMESTAMP-only — the explicit casts in the body; DATETIME → TIMESTAMP
  casts as UTC, which only ever differences against another such cast);
  (4) `round(x, 6)` and `sum()` ordering on the circular scores — a last-ulp
  difference before rounding would show as a differing `confidence`/
  `center_hour_local` row (a §8 gotcha and a dialect fix, never a re-freeze);
  (5) `qualify` + `row_number()` and `extract(hour from datetime)` on
  BigQuery; (6) `bq load` of a `JSON`-typed column from newline JSON and the
  empty-string `valid_to` → NULL (`null_marker`); (7) dbt-bigquery's
  transitive clients present in `uv.lock` (else `google-cloud-storage` is a
  STOP-and-ask); (8) the SA soft-delete detour (item 5).

## Out of scope (deferred, recorded)

- Composer running the DAG against BigQuery and a three-task (load / build /
  write-back) DAG — Phase 11 (a new BACKLOG row only if Phase 11 needs the
  split; item 3 closes the 8b row).
- The Spanner write-back target — Phase 10 (`send_schedule` stays the DuckDB
  stand-in; the write-back reads `scores_send_time` from DuckDB in 9b).
- A CI job for `test-int-bigquery` — item 4's choice ((b) recommended: the
  BACKLOG row re-deferred with the trigger "the first `enable_ci_wif = true`
  apply").
- `medium` on BigQuery — tiny first (CLAUDE.md); medium's pins are unfrozen
  and its 109 MB landing is a deliberate later run, not a 9b clause.
- BigQuery clustering — none (tiny; a DECISIONS note names `user_id` as the
  candidate when a profile is large enough to measure).
