# On-Time Rate Recovery Pipeline. Pipeline targets land with their phases
# (CLAUDE.md → Commands): seed/freeze (1); load, dbt-build (2); attribution-golden, eval (3); report (4); …

.PHONY: setup test lint check-docs review-gate mutate round-reset seed freeze load dbt-build drop-db gen-sources attribution-golden eval report scores-golden simulate holdout power readme writeback pipeline test-int-airflow bq-load test-int-bigquery spanner-load test-int-spanner composer-dbt-manifest build-serving-image tf-validate tf-plan tf-apply tf-destroy tf-migrate-state tf-freeze

# User variables reach recipes ONLY as make values via `$(call _Q,$(value VAR))`
# — UNEXPANDED and single-quoted — so a value like `SPEC='$(shell …)'` or
# `"; rm x; "` from EITHER origin reaches Python as one literal argument and
# no shell or make function runs on it; Python validates. `unexport` is hygiene
# only (keeps the value out of the child's environment) — an environment-set
# variable still reaches the recipe. The ONLY way to tell command line from
# environment is `$(origin VAR)`; every future CONFIRM knob tests
# `$(origin CONFIRM)` = `command line` inside its recipe (spec threat model,
# corrected in review round 1; pinned by tests/test_makefile.py).
unexport SPEC BASE DELETED CONFIRM PROFILE TARGET WRITE THROUGH FULL PROJECT VARS ALLOW_DESTROY
_Q = '$(subst ','\'',$(1))'

setup:
	uv sync
	uv run pre-commit install

# Offline unit suite: no services, no network. tests/integration is skipped
# unless OTR_INT=1 (conftest.py); only the Phase 8/9/10 test-int-* targets export it.
test:
	uv run pytest --ignore=tests/integration

# ruff via pre-commit. REWRITES files (ruff-format) — never run inside a gate.
lint:
	uv run pre-commit run --all-files

# The one docs guard (scripts/check_docs.py): links/anchors, named make targets,
# trace tokens, BACKLOG count, live identifiers (every value position in a tracked
# record holds a placeholder). Offline; not a pytest file.
check-docs:
	uv run python scripts/check_docs.py

# The offline review gate (scripts/review_gate.py): make test + ruff check/format
# --check (read-only) + check-docs; with SPEC, Evidence ids and Record-updates
# files; DELETED greps removed symbols. `/review-round N` runs it first.
review-gate:
	uv run python scripts/review_gate.py $(if $(value SPEC),--spec $(call _Q,$(value SPEC)),) --base $(call _Q,$(if $(value BASE),$(value BASE),main)) $(if $(value DELETED),--deleted $(call _Q,$(value DELETED)),)

# The mutation sweep (scripts/mutate.py): each ```mutations line applied to HEAD
# in a throwaway git worktree, offline suite run there, KILLED/SURVIVED/ERROR.
mutate:
	uv run python scripts/mutate.py --spec $(call _Q,$(value SPEC))

# Delete this checkout's local review-round-* tags (scripts/round_tag.py reset).
# Run at phase start: round tags are local, never pushed, and phase-agnostic, so
# a new phase's rounds would collide with the prior phase's leftovers. NEVER
# mid-phase — it deletes THIS phase's round boundary (round N+1 needs
# review-round-N..HEAD; a deleted annotated tag is unrecoverable).
round-reset:
	uv run python scripts/round_tag.py reset

# The seeded generator (generator/cli.py): validates PROFILE ([a-z0-9_]+),
# writes data/out/<PROFILE>/ only, compares its own hashes to
# fixtures/<PROFILE>/MANIFEST.sha256 when one exists (exit 1 on drift).
seed:
	uv run python -m generator.cli seed $(call _Q,$(value PROFILE))

