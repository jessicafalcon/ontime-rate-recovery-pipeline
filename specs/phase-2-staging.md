# Phase 2 — Staging on DuckDB (PROPOSED)

Contract for the `phase-2-staging` branch. Source: `docs/PHASES.md` Phase 2.
Depends on Phase 1 merged (PR #2, `e844cea`) and `fix/round-tag-phase-reset`
(PR #3, `661c402`).

**Status: PROPOSED — do not start until approved.** Three new runtime
dependencies, all on the Phase 2 allowlist: `duckdb`, `dbt-core`, `dbt-duckdb`.
No dbt package (`dbt-utils` included) — a need for one is a STOP-and-ask. dbt
unit tests (`unit_tests:` in schema yml) need dbt-core ≥ 1.8; if the resolved
version lacks them the spec is re-cut, not worked around.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 1 shipped, and the four carry-overs due
this phase. Items marked **design change** are the paragraphs that need
approval before any implementation; the rest are facts the spec pins.

1. **`dim_user` is loaded as a source, not a dbt seed** — *design change*.
   ARCHITECTURE §3 and §3.3 say "`dim_user` seed". A dbt seed is a CSV under
   `dbt/seeds/` with one fixed path; ours lives at
   `fixtures/<profile>/dims/dim_user.csv` and changes with `PROFILE`. Pinning
   it as a dbt seed means a per-profile `seed-paths` override, a second load
   path, and Phase 10's swap (§3.3: `EXTERNAL_QUERY` source) becomes a
   seed→source rewrite of every staging model. Now: `make load PROFILE=<p>`
   loads BOTH `fixtures/<p>/raw/events_*.jsonl` and
   `fixtures/<p>/dims/dim_user.csv` into a `raw` schema; both are declared in
   the generated `sources.yml`; Phase 10 swaps only the source config, which
   is what §3.3 already promises. Invariant restored: "output row content is a
   function of raw + dims + vars" (§4 item 2) with one landing path for both.
   Rejected: dbt seed with `seed-paths` set from an env var (two loaders, and
   `dbt seed` types columns by inference — `valid_to` empty would become a
   varchar column, item 7). §3/§3.3 wording corrected in Record updates: "dim
   seed file (loaded as a source)".
2. **Raw DDL and `sources.yml` are generated from `generator/models.py`**
   (BACKLOG row, due). Generator: `scripts/gen_dbt_sources.py` (tooling, not
   pipeline code — it imports `generator.models`, which names truth records,
   so it cannot live under `dbt/` or the loader; its OUTPUT names only
   `Event` and `DimUserRow`). It derives one column per field — pydantic type
   → DuckDB type (`str`→`varchar`, `datetime`→`timestamp`, `date`→`date`,
   `dict`→`json`, `EventType`→`varchar` + an `accepted_values` test over
   `EventType`), `not_null` for non-Optional fields, `unique` on nothing (see
   item 5) — and writes two files: `dbt/models/staging/sources.yml` (source
   `raw`, tables `events`, `dim_user`, column tests) and
   `loader/ddl.sql` (the `create table` statements the loader executes).
   `make gen-sources` regenerates both; `tests/test_dbt_sources.py::
   test_committed_sources_equal_regeneration` renders into `tmp_path` and
   asserts byte equality with the committed files; a hand edit is a red test.
   Rejected: reading the DDL from the live DuckDB catalog (proves the loader,
   not the contract); generating at dbt compile time via a macro (dbt cannot
   import pydantic).
3. **dbt SQL mutation operator — re-defer to Phase 3** (BACKLOG row, trigger
   arrived). Recommendation: do not build it here. Staging has one SQL-only
   invariant (dedupe) and it is pinned three ways without an operator: a dbt
   unit test (`unit_tests: stg_events_dedupes_insert_id` — given four raw rows
   with two `insert_id`s across two `server_upload_time` dates, expect two
   rows), a `unique` data test on `stg_events.insert_id`, and the Python pin
   (`tests/test_pins.py`: raw row count, staged count, their difference =
   `DEDUPE_COUNT`). An operator that deletes a `qualify`/`where` clause would
   be killed by exactly these three, so it adds nothing in Phase 2; Phase 3's
   precedence rules are the first place a SQL survivor could hide (five
   `case` arms, each with a unit test — a `swap-predicate` operator over the
   `case` is the honest first use). New trigger: "Phase 3 (attribution
   precedence is the first multi-branch SQL)". DECISIONS Process entry stays
   in force.
4. **`error_code` is JSON `null` on `upload_started` / `upload_completed`**
   (exact-keys rule; 190 null vs 8 `E_NET` in tiny). Not a bug. The JSON
   extract macro returns SQL NULL for JSON null (DuckDB `->>` does); the
   staged column `error_code` is nullable `varchar`; a dbt data test pins
   that every `upload_failed` row has a non-null `error_code` and no other
   upload row does, and `tests/pins.py` pins the null count. Loader: the raw
   `event_properties` column is typed `json`, never a struct — DuckDB's
   `read_json` struct inference would drop the `null` key.
5. **`raw/events_2026-01-04.jsonl` (7 rows) and `_2026-01-13.jsonl` (9 rows)
   exist outside the 7 simulated days.** Tokyo 08:00 local on day 1 is
   2026-01-04 23:00 UTC; late arrivals land on day 8. Not a bug. The loader
   globs `raw/events_*.jsonl` (never iterates `days`), and the pin is the
   count over ALL files (970 raw rows, 10 files). A duplicated `insert_id`
   is not unique within raw (44 duplicate ids in tiny) — so the generated
   `sources.yml` carries NO `unique` test on `raw.events.insert_id`; `unique`
   moves to `stg_events`. PHASES' "source tests (uniqueness …)" is corrected
   in Record updates to "uniqueness on `dim_user (user_id, valid_from)`,
   not-null and accepted values on both; `insert_id` uniqueness is a staging
   test by definition".
6. **`stg_prompts` has no defined grain in any plan.** Pinned here: one row
   per `prompt_id` — from `prompt_sent` (`user_id`, `cohort_id`,
   `window_minutes`, `sent_at`, `sent_at_local`) left-joined to the FIRST
   `prompt_delivered` by `client_event_time` then `insert_id`
   (`delivered_at`, nullable). Phase 3 attributes from this table plus
   `stg_events`; the join key is `prompt_id`. `unique` + `not_null` on
   `prompt_id`; pinned count 140.
7. **`dim_user.valid_to` is empty on the open row** (carry-over 4). The loader
   types `valid_to` as `timestamp` with empty → NULL (explicit `nullstr`,
   never inference); the SCD2 join is `valid_from <= client_event_time and
   (valid_to is null or client_event_time < valid_to)`. Pinned by a dbt unit
   test with a two-row user whose events straddle the change (tiny has two:
   `u-000008`, `u-000010`) and a singular data test asserting every
   `stg_events` row matched exactly one dim row (count of matches = count of
   events; zero and two both fail). Not a comment.
8. **Variables: `PROFILE` is the data profile, `TARGET` is the dbt target.**
   ARCHITECTURE §3.2 (`make dbt-build TARGET=bigquery`) and PHASES Phase 2
   (`make dbt-build PROFILE=tiny`) are both right; the recipe takes both,
   `TARGET` defaulting to `duckdb`. Both reach Python unexpanded via
   `$(call _Q,$(value VAR))` and are validated `[a-z0-9_]+` before anything
   derives a path; the DuckDB file is `data/<profile>.duckdb`, reached by
   dbt through `profiles.yml` reading one env var the Python entry point sets
   after validation. Threat-model rows for `load`, `dbt-build`, `drop-db`
   (the only deleter; `CONFIRM=yes` with command-line origin).
9. **Where the loader lives** — *design change*. `loader/` is a new top-level
   package (`loader/{load,cli}.py`, `loader/ddl.sql`), pipeline code, so
   `tests/test_truth_isolation.py` guards it automatically (it is not in
   `EXEMPT`); `dbt/` is likewise guarded the moment it exists. Rejected:
   Python under `dbt/` (the dbt project dir is dbt's, and `dbt/target/`
   would be grepped too — harmless but noisy) and a generic `pipeline/`
   package (Phase 8's `serving/` and `orchestration/` are already named
   per role in the Repo map). Repo map gains `loader/`.
10. **CI** gains `make dbt-build PROFILE=tiny` after `make test` (the
    comment placeholder in `.github/workflows/ci.yml`); it needs no
    services (DuckDB in-process). security-reviewer is triggered (CI +
    `dbt/profiles.yml` + a `CONFIRM` target).
11. **Round tags are `review-round-N`, phase-agnostic, reset at phase start**
    (PR #3) — not branch-scoped. `make round-reset` ran on this branch: no
    tags to delete.

## Why

Every phase after this one is SQL over staged rows. If staging can double a
row, pick the wrong time zone, or answer differently on a second run, Phase 3's
labels and Phase 5's histograms are wrong in ways no later test can see. This
phase makes "the staged rows are exactly these" a pinned number on the frozen
fixture, and puts the dialect seam in place before any model depends on it.

## The central constraint

**`fixtures/tiny/` does not change, and every staged number is a pin.** No
`Freeze:` line in this spec; the gate FAILs any fixture byte. `make dbt-build
PROFILE=tiny` reproduces `tests/pins.py` from the committed fixture on every
machine, with no clock, no wall-time freshness test, and no dependency on file
order.

## DONE command

```
make review-gate SPEC=specs/phase-2-staging.md && make dbt-build PROFILE=tiny
```

- `make review-gate SPEC=…` — offline suite (loader, pins via an in-process
  dbt build into `tmp_path`, sources regeneration equality, the no-clock and
  four-macro greps, Makefile origin tests, truth isolation over `dbt/` and
  `loader/`) + lint + check-docs + Evidence / Record / fixture checks.
- `make dbt-build PROFILE=tiny` — validates `tiny`, loads
  `fixtures/tiny/{raw,dims}` into `data/tiny.duckdb` schema `raw`, runs
  `dbt build --target duckdb` (sources tests → `stg_events`, `stg_prompts` →
  their tests and unit tests), prints `dbt-build OK: tiny/duckdb`; exit 1 on
  any failing test.

## Done-when

1. **The build is green on tiny.** `make dbt-build PROFILE=tiny` loads, builds
   and passes every source, model, singular and unit test. *Evidence: row 1.*
2. **Counts are pinned and reproduced.** `tests/pins.py` holds `RAW_EVENT_ROWS`
   (970), `RAW_FILES` (10), `STG_EVENT_ROWS`, `DEDUPE_COUNT`
   (`RAW_EVENT_ROWS − STG_EVENT_ROWS`), `STG_PROMPT_ROWS` (140),
   `DIM_USER_ROWS`, `UPLOAD_ERROR_CODE_NULLS` (190 raw / 180 staged, `RAW_`/`STG_` prefixed); a build from the fixture
   reproduces each. Exact values are read off the first green build and
   pinned in the same commit. *Evidence: row 2.*
3. **One staged row per `insert_id`.** For every duplicated `insert_id` in raw,
   whatever files the copies land in, exactly one `stg_events` row.
   *Evidence: row 3.*
4. **Local time uses the tz valid at `client_event_time`.** A user with two
   `dim_user` rows gets each event converted under the row whose
   `[valid_from, valid_to)` contains the event; the open row (`valid_to`
   NULL) is unbounded. *Evidence: row 4.*
5. **The dialect seam is exactly five macros, with no silent fallback.**
   `json_extract`, `timestamp_diff`, `safe_divide`, `to_local_time`,
   `partition_overwrite` each have a `duckdb__` body and a `bigquery__` body
   that raises a compiler error naming Phase 9 (`to_local_time` added by the
   approved amendment below). No model calls `current_timestamp`/`now()`.
   *Evidence: row 5.*
6. **`sources.yml` and the raw DDL are generated, never hand-edited.** The
   committed files equal a fresh render from `generator/models.py`.
   *Evidence: row 6.*

## Evidence (REQUIRED)

| Done-when | Proof |
|---|---|
| 1 | `make dbt-build PROFILE=tiny` output line `dbt-build OK: tiny/duckdb`; `tests/test_staging.py::test_tiny_build_is_green` (in-process build into `tmp_path`) |
| 2 | `tests/test_staging.py::test_pins_are_reproduced` (every constant in `tests/pins.py` vs the built tables), `tests/test_loader.py::test_loader_globs_every_raw_file` (10 files, 970 rows, incl. `events_2026-01-04.jsonl`) |
| 3 | dbt unit test `stg_events_dedupes_insert_id` (`dbt/models/staging/schema.yml`), dbt data test `unique` on `stg_events.insert_id`, `tests/test_staging.py::test_dedupe_count_matches_pin`, `tests/test_loader.py::test_duplicate_insert_id_across_files_is_loaded_twice_and_staged_once` |
| 4 | dbt unit test `stg_events_uses_tz_valid_at_client_event_time` (a two-row user, events on both sides of the change), singular test `dbt/tests/assert_every_event_matches_one_dim_row.sql`, `tests/test_loader.py::test_empty_valid_to_loads_as_null`, `tests/test_staging.py::test_tz_change_users_are_converted_under_each_row` |
| 5 | `tests/test_dbt_conventions.py::test_exactly_five_dispatch_macros`, `::test_each_macro_has_duckdb_body_and_bigquery_stub_that_raises`, `::test_no_default_dispatch_body`, `::test_no_clock_call_in_any_model_or_macro`, `::test_no_dbt_packages`, `::test_every_model_has_description_and_a_test` |
| 6 | `tests/test_dbt_sources.py::test_committed_sources_equal_regeneration`, `::test_hand_edit_is_detected` (tmp copy with one column removed → not equal), `::test_no_unique_test_on_raw_insert_id`; `make gen-sources` output line `gen-sources OK: 2 files unchanged` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. For all `insert_id`, exactly one `stg_events` row, regardless of how many raw rows carry it and which upload-date files they land in; the surviving row is the one with the earliest `server_upload_time`, then earliest `server_received_time` (content-derived tie-break, never file or load order). | dbt unit test `stg_events_dedupes_insert_id` (4 raw rows, 2 ids, copies split across two `server_upload_time` dates, one copy with a later `server_received_time` → 2 rows, the earlier ones); `unique` on `stg_events.insert_id`; `tests/test_loader.py::test_duplicate_insert_id_across_files_is_loaded_twice_and_staged_once`; `tests/test_staging.py::test_dedupe_count_matches_pin` |
| 2. For all events, `client_event_time_local` is `client_event_time` converted under the `dim_user` row with `valid_from <= client_event_time and (valid_to is null or client_event_time < valid_to)`; every event matches exactly one row. | dbt unit test `stg_events_uses_tz_valid_at_client_event_time`; singular `assert_every_event_matches_one_dim_row`; `tests/test_staging.py::test_tz_change_users_are_converted_under_each_row` (`u-000008`, `u-000010` against `generator/dims.py::tz_at`); `tests/test_loader.py::test_empty_valid_to_loads_as_null` |
| 3. For all models and macros, no call reads the clock (`current_timestamp`, `now()`, `current_date`, `get_current_timestamp`, `run_started_at`) and no source has a freshness block; two builds of the same fixture produce identical staged tables. | `tests/test_dbt_conventions.py::test_no_clock_call_in_any_model_or_macro` (grep over `dbt/models/**`, `dbt/macros/**`, `dbt/tests/**`; a planted `now()` in a tmp copy is found), `::test_no_freshness_block`, `tests/test_staging.py::test_two_builds_are_identical` (row hashes of both staging tables, build twice into two `tmp_path` dbs) |
| 4. For all dialect-divergent SQL, it goes through exactly five `adapter.dispatch` macros; every macro has a `duckdb__` implementation and a `bigquery__` implementation that raises `exceptions.raise_compiler_error` — never a `default__` body a new adapter could silently fall into. | `tests/test_dbt_conventions.py::test_exactly_five_dispatch_macros` (parses `dbt/macros/*.sql`; `adapter.dispatch` nowhere else), `::test_each_macro_has_duckdb_body_and_bigquery_stub_that_raises`, `::test_no_default_dispatch_body` |
| 5. For all constants in `tests/pins.py`, a build from `fixtures/tiny/` reproduces them; a pin that drifts is a red test, never a rewritten number. | `tests/test_staging.py::test_pins_are_reproduced`; `tests/test_loader.py::test_loader_globs_every_raw_file` |
| 6. For all raw tables, the source tests are `not_null` on every non-Optional field, `accepted_values` on `event_type` over `EventType`, `unique` on `dim_user (user_id, valid_from)`; NO `unique` on `raw.events.insert_id` and NO `freshness`. | `tests/test_dbt_sources.py::test_no_unique_test_on_raw_insert_id`, `::test_source_tests_cover_every_required_column`, `tests/test_dbt_conventions.py::test_no_freshness_block` |
| 7. For all committed generated files (`dbt/models/staging/sources.yml`, `loader/ddl.sql`), the bytes equal a fresh render from `generator/models.py`. | `tests/test_dbt_sources.py::test_committed_sources_equal_regeneration`, `::test_hand_edit_is_detected` |
| 8. For all `load` / `dbt-build` / `drop-db` runs, `PROFILE` and `TARGET` are validated `[a-z0-9_]+` before any path is derived; `drop-db` deletes only `data/<profile>.duckdb` and only with `CONFIRM=yes` from the command line; `load` is idempotent (raw tables recreated from the fixture, never appended). | `tests/test_makefile.py::test_load_and_dbt_build_pass_profile_and_target_as_one_literal`, `::test_drop_db_requires_confirm_from_the_command_line`, `tests/test_loader.py::test_profile_and_target_are_validated`, `::test_load_twice_gives_the_same_row_count`, `::test_drop_db_removes_only_the_named_file` |
| 9. For all files under `dbt/` and `loader/`, the word truth does not appear (invariant 2 of Phase 1, now live for two new pipeline dirs). | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth`, `::test_pipeline_dirs_are_derived_from_the_tree` |

```mutations
loader/load.py::event_files                     constant-return:[]
loader/load.py::load_dims                       constant-return:0
loader/load.py::create_raw_tables               delete-call
loader/cli.py::validate_name                    invert-guard
loader/cli.py::drop_db                          constant-return:0
loader/cli.py::dbt_build                        constant-return:0
scripts/gen_dbt_sources.py::duckdb_type         constant-return:"varchar"
scripts/gen_dbt_sources.py::column_tests        constant-return:[]
scripts/gen_dbt_sources.py::render_ddl          constant-return:""
```

SQL-only invariants (1, 2, 3, 4, 6) are pinned by the dbt unit / data tests
named in the table — the sweep cannot reach them (BACKLOG, re-deferred to
Phase 3 in reconciliation item 3).

**Amendment (approved 2026-08-25, before the models were written): a fifth
dispatch macro, `to_local_time(ts_utc, tz)`.** UTC→local conversion is
dialect-divergent (DuckDB `timezone(tz, timezone('UTC', ts))::timestamp`,
BigQuery `datetime(ts, tz)`) and no plan listed it among the four seams. It is
the load-bearing expression of invariant 2, so it sits behind the seam with the
same shape as the other four (DuckDB body, BigQuery stub that raises, no
`default__`); invariant 4, decision 4 and CLAUDE.md / ARCHITECTURE §3.2 now say
five. Rejected: the DuckDB form inline in `stg_events` (Phase 9 would edit a
staging model to port a dialect — the thing the seam exists to prevent);
folding it into `timestamp_diff` (unrelated semantics under one name).

## Pinned decisions (do not re-litigate)

- **Dedupe by content-derived tie-break.** `stg_events` keeps, per
  `insert_id`, the row with the earliest `(server_upload_time,
  server_received_time)` via `qualify row_number() over (partition by
  insert_id order by server_upload_time, server_received_time) = 1` —
  satisfies invariant 1. Rejected: `distinct` over all columns (two copies
  differing only in `server_upload_time` — the late-arrival injector's case
  — would survive as two rows).
- **Local time once, in staging, by SCD2 range join on the loaded `raw.dim_user`
  source.** `valid_to` NULL is open-ended; the join predicate is in one CTE
  that `stg_prompts` reuses through `stg_events` — satisfies invariant 2.
  Rejected: dbt seed (reconciliation item 1); a `coalesce(valid_to,
  '9999-12-31')` sentinel (a literal date on a data path that a later
  fixture could exceed).
- **`sources.yml` and `loader/ddl.sql` are rendered by
  `scripts/gen_dbt_sources.py` from `Event` and `DimUserRow`, committed, and
  equality-tested.** `make gen-sources` is the only writer; the test is the
  guard, not the target — satisfies invariants 6, 7. Rejected: rendering at
  load time (the committed file is what reviewers and dbt read).
- **Five dispatch macros, `bigquery__` stubs raise, no `default__`.**
  `dbt/macros/{json_extract,timestamp_diff,safe_divide,to_local_time,partition_overwrite}.sql`;
  `partition_overwrite` has a DuckDB body Phase 7 will call and no caller yet
  (the seam exists before the first incremental model needs it) — satisfies
  invariant 4. Rejected: a `default__` body (a fifth adapter would build and
  be wrong).
- **One Python entry point (`loader/cli.py`) behind `load`, `dbt-build`,
  `drop-db`.** It validates `PROFILE`/`TARGET`, derives `data/<profile>.duckdb`,
  sets one env var (`OTR_DUCKDB_PATH`) that `dbt/profiles.yml` reads, and runs
  dbt via `dbtRunner` (no shell). `dbt-build` loads first, then builds, so CI
  is one target — satisfies invariant 8. Rejected: `dbt` invoked directly from
  the Makefile with `--vars` (the profile name would reach a path unvalidated).
- **`stg_prompts` is one row per `prompt_id`** (reconciliation item 6): from
  `prompt_sent`, left-joined to the first `prompt_delivered` (by
  `client_event_time`, then `insert_id`) — the grain Phase 3 attributes on.
  Rejected: prompt×user (identical here, one user per prompt; the name would
  mislead).

## Scope (files)

- `dbt/{dbt_project.yml,profiles.yml}`, `dbt/models/staging/{sources.yml,
  stg_events.sql,stg_prompts.sql,schema.yml}`,
  `dbt/macros/{json_extract,timestamp_diff,safe_divide,partition_overwrite}.sql`,
  `dbt/tests/{assert_every_event_matches_one_dim_row,assert_dim_user_key_unique,assert_error_code_only_on_upload_failed}.sql`
- `loader/{__init__,load,cli}.py`, `loader/ddl.sql` (generated)
- `scripts/gen_dbt_sources.py`, `scripts/check_docs.py` (TRACES rows)
- `Makefile` (`load`, `dbt-build`, `drop-db`, `gen-sources`), `pyproject.toml`
  + `uv.lock` (duckdb, dbt-core, dbt-duckdb), `.gitignore` (`*.duckdb` under
  `data/` already; `dbt/target/`, `dbt/logs/` already), `.github/workflows/ci.yml`
- `tests/{pins,test_loader,test_staging,test_dbt_sources,test_dbt_conventions}.py`,
  `tests/test_makefile.py`
- `specs/phase-2-staging.md`, `DECISIONS.md`, `BACKLOG.md`, `CLAUDE.md`,
  `docs/ARCHITECTURE.md`, `docs/PHASES.md`

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 2 entry: dim_user as loaded source, generated
      sources, dedupe tie-break, stub-raises macros, single entry point,
      `stg_prompts` grain, SQL-mutation re-deferral
- [ ] `docs/PHASES.md` — Phase 2 Done-when as landed; "Delivered" paragraph;
      source-test wording (no `unique` on raw `insert_id`)
- [ ] `CLAUDE.md` — Current status; Commands (`load`, `dbt-build`, `drop-db`,
      `gen-sources`); Repo map (`dbt/`, `loader/` real); allowlist unchanged
      (three Phase 2 packages landed); BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §3 / §3.3 "dim seed file (loaded as a source)";
      §8 Gotchas for every dbt-duckdb / DuckDB JSON surprise found live
- [ ] `BACKLOG.md` — strike "Raw DDL and dbt sources.yml …" (DONE Phase 2);
      re-defer "Mutation sweep has no operator for dbt SQL" with trigger
      Phase 3
- [ ] Spec amendments — none (no later spec exists)
- [ ] RESULTS / METRICS / DEPLOYMENT — none
- [ ] README — none (Phase 13)

## Threat model (REQUIRED when the phase adds a Makefile target that takes a variable, deletes anything, touches cloud resources, or takes user input)

`load` and `dbt-build` take `PROFILE` (+ `TARGET`, default `duckdb`);
`drop-db` takes `PROFILE` and `CONFIRM` and deletes `data/<profile>.duckdb`.
Same shape as Phase 1: `$(call _Q,$(value VAR))`, `unexport`, Python validates
`[a-z0-9_]+`, every path derived from the validated name, one-line recipes,
`$(origin CONFIRM)` tested inside the recipe. `gen-sources` takes no variable
and writes two fixed paths inside the tree (the equality test is its guard).

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make load PROFILE=` | refused, exit 2 | refused by `[a-z0-9_]+`, exit 2 | one literal argv token; refused | reaches the recipe, validated identically | n/a | `tests/test_makefile.py::test_load_and_dbt_build_pass_profile_and_target_as_one_literal`, `tests/test_loader.py::test_profile_and_target_are_validated` |
| `make dbt-build PROFILE= TARGET=` | PROFILE empty refused; TARGET empty → `duckdb` | refused | refused | same; `TARGET=bigquery` from any origin is accepted but is Phase 9's manual path (no credentials exist here → dbt fails to connect, nothing billed) | n/a | same two tests |
| `make drop-db PROFILE= CONFIRM=` | refused; without CONFIRM prints `drop-db: refused — pass CONFIRM=yes on the command line`, exit 2, no delete | refused | refused | `CONFIRM=yes` from the environment is NOT accepted | recipe passes `$(origin CONFIRM)`; Python accepts only `command line` | `tests/test_makefile.py::test_drop_db_requires_confirm_from_the_command_line`, `tests/test_loader.py::test_drop_db_removes_only_the_named_file` |

Residual (stated): `MAKEFLAGS='CONFIRM=yes'` has command-line origin —
"mistakes, not a user who controls the environment". `drop-db` only ever
removes a gitignored file that `load` recreates from the fixture.

## Review & stack risk

- **code-reviewer** (triggered — code in Scope): no clock on a data path, the
  tie-break key named, one column per line / lowercase SQL, every model has a
  description + a test, the four-macro count, allowlist (three packages, no
  dbt package), truth isolation over `dbt/` and `loader/`, fixtures untouched.
- **security-reviewer** (mandatory — CI, `dbt/profiles.yml`, `drop-db` with
  `CONFIRM`): origin gating, path derivation, no credentials in
  `profiles.yml` (the bigquery target names only `method: oauth` + env-var
  project), `.duckdb` never committed.
- **functionality-tester** (same trigger): DONE command; `make dbt-build
  PROFILE=tiny` twice; `PROFILE=`, `PROFILE=../x`; `drop-db` without
  CONFIRM; the dbt unit tests actually fail when the `qualify` clause and
  the `valid_to is null` disjunct are removed in a worktree (the SQL-side
  check the sweep cannot do).
- **coherence-auditor** at exit (mandatory): §3/§3.3 no longer say "seed";
  Repo map marks `dbt/`, `loader/` real; BACKLOG rows struck / re-deferred;
  PHASES Phase 2 matches the spec as landed; CLAUDE.md Commands lists the
  four targets.
- Stack risk, verified in the first hour (all held; two surprises logged under §8: `timezone()` direction, `unit_tests:` top-level + dict `expect`): dbt-core
  version resolved by uv supports `unit_tests:` (≥ 1.8); dbt-duckdb reads
  `path` from `env_var`; DuckDB `json` column keeps a JSON `null` value and
  `->>` returns SQL NULL for it; `timezone(tz, ts)` on a UTC `timestamp`
  gives the local wall time for an IANA name (DuckDB's ICU extension must be
  present in the wheel); `qualify` is supported. Findings go under §8.

## Out of scope (deferred, recorded)

- dbt SQL mutation operator — BACKLOG, trigger Phase 3.
- Incremental materialization / `partition_overwrite` callers — Phase 7 (the
  macro body exists, unused).
- `fixtures/tiny/expected/` — Phase 3 (needs a declared re-freeze).
- BigQuery bodies for the four macros — Phase 9.
