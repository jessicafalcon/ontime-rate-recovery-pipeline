# Phase 13 — Docs, dashboard, narrative (PROPOSED)

Contract for the `phase-13-docs-narrative` branch. Source: docs/PHASES.md
Phase 13. Depends on Phase 12 (`phase-12-live-run`) merged to main.

**Status: PROPOSED — do not start until approved.** No new dependencies: the
README first-screen block and the findings chart are rendered by the SAME
marker-confined writer Phase 6 uses (`eval/blocks.py`), from numbers that already
live in `tests/pins.py` and the committed `docs/RESULTS.md` blocks; the chart is
a hand-written deterministic SVG string, NOT matplotlib (a plotting package for a
static picture is a STOP-and-ask, CLAUDE.md allowlist). If a Markdown renderer on
GitHub turns out to drop the inline `<svg>` or a Mermaid fence this spec assumes,
that is a STOP-and-report (fall back to a committed PNG is its own decision), not
a silent workaround.

## Reconciliation against main (first commit on the branch)

Main as it actually is (post-Phase-12, `964dff9` at branch creation): Phases
0–12 merged. There is **no `README.md`** and **no `docs/img/`** yet;
`scripts/check_docs.py` already treats `README` as a LIVING doc "read only if one
is tracked — none today" (its link/target checks turn on automatically the moment
the file is committed). The generated-block machinery exists
(`eval/blocks.py::find_block`/`replace_block`/`write_block`/`diff_block`), used by
`make simulate` (`docs/RESULTS.md`) and `make power` (`docs/AB_DESIGN.md`); the
`power` target (no PROFILE, `--write $(value WRITE)`) is the shape `make readme`
copies. Every served number is a pin (`tests/pins.py`) or a committed RESULTS
block. The whole GCP path is proven and torn down (Phase 12); `docs/DEPLOYMENT.md`
is the ask-first cloud runbook.

