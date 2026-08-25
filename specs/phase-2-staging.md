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
