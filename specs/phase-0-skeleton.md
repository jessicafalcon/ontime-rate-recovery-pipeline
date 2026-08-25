# Phase 0 — Skeleton and workflow machinery (PROPOSED)

Contract for the `phase-0-skeleton` branch. Source: `docs/PHASES.md` Phase 0.
Depends on nothing (first branch after the brief).

**Status: PROPOSED — awaiting the developer's approval of the three docs.** No
new runtime dependencies; dev group only (pytest, ruff, pre-commit). Python
3.12 pinned (DECISIONS Phase 0).

## Why

Every later phase is a diff against a frozen fixture, judged by a gate that
runs before any agent. That machinery has to exist — and be proven on itself —
before the first line of pipeline code, or the first phase is reviewed by
judgment instead of by command.

## The central constraint

**No pipeline code lands in this phase.** The gate must prove the gate: the
review tooling is pinned by its own tests on throwaway repos, and the three
load-bearing docs are the only place domain decisions appear.

## DONE command

```
make review-gate SPEC=specs/phase-0-skeleton.md
```

- `make test` — the tooling pins (`tests/test_review_tools.py`,
  `tests/test_check_docs.py`, `tests/test_truth_isolation.py`) green, offline.
- `ruff check` + `ruff format --check` — read-only lint green.
- `make check-docs` — links, named targets, traces, BACKLOG count green.
- Evidence rows — every id below exists; Record updates — every file below is
  in `git diff main...HEAD`.

## Done-when

1. **The offline gate runs green on a clean checkout with no services.**
   *Evidence: row 1.*
2. **The gate refuses what it must refuse.** A SPEC outside `specs/`, a
   non-literal `constant-return`, a mutation target under `tests/`, and a
   missing Evidence test id each produce a one-line refusal / FAIL, never a
   traceback. *Evidence: row 2.*
3. **The mutation sweep never touches the working tree** and reports
   `KILLED`/`SURVIVED` per line with the worktree registry unchanged.
   *Evidence: row 3.*
4. **Truth isolation is enforced structurally** from day one, over
   directories that do not exist yet. *Evidence: row 4.*
5. **The docs are load-bearing**: every relative link in CLAUDE.md, docs/,
   PROJECT_BRIEF, DECISIONS, BACKLOG resolves; every `make` target the living
   docs name exists (plans are link-only); the BACKLOG count matches.
   *Evidence: row 5.*
6. **CI is green on the Phase 0 PR** with SHA-pinned actions,
   `uv sync --locked`, `permissions: contents: read`, `persist-credentials:
   false`, and a SHA-pinned pre-commit hook. *Evidence: row 6 — verified on
   first push; BACKLOG row until then.*

## Evidence (REQUIRED)

| Done-when | Proof |
|---|---|
| 1 | `make review-gate` output line `review-gate OK: 3/3 checks` (no SPEC) / `5/5` with SPEC |
| 2 | `tests/test_review_tools.py::test_spec_outside_specs_is_refused`, `::test_constant_return_value_must_be_a_short_literal`, `::test_mutation_targets_under_tests_are_refused_for_every_operator`, `::test_gate_fails_on_a_missing_evidence_test_id`, `::test_cli_refusals_are_one_line_exit_2` |
| 3 | `tests/test_review_tools.py::test_mutate_reports_survived_and_killed_and_leaves_the_tree_untouched`, `::test_registry_change_is_its_own_latched_outcome` |
| 4 | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth` |
| 5 | `tests/test_check_docs.py::test_every_named_make_target_exists_today`, `::test_backlog_count_matches_today`, `::test_check_links_reports_a_broken_link_and_anchor`; `make check-docs` output line `check-docs OK` |
| 6 | GitHub Actions `ci / lint-test` green on the PR |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all SPEC values, the gate acts only on an existing file under `specs/`; nothing else is derived from the value. | `tests/test_review_tools.py::test_spec_outside_specs_is_refused` — `../x`, absolute, directory, empty |
| For all mutation lines, the working tree and the worktree registry are identical before and after the sweep. | `tests/test_review_tools.py::test_mutate_reports_survived_and_killed_and_leaves_the_tree_untouched`, `::test_registry_change_is_its_own_latched_outcome` |
| For all `constant-return:<v>`, `<v>` is a Python literal ≤ 64 chars; spec text never reaches `exec`. | `tests/test_review_tools.py::test_constant_return_value_must_be_a_short_literal` |
| For all doc citations of a `make` target or a traced symbol, the target/symbol exists as an exact token. | `tests/test_check_docs.py::test_partial_rename_is_a_failure`, `::test_every_named_make_target_exists_today` |
| For all pipeline directories, present or future, no source file mentions truth. | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth`, `::test_a_planted_truth_reference_is_found` (positive control on a tmp tree), `::test_pipeline_dirs_are_derived_from_the_tree` |
| For all four mutation operators, applying the operator to a fixture function changes the source in the documented way. | `tests/test_review_tools.py::test_delete_call_removes_statement_level_calls_only`, `::test_swap_sort_key_reverses_the_tuple_key` (plus the existing sweep test for `invert-guard` / `constant-return`) |
| For all docs-guard checks, a violating input makes the check report an error (no check is vacuous-green). | `tests/test_check_docs.py::test_check_links_reports_a_broken_link_and_anchor`, `::test_check_make_targets_reports_an_unknown_target`, `::test_check_traces_reports_a_renamed_token`, `::test_check_backlog_count_reports_a_mismatch` |