# The ONLY writer of fixtures/: copies data/out/<PROFILE>/ over
# fixtures/<PROFILE>/ and writes the manifest. Overwrites a committed golden, so
# CONFIRM=yes must come from the COMMAND LINE ($(origin CONFIRM)); Python refuses
# any other origin or value. A re-freeze needs a DECISIONS entry + a `Freeze:`
# line in the phase spec (the review gate checks the diff). $(origin CONFIRM)
# needs no _Q: make's origin words are a closed, quote-free set (command line /
# environment / file / …), so user input can never reach that argument.
freeze:
	uv run python -m generator.cli freeze $(call _Q,$(value PROFILE)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# ------------------------------------------------------------------ Phase 2
# Raw landing (landing/cli.py): validates PROFILE, loads fixtures/<PROFILE>/{raw,dims}
# into data/<PROFILE>.duckdb schema `raw`. Append-only (fix/append-landing):
# raw.events persists and each load overwrites the selected upload-date partitions
# (re-land = 0 net rows), raw.dim_user is a full replace. THROUGH (an upload date
# YYYY-MM-DD) lands only the files uploaded on or before it — a landing is the
# raw-table state (Phase 7), accumulating forward; empty loads them all.
load:
	uv run python -m landing.cli load $(call _Q,$(value PROFILE)) --through $(call _Q,$(value THROUGH))

# The TARGET's landing, then `dbt build` (sources tests → staging → attribution
# → their tests). TARGET selects the dbt target (default duckdb: `load` into
# data/<PROFILE>.duckdb); bigquery (Phase 9b) lands through `bq-load` instead,
# needs PROJECT (validated as a GCP project-id, exported to dbt as
# OTR_GCP_PROJECT from inside Python) and is a cloud-cost command needing
# CONFIRM=yes from the COMMAND LINE ($(origin CONFIRM)). The event-level models
# are incremental with a LOOKBACK_DAYS reprocessing window (Phase 7). FULL=yes
# from the COMMAND LINE ($(origin FULL)) passes --full-refresh. THROUGH lands
# only files uploaded on or before it — a per-interval build (Phase 8b); unset
# loads all. Names validated in Python before any path.
dbt-build:
	uv run python -m pipeline.cli dbt-build $(call _Q,$(value PROFILE)) --target $(call _Q,$(value TARGET)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)' --full $(call _Q,$(value FULL)) --full-origin '$(origin FULL)' --through $(call _Q,$(value THROUGH)) --project $(call _Q,$(value PROJECT))

# The BigQuery landing alone (landing/cli.py bq-load, Phase 9b): the same files
# `load` selects → gs://<PROJECT>-ontime/landing/<PROFILE>/ → raw.events /
# raw.dim_user with the schema generated from generator/models.py; append-only
# (fix/append-landing) — a WRITE_TRUNCATE load per upload-date partition into the
# DAY-partitioned raw.events$YYYYMMDD, raw.dim_user a full replace; idempotent.
# Cloud-cost (cents): CONFIRM=yes must have
# COMMAND-LINE origin; PROJECT validated before any client; ADC, never a key.
bq-load:
	uv run python -m landing.cli bq-load $(call _Q,$(value PROFILE)) --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)' --through $(call _Q,$(value THROUGH))

