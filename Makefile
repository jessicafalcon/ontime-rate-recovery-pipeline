# On-Time Rate Recovery Pipeline. Pipeline targets land with their phases
# (CLAUDE.md → Commands): seed/freeze (1); load, dbt-build (2); …

.PHONY: setup test lint check-docs review-gate mutate seed freeze

# User variables reach recipes ONLY as make values via `$(call _Q,$(value VAR))`
# — UNEXPANDED and single-quoted — so a value like `SPEC='$(shell …)'` or
# `"; rm x; "` from EITHER origin reaches Python as one literal argument and
# no shell or make function runs on it; Python validates. `unexport` is hygiene
# only (keeps the value out of the child's environment) — an environment-set
# variable still reaches the recipe. The ONLY way to tell command line from
# environment is `$(origin VAR)`; every future CONFIRM knob tests
# `$(origin CONFIRM)` = `command line` inside its recipe (spec threat model,
# corrected in review round 1; pinned by tests/test_makefile.py).
unexport SPEC BASE DELETED CONFIRM PROFILE TARGET
_Q = '$(subst ','\'',$(1))'

setup:
	uv sync
	uv run pre-commit install

# Offline unit suite: no services, no network. tests/integration is skipped
# unless OTR_INT=1 (conftest.py); only the Phase 8/9 test-int-* targets export it.
test:
	uv run pytest --ignore=tests/integration

# ruff via pre-commit. REWRITES files (ruff-format) — never run inside a gate.
lint:
	uv run pre-commit run --all-files

# The one docs guard (scripts/check_docs.py): links/anchors, named make targets,
# trace tokens, BACKLOG count. Offline; not a pytest file.
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

# The seeded generator (generator/cli.py): validates PROFILE ([a-z0-9_]+),
# writes data/out/<PROFILE>/ only, compares its own hashes to
# fixtures/<PROFILE>/MANIFEST.sha256 when one exists (exit 1 on drift).
seed:
	uv run python -m generator.cli seed $(call _Q,$(value PROFILE))

# The ONLY writer of fixtures/: copies data/out/<PROFILE>/ over
# fixtures/<PROFILE>/ and writes the manifest. Overwrites a committed golden, so
# CONFIRM=yes must come from the COMMAND LINE ($(origin CONFIRM)); Python refuses
# any other origin or value. A re-freeze needs a DECISIONS entry + a `Freeze:`
# line in the phase spec (the review gate checks the diff).
freeze:
	uv run python -m generator.cli freeze $(call _Q,$(value PROFILE)) --confirm $(call _Q,$(value CONFIRM)) --confirm-origin '$(origin CONFIRM)'