**Amendment (review round 1, 2026-08-25)** — four invariants the round found
missing or mis-stated, added before any fix was implemented:

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| **Trusted origin.** For all user variables of a `make` target, the value reaches Python as ONE single-quoted literal argument with no shell and no make-function expansion, from either origin; and for any confirmation knob, only a COMMAND-LINE origin counts (`$(origin VAR)` = `command line`). `unexport` is not the guard — it only strips the child environment. | `tests/test_makefile.py::test_user_variable_reaches_python_as_one_literal_from_both_origins` (`"; echo pwned; "`, `$(shell …)`, env vs command line via `make -n`), `::test_env_exported_spec_reaches_the_recipe_and_is_validated_in_python` |
| **Sweep baseline.** For all sweeps, the unmutated suite is green in the worktree before any mutation is judged; a red HEAD is a refusal, never `KILLED`. | `tests/test_review_tools.py::test_sweep_refuses_on_a_red_baseline` |
| **Round boundary.** For all N, `review-round-N` names round N and is an ancestor of HEAD, or `read` is a parse error; a caller-supplied N never becomes a boundary unverified. | `tests/test_review_tools.py::test_round_tag_parse_is_anchored_and_never_defaults`, `::test_round_tag_requires_ancestry` |
| **Registry restored.** For all sweeps, the worktree registry after equals the registry before, and a difference is its own latched outcome. | `tests/test_review_tools.py::test_registry_change_is_its_own_latched_outcome` |

Consequence for later phases: every destructive or cloud target (Phases 8–12)
gates `CONFIRM` on `$(origin CONFIRM)` = `command line` inside its one-line
recipe; `unexport` stays for hygiene only. The threat-model table below is
corrected accordingly.

```mutations
scripts/review_common.py::resolve_spec        constant-return:None
scripts/mutate.py::_repo_path                 invert-guard
scripts/mutate.py::_registry_changed          constant-return:False
scripts/mutate.py::_baseline_is_green         constant-return:True
scripts/check_docs.py::token_present          constant-return:True
scripts/check_docs.py::check_links            constant-return:0
scripts/check_docs.py::check_make_targets     constant-return:0
scripts/check_docs.py::check_traces           constant-return:0
scripts/check_docs.py::check_backlog_count    constant-return:0
scripts/round_tag.py::parse                   constant-return:1
scripts/review_gate.py::evidence_ids          constant-return:([],[])
scripts/review_gate.py::_matches              constant-return:True
scripts/review_common.py::make_targets        constant-return:set()
scripts/review_gate.py::resolve_base          invert-guard
scripts/check_docs.py::named_targets          constant-return:set()
scripts/mutate.py::parse_mutations            invert-guard
```

## Pinned decisions (do not re-litigate)

- **Hardened from day one.** The gate scripts carry their full hardening
  (unexpanded `$(value)` + `_Q`, `unexport`, literal-only `constant-return`,
  registry check) — satisfies invariants 1–3. Rejected: a lighter gate.
- **`check_docs.py` keeps four checks with three tooling traces** —
  satisfies invariant 4. Rejected: dropping traces until needed; the token
  matcher is the part that catches renames and costs nothing empty.
- **Truth isolation greps directories that may not exist** — satisfies
  invariant 5. Rejected: adding the test in Phase 2; a guard added after the
  package it guards is added late.
- **Hook wiring local-only; `.claude/settings.json` committed as `{}`.**
  Rejected: committing the hook wiring (auto-executes an inbound branch).
- **Python 3.12 pinned** (DECISIONS Phase 0).
- **No runtime dependency** until the phase that needs it (DECISIONS Phase 0).

## Scope (files)