# Deletes data/<PROFILE>.duckdb and its .wal (gitignored; `make load` recreates it). The only
# deleter this phase adds: CONFIRM=yes must have COMMAND-LINE origin.
drop-db:
	uv run python -m landing.cli drop-db $(call _Q,$(value PROFILE)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# Re-render landing/ddl.sql, landing/bq_schema.json (Phase 9b) and
# dbt/models/staging/sources.yml from generator/models.py
# (scripts/gen_dbt_sources.py). tests/test_dbt_sources.py fails on a hand edit.
gen-sources:
	uv run python scripts/gen_dbt_sources.py

# ------------------------------------------------------------------ Phase 3
# The golden (eval/cli.py golden): the built attribution table vs
# fixtures/<PROFILE>/expected/attribution.csv, sorted by (prompt_id, user_id); exit 1 on
# any differing row. WRITE=yes (the literal only) writes data/out/<PROFILE>/
# expected/attribution.csv instead — never fixtures/ (`make freeze` is the
# only writer there). Needs `make dbt-build PROFILE=<p>` first.
attribution-golden:
	uv run python -m eval.cli golden $(call _Q,$(value PROFILE)) --write $(call _Q,$(value WRITE))

# Label accuracy vs <PROFILE>/truth/prompts.jsonl plus reachable-centre MAE and
# coverage vs <PROFILE>/truth/users.jsonl (eval/cli.py score — the ONLY truth
# reader; fixtures/<PROFILE>/ when frozen, else data/out/<PROFILE>/, marked
# `(unfrozen)`); exit 1 below LABEL_ACCURACY or off the SEND_TIME_PINS.
eval:
	uv run python -m eval.cli score $(call _Q,$(value PROFILE))

# The scores golden (eval/cli.py scores-golden): the built scores_send_time
# table vs fixtures/<PROFILE>/expected/scores_send_time.csv, sorted by
# (user_id, cohort_id); exit 1 on any differing row. WRITE=yes (the literal
# only) writes data/out/<PROFILE>/expected/scores_send_time.csv instead — never
# fixtures/. Needs `make dbt-build` first.
scores-golden:
	uv run python -m eval.cli scores-golden $(call _Q,$(value PROFILE)) --write $(call _Q,$(value WRITE))

# The on-time report (eval/cli.py report): the built ontime_rate_daily mart vs
# fixtures/<PROFILE>/expected/ontime_rate_daily.csv, sorted by (cohort_id,
# prompt_date), plus the overall rate vs tests/pins.py::ONTIME_RATE; console
# only. WRITE=yes (the literal only) writes data/out/<PROFILE>/expected/
# ontime_rate_daily.csv instead — never fixtures/. Needs `make dbt-build` first.
report:
	uv run python -m eval.cli report $(call _Q,$(value PROFILE)) --write $(call _Q,$(value WRITE))

# The counterfactual simulation (eval/cli.py simulate): three arms under
# common random numbers, per cause, rendered as the <PROFILE> block of
# docs/RESULTS.md. Check mode diffs the committed block byte-for-byte (exit 1
# on drift); WRITE=yes (the literal only) replaces the bytes between the
# profile's markers and nothing else (a missing pair refuses). truth/ resolves
# as `eval` does (`(unfrozen)` for medium). Needs `make dbt-build` first.
simulate:
	uv run python -m eval.cli simulate $(call _Q,$(value PROFILE)) --write $(call _Q,$(value WRITE))

# The temporal holdout (eval/cli.py holdout): serve on data landed <= the
# profile's cut (tests/pins.py::HOLDOUT_CUTS), score the served schedule against
# the RAW organic opens uploaded after the cut, rendered as the <PROFILE> block
# of docs/RESULTS.md. Self-contained: it builds two throwaway DuckDB warehouses
# in a temp dir (served <= cut, full for the held-out opens) — no `dbt-build`
# first, no truth, no clock. Same check / WRITE=yes shape as simulate. medium is
# seeded and unfrozen, so `make seed PROFILE=medium` first for it.
holdout:
	uv run python -m eval.cli holdout $(call _Q,$(value PROFILE)) --write $(call _Q,$(value WRITE))

# The A/B power table (eval/cli.py power): users per arm and days to power
# from the pinned baseline rates, rendered as the block of docs/AB_DESIGN.md;
# same check / WRITE=yes shape as simulate. No PROFILE — both profiles' rows.
power:
	uv run python -m eval.cli power --write $(call _Q,$(value WRITE))

# The README first-screen block (README.md) + the findings chart
# (docs/img/lift.svg), rendered by eval/cli.py readme from tests/pins.py (which
# the committed docs/RESULTS.md blocks are pinned to) — no number typed by hand. Check mode diffs
# both byte-for-byte (exit 1 on drift); WRITE=yes (the literal only) rewrites
# both. No PROFILE; non-destructive (only the same generated bytes change).
readme:
	uv run python -m eval.cli readme --write $(call _Q,$(value WRITE))

# The write-back (serving/cli.py writeback): upsert scores_send_time + the open
# dim_user tz into send_schedule, replacing a user's row only on a strictly
# greater (model_version, computed_as_of); idempotent (a re-run writes 0).
# TARGET=duckdb (default): serving.send_schedule in data/<PROFILE>.duckdb (the
# stand-in, §2.9) — no CONFIRM (create-if-not-exists + upsert, never
# destructive). TARGET=spanner (Phase 10): read the same two relations off
# BigQuery `ontime`, write the Spanner table — cloud-cost, CONFIRM=yes from the
# COMMAND LINE ($(origin CONFIRM)) and PROJECT validated before any client;
# PROFILE is optional there (the read is the warehouse's, not a build's).
writeback:
	uv run python -m serving.cli writeback $(call _Q,$(value PROFILE)) --target $(call _Q,$(value TARGET)) --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# The local pipeline with no scheduler (serving/cli.py pipeline): dbt build →
# eval → write-back in one validated process, producing scores_send_time and
# send_schedule. eval here is the union-only validation gate (it asserts the
# full-data pins and reads truth). Phase 8b's Airflow DAG orders the WRITING
# steps (dbt build → write-back); eval is not a per-interval DAG task, so the DAG
# produces the same two tables byte-identically without gating on partial data.
pipeline:
	uv run python -m serving.cli pipeline $(call _Q,$(value PROFILE))

# Phase 8b integration: spin the Docker-local Airflow (SequentialExecutor +
# SQLite), run the DAG (a union run + a three-interval backfill) and assert both
# tables == make pipeline (the send_schedule hash), then tear down. Exports
# OTR_INT=1 in-recipe so tests/integration/ collects (conftest skips it
# otherwise); CI never runs this. Takes NO variable (tiny by definition — the
# DAG's PROFILE=tiny is a manifest literal); non-destructive to tracked files
# (writes only the container's data/ and `docker compose down -v`). Needs Docker.
test-int-airflow:
	OTR_INT=1 uv run pytest tests/integration/test_int_airflow.py

# Phase 9b integration: the DuckDB≡BigQuery pin-parity run. Validates PROFILE
# (default tiny) + PROJECT and gates CONFIRM in Python FIRST, then runs the
# pytest (tests/integration/test_int_bigquery.py) with OTR_INT=1 and the
# validated project in its env: lands tiny, builds on bigquery, diffs the three
# goldens against fixtures/tiny/expected/ and asserts the pins. Cloud-cost
# (ask first); CI never runs it (the CI leg needs the opt-in WIF apply —
# docs/DEPLOYMENT.md).
test-int-bigquery:
	uv run python -m pipeline.cli test-int-bigquery $(call _Q,$(if $(value PROFILE),$(value PROFILE),tiny)) --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# ------------------------------------------------------------------ Phase 10
# The Spanner dims landing (landing/cli.py spanner-load): the same dim seed the
# other landings select → the Spanner `dim_user` table (the production dims
# home BigQuery federates from, §2.3/§3.3), columns/types from the generated
# contract, one idempotent batch upsert. Cloud-cost (a spanner-enabled stack):
# CONFIRM=yes must have COMMAND-LINE origin; PROJECT validated before any
# client; ADC, never a key.
spanner-load:
	uv run python -m landing.cli spanner-load $(call _Q,$(value PROFILE)) --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# Phase 10 integration: the Spanner write-back + federation run. Validates
# PROFILE (default tiny) + PROJECT and gates CONFIRM in Python FIRST, then runs
# the pytest (tests/integration/test_int_spanner.py) with OTR_INT=1: lands dims
# in Spanner, builds on bigquery with the dim_user source swapped to the
# federated view (same three goldens), runs the write-back twice (second writes
# 0, row hash unchanged and equal to the DuckDB pin). Cloud-cost (ask first;
# needs an `enable_spanner=true` apply); CI never runs it.
test-int-spanner:
	uv run python -m pipeline.cli test-int-spanner $(call _Q,$(if $(value PROFILE),$(value PROFILE),tiny)) --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# ------------------------------------------ fix/composer-cosmos (ROADMAP item 7)
# The precompiled dbt manifest for the Cloud-Composer Cosmos DAG (offline `dbt
# parse` on the duckdb target — structure only, no cloud). Renders
# dbt/target/manifest.json (gitignored); the deploy uploads it into the DAG
# bucket so Cosmos loads from it (LoadMode.DBT_MANIFEST) and the scheduler runs
# no dbt at parse. Takes NO variable; non-destructive.
composer-dbt-manifest:
	uv run python -m pipeline.cli composer-manifest

# Build and push the serving+landing image the KubernetesPodOperator pods run
# (Artifact Registry). Cloud-cost (a registry push): CONFIRM=yes must have
# COMMAND-LINE origin ($(origin CONFIRM)) and PROJECT is validated before any
# docker/registry call; the push runs in 7b (ask first). No credential is baked
# (Workload Identity at run).
build-serving-image:
	uv run python -m pipeline.cli build-serving-image $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# ------------------------------------------------------------------ Phase 9a
# Terraform foundation (infra/cli.py): validates PROJECT (a GCP project-id shape)
# before deriving the -var, runs terraform -chdir=infra. Auth is ADC/WIF only —
# never a keyfile. tf-validate is offline (init -backend=false -input=false
# -lockfile=readonly + validate + fmt -check; downloads the provider once from the registry — outside `make test`).
# tf-plan reads GCP APIs. tf-apply/tf-destroy are cloud-cost/destructive:
# CONFIRM=yes must have COMMAND-LINE origin ($(origin CONFIRM)); ask first.
tf-validate:
	uv run python -m infra.cli validate

# Toggles reach Terraform ONLY as VARS='name=value,…' from the COMMAND LINE
# ($(origin VARS), like CONFIRM) → argv `-var` (fix/tf-vars-argv); an auto-loaded
# tfvars (Amendment T) or a TF_VAR_*/TF_CLI_ARGS* in the environment is refused
# before terraform runs, and the child gets an allowlisted environment — the
# argv is the whole input by construction.
tf-plan:
	uv run python -m infra.cli plan --project $(call _Q,$(value PROJECT)) --vars $(call _Q,$(value VARS)) --vars-origin '$(origin VARS)'

# tf-apply plans first and applies the SAVED plan only if every planned
# action is in infra.cli.SAFE_ACTIONS: a destroy needs ALLOW_DESTROY=yes from
# the COMMAND LINE ($(origin ALLOW_DESTROY)) — the toggle-flip teardown passes
# it; an apply that merely omitted a currently-applied toggle cannot destroy —
# and an unreadable plan or any other verb (forget, a future one) refuses
# ALWAYS (Amendments F, K, N1, O2).
tf-apply:
	uv run python -m infra.cli apply --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)' --vars $(call _Q,$(value VARS)) --vars-origin '$(origin VARS)' --allow-destroy $(call _Q,$(value ALLOW_DESTROY)) --allow-destroy-origin '$(origin ALLOW_DESTROY)'

