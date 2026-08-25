# On-Time Rate Recovery Pipeline — Phase 0: tooling only. Pipeline targets
# (seed, load, dbt-build, attribution-golden, report, simulate, pipeline,
# writeback, tf-*) land with their phases (docs/PHASES.md).

.PHONY: setup test lint check-docs review-gate mutate

# User variables reach recipes ONLY as make values via `$(call _Q,$(value VAR))`
# — unexpanded and single-quoted — and are unexported, so a value like
# `SPEC='$(shell …)'` from the environment runs no shell (predecessor
# fix/make-quote-profile; threat model in specs/TEMPLATE.md). PROFILE / TARGET /
# CONFIRM are reserved here so a later phase cannot add them un-guarded.
unexport SPEC BASE DELETED CONFIRM PROFILE TARGET
_Q = '$(subst ','\'',$(1))'

setup:
	uv sync
	uv run pre-commit install

# Offline unit suite: no services, no network. tests/integration is skipped
# unless OTR_INT=1 (conftest.py) — the plumbing for Phase 8/9 live suites.
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