- `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/PHASES.md`, `DECISIONS.md`,
  `BACKLOG.md`, `PROJECT_BRIEF.md` (§6 renumbered, §7 status),
  `specs/TEMPLATE.md`, `specs/phase-0-skeleton.md`
- `Makefile`, `pyproject.toml`, `uv.lock`, `.python-version`,
  `.pre-commit-config.yaml`, `.github/workflows/ci.yml`,
  `.github/pull_request_template.md`
- `scripts/{review_common,review_gate,mutate,round_tag,check_docs}.py`
- `.claude/agents/*.md`, `.claude/commands/*.md`, `.claude/hooks/run-tests.py`,
  `.claude/settings.json`
- `tests/{conftest,test_review_tools,test_check_docs,test_truth_isolation,test_makefile}.py`

## Record updates (REQUIRED)

- [x] `DECISIONS.md` — "Decisions still in force", "Process", Phase 0 entry
- [x] `docs/PHASES.md` — the re-cut plan (this file is new)
- [x] `CLAUDE.md` — Current status; Commands; Project tooling; BACKLOG count
- [x] `docs/ARCHITECTURE.md` — new; §8 Gotchas empty by construction
- [x] `BACKLOG.md` — five opening rows + "CI green verified on first push" (round 1)
- [x] `PROJECT_BRIEF.md` — §6 renumbered to this plan; §7 status
- [ ] Spec amendments — none (no later spec exists)
- [ ] RESULTS / METRICS / DEPLOYMENT — none
- [ ] README — none (Phase 13; PROJECT_BRIEF.md is the front door until then)

## Threat model (REQUIRED when the phase adds a Makefile target that takes a variable, deletes anything, touches cloud resources, or takes user input)

`review-gate` takes `SPEC`, `BASE`, `DELETED`; `mutate` takes `SPEC`. None
deletes anything in the working tree; `mutate` creates and removes worktrees
under the temp dir `tempfile.mkdtemp` picks (honours `TMPDIR`).

**What the guard is** (corrected in round 1): `$(call _Q,$(value VAR))` passes
the UNEXPANDED value single-quoted, so no shell and no make function ever runs
on it, from either origin; Python then validates. `unexport` only keeps the
value out of the child's environment — an environment-set `SPEC` DOES reach the
recipe, and that is fine because Python re-validates it. Only `$(origin VAR)`
tells command line from environment; no Phase 0 target needs it.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make review-gate SPEC=` | no SPEC → checks a, b only; `SKIP evidence, records` | refused, exit 2, one line | one literal argv token; refused as not-a-file; nothing runs | reaches the recipe as one literal, validated in Python exactly like a command-line value | n/a (no CONFIRM in Phase 0) | `tests/test_makefile.py::test_user_variable_reaches_python_as_one_literal_from_both_origins`, `::test_env_exported_spec_reaches_the_recipe_and_is_validated_in_python`, `tests/test_review_tools.py::test_spec_outside_specs_is_refused` |
| `make mutate SPEC=` | refused: SPEC is empty | refused | one literal; refused | same as above | n/a | same |
| `BASE=` | defaults to `main` | validated `[\w./-]+`, must not start with `-`; else refused | one literal; refused | same | n/a | `tests/test_review_tools.py::test_base_is_validated` |
| `DELETED=` | `SKIP deleted symbols` | literal grep (`-F -w`), never a regex | literal | same | n/a | `::test_deleted_symbol_is_literal_and_git_errors_are_distinct` |

Stated residual: `MAKEFLAGS='SPEC=…'` is a make-level override; threat model
is "mistakes, not a user who controls the environment".

## Review & stack risk

- **code-reviewer** (mandatory): the gate scripts against this CLAUDE.md
  (`OTR_INT` marker consistent between Commands, conftest and the scripts).
- **security-reviewer**: triggered — CI workflow added. Checks SHA pins,
  `--locked`, no credentials, `.gitignore` coverage.
- **functionality-tester**: DONE command; runs `make mutate
  SPEC=specs/phase-0-skeleton.md` and expects 3/3 KILLED.
- **coherence-auditor** at exit: PROJECT_BRIEF §6 phase table vs
  docs/PHASES.md (the brief is the origin record, PHASES is authoritative —
  the brief's table must say so); CLAUDE.md Repo map lists only planned dirs
  marked *(Phase N)*.
- Stack risk: `uv` resolving Python 3.12 on macOS 26 / Python 3.14 host;
  ruff 0.16.3 availability.

## Out of scope (deferred, recorded)

- Golden fixture — Phase 1 (BACKLOG "Phase 0 has no golden fixture").
- dbt SQL mutation operator — BACKLOG.
- README — Phase 13.