Reconciliation items (the developer's calls at phase entry):

1. **"Phases 1–8" (PHASES.md Done-when) = the local, no-cloud chain.** The
   cold-reader path a README must make runnable is `make setup` → `make seed
   PROFILE=tiny` → `make dbt-build PROFILE=tiny` → `make eval PROFILE=tiny` →
   `make report PROFILE=tiny` → `make simulate PROFILE=tiny` → `make writeback
   PROFILE=tiny` → `make pipeline PROFILE=tiny`, on `fixtures/tiny`, no GCP. The
   cloud phases (9–12) are LINKED as the ask-first runbook (`docs/DEPLOYMENT.md`),
   never presented as a README quickstart command. An invariant pins that no
   README quickstart command is a cloud-cost / `CONFIRM` / `tf-*` target.

2. **The first screen is one generated block, not typed prose.** The README
   carries a `<!-- readme:begin -->` … `<!-- readme:end -->` block written by
   `make readme [WRITE=yes]` (same check-mode/`WRITE=yes` shape as `simulate` /
   `power`): every number in it — tiny label accuracy, tiny/medium MAE + coverage,
   the medium recommended-vs-baseline lift, the on-time rates — is read from
   `tests/pins.py` and the committed RESULTS blocks, never hand-typed.
   `tests/test_readme.py` regenerates it byte-identically under `make test` (the
   CI proof, exactly as `tests/test_power.py::test_ab_design_block_matches_the_committed_block`
   is). Prose OUTSIDE the markers (the tagline, the quickstart list, the docs
   index) is the author's and is link-/target-checked by `check-docs`, not pinned.

3. **The findings chart is a deterministic SVG with no new dependency.**
   `docs/img/lift.svg` is rendered from the same medium simulation numbers by a
   Python string template (`eval/readme.py`), a wholly-generated file
   `make readme` rewrites and check-mode diffs byte-for-byte. Rejected: matplotlib
   / plotly (a STOP-and-ask package for a static picture); a hand-drawn SVG (not
   regenerable, so it drifts silently).

4. **`check-docs` widens to README and to every named guard/target.** README
   becomes tracked, so `check-docs`'s link and make-target checks cover it with no
   code change; `TRACES` gains a row per guard/target the README and the new
   docs name by identity (the Done-when's "traces over every named guard and
   target" clause). Widening `TRACES` is a visible edit, as always.

5. **Surface → agents (plan for the honest surface).** The range is not docs-only:
   `make readme` touches `eval/readme.py` (new), `eval/cli.py` (a `readme`
   subcommand), the `Makefile` (the `readme` target), and `tests/` — so the range
   runs the gate + code-reviewer + functionality-tester (code surface), and the
   phase-exit whole-repo **coherence-auditor** is mandatory. security-reviewer is
   NOT triggered: no CI / `.env` / `infra/` / credential / destructive / cloud
   target is touched (the `readme` target is non-destructive, offline, no
   `CONFIRM`).

6. **What is IN (docs) vs OUT (code/cloud).** IN this phase: the README (first
   screen + Mermaid diagram + quickstart + docs index), `docs/INSIGHT.md` (the
   one-page writeup naming the tiny NEGATIVE lift and the simulation's
   circularity honestly), an Amplitude-export mapping subsection in ARCHITECTURE,
   a privacy/PII paragraph in ARCHITECTURE §6, the stack-roles table, and
   condensing CLAUDE.md's multi-paragraph "Current status" to one paragraph (the
   phase history lives in PHASES.md / DECISIONS). OUT, each recorded as a BACKLOG
   row with a trigger and named as its own future branch, NOT built here: the GCS
   remote tfstate backend (`fix/tf-remote-state`, row 16 — the confidentiality
   half's trigger fires at this public-facing exit); a large-profile BigQuery cost
   run; making the DAG truly runnable on Composer (Cosmos / KubernetesPodOperator,
   which would supersede Option A); the WIF CI parity leg (row 30). **Open
   question for the developer (a plan change, not mine to decide): do we add
   phases 14+ to `docs/PHASES.md`, or is Phase 13 the planned close?** If closed,
   the OUT items stay BACKLOG rows only; if extended, each becomes a PHASES row.
   PHASES.md is untouched by this spec until that call is made.

## Why

Every capability is built, proven, and torn down, but the repo has no front door:
a reader lands on `CLAUDE.md`'s dense operating manual with no one-screen "what
this is, what it found, how to run it". The hiring-review feedback is specifically
that the project reads as machinery without a narrative — no README, no findings
chart, no honest one-page insight (including that tiny's lift is *negative* by
construction and the simulation is circular). Phase 13 is the docs-and-narrative
capstone: it adds the front door and the story WITHOUT restating any number by
hand — every figure a reader sees is the same pinned number the suite already
guards. It is not a fix PR: it adds a new generated artifact (`make readme`), a
new doc surface, and widens `check-docs`; the mechanism is Phase 6's block writer,
reused.

## The central constraint

**Not one number a reader sees is typed by a human.** Every figure on the README
first screen and in the findings chart is rendered from `tests/pins.py` /
the committed `docs/RESULTS.md` blocks by `make readme`, and `make test`
regenerates both byte-identically; a drift is a red test, never a hand-edited
constant. The pins, fixtures, macros, and models do not move — Phase 13 adds only
docs and one generated artifact.

## DONE command

```
make test && make lint && make review-gate SPEC=specs/phase-13-docs-narrative.md
```

- `make test` — the offline suite, now including `tests/test_readme.py` (the
  first-screen block AND the lift SVG regenerate byte-identically — invariants
  1–2) and the quickstart-is-cloud-free assertion (invariant 4); every existing
  pin unchanged.
- `make lint` — ruff check + format, read-only.
- `make review-gate SPEC=…` — the offline gate (`make test` + ruff +
  `make check-docs`, now covering the tracked README and the widened `TRACES`) +
  every Evidence test id / `make` target here exists and every Record-updates file
  is in `git diff main...HEAD`.

The third Done-when clause (a cold reader can run Phases 1–8 from README alone) is
proven MANUALLY, like Phase 12's live half: a cold clone of the branch into a
scratch dir, the README quickstart commands run verbatim, their output pasted into
Evidence row 3. It is not one command because it asserts a human can follow prose.

## Done-when

1. **`make check-docs` is green with README tracked and `TRACES` widened.** Every
   relative link/anchor in the README resolves; every `make <target>` the README
   names exists in the Makefile; every `(file, token)` in the widened `TRACES`
   (each new guard/target the docs name) is present in source as an exact token;
   the BACKLOG count line still matches. *Evidence: row 1.*
2. **Every number on the README first screen and in the chart is sourced from a
   generated block.** The `readme:begin` block and `docs/img/lift.svg` regenerate
   byte-identically from `tests/pins.py` + the committed RESULTS blocks under
   `make test`; `make readme` (check mode) diffs both to empty. *Evidence: row 2.*
3. **A cold reader can run Phases 1–8 from the README alone.** A fresh clone,
   following only the README quickstart (`make setup` … `make pipeline
   PROFILE=tiny`, no cloud), reaches `pipeline OK: tiny`; no quickstart command is
   a cloud-cost / `CONFIRM` / `tf-*` target (invariant 4). *Evidence: row 3.*

(3 items — the three PHASES.md Done-when clauses verbatim. `docs/PHASES.md`
carries the same clauses; the spec and DECISIONS are authoritative if the landing
diverges.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `make check-docs` prints its four `OK` lines with README tracked (link/target/trace/backlog) — run in the report; `make review-gate SPEC=specs/phase-13-docs-narrative.md` line "check-docs … OK" |
| 2 | `tests/test_readme.py::test_first_screen_block_matches_committed` (block == `eval.readme.render_block()`) and `::test_lift_svg_matches_committed` (`docs/img/lift.svg` == `eval.readme.render_svg()`); `make readme` (check mode) prints `readme OK: first-screen block matches, lift.svg matches` |
| 3 | A cold `git clone`/`worktree` of the branch into a scratch dir; the README quickstart run verbatim ending in `pipeline OK: tiny`; the output pasted here. Plus `tests/test_readme.py::test_quickstart_commands_are_cloud_free` (no quickstart `make` command is in the cloud-cost/CONFIRM/tf set) |

The same table, filled with the actual run's output, is item 2 of "Before
reporting DONE".

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For every number a reader sees (the README first-screen block), the block regenerates byte-identically from `tests/pins.py` + the committed RESULTS blocks — nothing in it is hand-authored. | `tests/test_readme.py::test_first_screen_block_matches_committed` — the committed block must equal `eval.readme.render_block()`; a hand-edited figure inside the markers fails it |
| For the findings chart, `docs/img/lift.svg` regenerates byte-identically from the same medium simulation numbers. | `tests/test_readme.py::test_lift_svg_matches_committed` — the committed SVG must equal `eval.readme.render_svg()`; a hand-edited path/label fails it |
| For every `make` command the README names, that target exists in the Makefile. | `scripts/check_docs.py` target check over the now-tracked README (`make check-docs`) — a renamed/removed target the README still cites FAILs |
| For every command in the README quickstart, it is offline and free — no cloud-cost / `CONFIRM` / `tf-*` target appears there (the cold-reader path never bills). | `tests/test_readme.py::test_quickstart_commands_are_cloud_free` — parse the quickstart fenced block; any `make` command carrying `TARGET=bigquery`/`TARGET=spanner`, `CONFIRM=`, or a `tf-` name fails it |

```mutations
eval/readme.py::render_block      constant-return:""
eval/readme.py::render_svg        constant-return:""
```

(The two offline invariants 1–2 are upheld by the two new renderers in
`eval/readme.py`; each mutation neuters one, turning `test_readme.py`'s
byte-identity assertions red. Invariant 3 is a `check-docs` guard, not new Python;
invariant 4 is upheld by the parse-and-check test, whose logic is a straight
membership assertion — pinned by the test, not by a mutation of new production
code. The block WRITER itself (`eval/blocks.py`) already carries Phase 6's
mutation coverage via `test_simulate.py` / `test_power.py`.)

## Pinned decisions (do not re-litigate)

- **The first screen is one `make readme`-generated block; the chart is one
  generated SVG.** `eval/readme.py` renders both from `tests/pins.py` + the
  committed RESULTS blocks; `eval/cli.py` gains a `readme` subcommand and the
  `Makefile` a `readme` target mirroring `power` (no PROFILE, `--write
  $(value WRITE)`, already `unexport`ed). Satisfies invariants 1–2. Rejected:
  typing the numbers into README prose (drifts the instant a pin moves; the
  hiring-review's exact complaint is un-sourced figures). (Reconciliation 2, 3.)
- **The README quickstart is the local, no-cloud chain; cloud is a runbook link.**
  The quickstart is `setup → seed → dbt-build → eval → report → simulate →
  writeback → pipeline` on tiny; Phases 9–12 are one link to
  `docs/DEPLOYMENT.md`, never a quickstart command. Satisfies invariant 4.
  Rejected: putting `tf-apply` / `TARGET=bigquery` in the quickstart (a cold
  reader would bill GCP following the front page). (Reconciliation 1.)
- **`check-docs` widens by tracking README and extending `TRACES`; no guard logic
  changes.** README's link/target checks turn on the moment it is committed (the
  guard already reads it "if tracked"); `TRACES` gains one row per guard/target
  the README and new docs name by identity. Satisfies invariant 3. Rejected: a
  bespoke README linter (duplicates `check_docs.py`). (Reconciliation 4.)
- **Narrative is honest, not promotional.** `docs/INSIGHT.md` states plainly that
  tiny's simulated lift is NEGATIVE (its `c-morning` bin-3/10 tie, 20 users — a
  regression pin, not a result) and that the simulation is CIRCULAR (outcomes
  re-drawn from the same latent that generated the data, so it validates the
  served schedule under the data's own rule, says nothing about real users — the
  A/B in `docs/AB_DESIGN.md` is the real test). The medium +0.162371 lift is the
  proof, framed as such. No pin or fixture moves. (Reconciliation 6, IN.)
- **CLAUDE.md's "Current status" condenses to one paragraph; phase history stays
  in PHASES.md / DECISIONS.** The multi-paragraph running log is replaced by a
  single "Phases 0–13 complete; the pipeline runs local (DuckDB) and cloud
  (BigQuery + Spanner + Composer), meter-off by default" paragraph + the pointer
  to PHASES/DECISIONS for the trail. The BACKLOG-count line and Repo-map stay.
  Rejected: leaving the 200-line status block (it is the opposite of a front
  door). (Reconciliation 6, IN.)
- **OUT items are BACKLOG rows named as their own branches; PHASES.md 14+ is the
  developer's open call.** Remote tfstate (`fix/tf-remote-state`, row 16), the
  large-profile cost run, the Composer-runnable DAG (Cosmos/KPO — supersedes
  Option A), the WIF CI leg (row 30) are recorded with triggers, not built.
  Whether to add phases 14+ to `docs/PHASES.md` is asked in the reconciliation
  (item 6) and left to the developer — this spec touches no PHASES future rows.
  (Reconciliation 6, OUT.)

## Scope (files)

- `eval/readme.py` — NEW: `first_screen_rows()` / `render_block(rows)` and
  `render_svg(rows)`, reading `tests/pins.py` + the committed RESULTS blocks.
- `eval/cli.py` — a `readme` subcommand (check mode / `--write yes`), mirroring
  `power_cmd` + `_block_cmd`; the SVG is a whole-file write, the README block is
  marker-confined via `eval/blocks.py`.
- `Makefile` — the `readme` target (no PROFILE; `--write $(call _Q,$(value
  WRITE))`; `WRITE` already in `unexport`).
- `README.md` — NEW: first-screen `readme:begin` block + tagline + Mermaid
  architecture diagram + the cloud-free quickstart list + the docs index +
  the stack-roles table.
- `docs/img/lift.svg` — NEW: the generated findings chart.
- `docs/INSIGHT.md` — NEW: the one-page honest writeup.
- `docs/ARCHITECTURE.md` — an Amplitude-export mapping subsection (envelope →
  the pipeline's columns; §2.1/§3 area) and a privacy/PII paragraph in §6.
- `scripts/check_docs.py` — `TRACES` widened (rows for each named guard/target).
- `tests/test_readme.py` — NEW: the two byte-identity tests + the
  quickstart-is-cloud-free test.
- `CLAUDE.md` — "Current status" condensed; Commands (the `readme` target); Repo
  map (`eval/readme.py`, `docs/INSIGHT.md`, `docs/img/`, README); BACKLOG count.
- `DECISIONS.md`, `docs/PHASES.md`, `BACKLOG.md` — records (below).

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 13 entry (generated first screen + SVG over typed
      numbers; cloud-free quickstart; honest-narrative call; the OUT-item
      dispositions; whichever way the phases-14+ question is answered)
- [ ] `docs/PHASES.md` — Phase 13 row: Done-when as landed; "Delivered" paragraph
      (and, only if the developer says so, phases 14+ rows — otherwise untouched)
- [ ] `CLAUDE.md` — Current status condensed; Commands (`make readme`); Repo map
      (`eval/readme.py`, `docs/INSIGHT.md`, `docs/img/lift.svg`, `README.md`);
      BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — Amplitude-export mapping subsection + §6
      privacy/PII paragraph (no §2/§3 arrow moves; §8 only if a live doc surprise)
- [ ] `BACKLOG.md` — rows opened for the OUT items with triggers (remote tfstate
      row 16's confidentiality half now DUE → `fix/tf-remote-state`; the
      Composer-runnable DAG; the large-profile cost run; row 30 re-stated); row 15
      re-confirmed at exit
- [ ] Spec amendments — none (Phase 13 is the docs capstone; no later spec exists)
- [ ] RESULTS / METRICS / DEPLOYMENT — none (Phase 13 reads RESULTS, writes none
      of these)
- [ ] `README.md` — the phase's own new file (first screen, quickstart, index)

## Threat model (REQUIRED when the phase adds a Makefile target that takes a variable, deletes anything, touches cloud resources, or takes user input)

One new target, `make readme [WRITE=yes]`, mirroring `make power`: no PROFILE, no
path variable, non-destructive (it rewrites the marked README bytes and the
wholly-generated `docs/img/lift.svg`, nothing else), no cloud, no `CONFIRM`.

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make readme` | `WRITE=` → check mode (the default; `--write ""` is the not-`yes` branch → check, never write) | no path variable exists — `readme` takes only `WRITE`, whose sole accepted value is the literal `yes`; anything else refuses (`WRITE takes only the literal yes`) | `WRITE='"; '` reaches `eval.cli` single-quoted via `$(call _Q,$(value WRITE))` and is rejected as not-`yes` before any write; never a shell token | `WRITE` is `unexport`ed (Makefile line 15); an env-exported `WRITE=yes` still reaches the recipe via `$(value WRITE)` and would WRITE — but `readme` is non-destructive and marker-confined, so an accidental env `WRITE=yes` only rewrites the same generated bytes (identical output), never a data loss; stated, not gated | not applicable — `readme` takes no `CONFIRM` (nothing destructive or cloud) | `tests/test_readme.py::test_readme_write_takes_only_yes`, `tests/test_makefile.py` (the `_Q`/`unexport` shape shared with `power`) |

`make readme` writes only `README.md` (between its markers — a missing pair is a
refusal, `eval/blocks.py`) and `docs/img/lift.svg` (wholly generated). It reads
`tests/pins.py` and `docs/RESULTS.md`; it writes no table, no fixture, nothing
under `fixtures/`.

## Review & stack risk

- **code-reviewer** (triggered — `eval/`, `Makefile`, `scripts/`, `tests/` in
  Scope): the `readme` renderer reads pins/RESULTS and never re-derives a number;
  the writer is `eval/blocks.py` reused (marker-confined, refuses a missing pair);
  the SVG is a deterministic string (no clock, no order dependence); no pin,
  fixture, or model moves; the quickstart is cloud-free.
- **security-reviewer** (NOT triggered — no CI / `.env` / `infra/` / IAM /
  credential / destructive / cloud target in Scope; `make readme` is offline and
  non-destructive with no `CONFIRM`).
- **functionality-tester** (triggered): the DONE command; the two byte-identity
  tests; the quickstart-is-cloud-free test; the mutation block (`render_block` /
  `render_svg` neutered → `test_readme.py` red); the cold-clone quickstart run
  (Evidence row 3).
- **coherence-auditor** at exit (MANDATORY — phase exit, whole repo): the README's
  Mermaid diagram names the same components as CLAUDE.md's ASCII diagram; no
  number is typed where a pin exists; the condensed CLAUDE status contradicts
  nothing in PHASES/DECISIONS; the Record-updates list matches the diff; every
  `TRACES` row resolves.
- Stack risk: GitHub's Markdown pipeline may strip an inline `<svg>` or fail a
  Mermaid fence — verify the rendered README on the branch's GitHub page before
  PR; a stripped SVG is a STOP (a committed PNG rendered from the same numbers is
  a fallback decision, not a silent swap). `check-docs`'s anchor-slug algorithm
  must match GitHub's for the docs-index links — verify the index links resolve on
  GitHub, not only under `check_docs.py`.

## Out of scope (deferred, recorded)

- The GCS remote tfstate backend — BACKLOG row 16 (confidentiality half DUE at
  this public-facing exit); proposed as `fix/tf-remote-state`.
- A large-profile BigQuery cost run — new BACKLOG row (trigger: a decision to
  publish real-scale cost numbers).
- Making the DAG runnable on Composer (Cosmos / KubernetesPodOperator, superseding
  Option A) — new BACKLOG row (trigger: a scheduled cloud run is wanted).
- The WIF CI parity leg — BACKLOG row 30, re-stated.
- Adding phases 14+ to `docs/PHASES.md` — the developer's open call (reconciliation
  item 6); not decided by this spec.