tf-destroy:
	uv run python -m infra.cli destroy --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)' --vars $(call _Q,$(value VARS)) --vars-origin '$(origin VARS)'

# tf-migrate-state runs `terraform init -migrate-state` onto the GCS backend
# (fix/tf-remote-state, ROADMAP item 2): the versioned <project_id>-tfstate
# bucket is bootstrapped ONCE by hand (docs/DEPLOYMENT.md § state-backend
# bootstrap — a bucket cannot create its own backend), the backend block is
# uncommented in infra/main.tf, then this migrates the local state to it. The
# bucket is supplied as a partial backend config from the validated PROJECT (no
# id in the .tf); same gates as every tf-* — CONFIRM=yes command-line origin,
# cloud-env refusal, no TF_VAR_*/auto-tfvars, allowlisted child env,
# -lockfile=readonly. Cloud-touching (writes state to GCS): ask first.
tf-migrate-state:
	uv run python -m infra.cli migrate-state --project $(call _Q,$(value PROJECT)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'

# The ONLY writer of infra/MANIFEST.sha256 — the content pin over every file
# Terraform loads (*.tf, *.tf.json) and the provider lock (Amendments P/R): any
# edit to one is red in `make test` until the
# manifest is rewritten here, in the same commit. Overwrites a committed pin, so
# CONFIRM=yes must have COMMAND-LINE origin ($(origin CONFIRM)), like freeze.
tf-freeze:
	uv run python -m infra.cli freeze --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'
