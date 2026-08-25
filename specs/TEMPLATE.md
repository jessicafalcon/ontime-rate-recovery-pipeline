# Phase N — <name> (PROPOSED)

Contract for the `phase-N-<slug>` branch. Source: <docs/PHASES.md entry, or
"post-plan extension — not in the original plan" + the review finding that
originated it>. Depends on <predecessor> merged.

**Status: PROPOSED — do not start until approved.** <Dependency note: "no new
dependencies", or the package and why; any pinned-version feature the phase
relies on is a STOP-and-ask if it turns out unsupported.>

Four sections marked REQUIRED are mandatory; a spec without them is not
approvable (CLAUDE.md → Workflow rules). They exist because the common review
failures are record files lagging code, spec clauses written before the prior
phase landed, and review rounds finding bugs in the previous round's fixes
because the spec pinned a mechanism instead of the property it had to keep. A spec carries at most ~6 pinned decisions / Done-when
items — split larger scope into sub-phases (7a/7b), each from this template.

## Why

<The problem in the reviewer's words, then why this phase and not a fix PR.>

## The central constraint

**<One bolded sentence.>** <What must not move while the phase moves everything
else — byte-identical fixtures, a frozen label set, a pinned number.>

## DONE command

```
make test && make lint && <the phase's own live gate>
```

- <One bullet per command segment: what it proves and which pin / golden it
  reproduces.>

## Done-when

1. **<Item.>** <Behavioural clause the code can falsify.> *Evidence: row 1.*
2. …

(≤ ~6 items. An item is a contract, not a narrative. `docs/PHASES.md` carries
the same clauses; if the landing diverges, PHASES.md is corrected at exit — the
spec and DECISIONS are authoritative.)

## Evidence (REQUIRED)

Every Done-when item names the test or command output that proves it. **An item
without evidence is not a Done-when item** — find its proof or cut it. The
functionality-tester confirms every named test exists and exercises the claim;
a named-but-missing test is a blocker. `make review-gate SPEC=…` checks every
`tests/….py::test_x` id and `make <target>` here exists.

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_<x>.py::test_<y>` / `make <target>` output line "<…>" |
| 2 | … |

The same table, filled with the actual run's output, is item 2 of "Before
reporting DONE" (CLAUDE.md).

## Invariants (REQUIRED)

Properties, not mechanisms — written BEFORE any pinned decision names how the
code works. Each is a universally quantified sentence ("for all X, Y holds")
paired with the scenario test that would falsify it. The test is named here
first; the mechanism comes later and must satisfy the invariant, never the
reverse. Reviewers read this list (code-reviewer "Invariants" check), the
functionality-tester mutates the code that upholds each one, the coherence
auditor greps the records against it.

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all <X>, <Y>. | `tests/test_<x>.py::test_<scenario>` — <the scenario in one clause> |
| … | … |

Rules:

- **A mechanism is not an invariant.** "A status column marks provisional
  rows" is a mechanism; the invariant is "for every prompt×user whose lookback
  has closed, the label is identical on every later run". State the second; let
  Pinned decisions name the first by reference ("satisfies invariant 2").
- **Every invariant names its falsifying test before any code exists** — a test
  that reproduces the scenario under the quantifier (a second landing, a replay,
  equal sort keys, a non-UTC machine, a different `run_date`), not the
  mechanism's happy path.
- **A fix that changes a data structure, a write path, or who-writes-what is a
  design change**: one-paragraph amendment to this section naming the invariant
  it restores (CLAUDE.md "Fix amendments").
- **Every invariant's upholding Python gets a mutation line.** `make mutate
  SPEC=…` reads ONE fenced block here, `path.py::function  operator`, one
  mutation per line; operators exactly `delete-call`, `constant-return:<v>`,
  `invert-guard`, `swap-sort-key`. Each is applied to HEAD in a throwaway
  worktree and the offline suite must go red; a `SURVIVED` line is a
  correctness finding. A spec with no block is GATE RED under `/review-round`.
  dbt SQL has no operator yet (BACKLOG) — an invariant upheld only in SQL names
  the dbt unit test / data test that pins it instead, in the table.

  ```mutations
  generator/generate.py::emit_events        delete-call
  eval/score.py::label_accuracy             constant-return:1.0
  serving/writeback.py::should_replace      invert-guard
  eval/simulate.py::pick_window             swap-sort-key
  ```

Worked example of the rule: a decision that read "the write-back keeps a
last-written marker" pinned a mechanism; the invariant it stood in for is "for
all rows, a replacement happens iff `(model_version, computed_as_of)` is
strictly greater" — data-derived, never caller-supplied. The pinned decision
then reads "replace-iff-greater on the row's own columns — satisfies invariant
1; the marker is gone because no mechanism storing a caller-supplied value can
satisfy it".

## Pinned decisions (do not re-litigate)

Each decision may name a mechanism only by reference to the invariant it
satisfies. A decision that pins a mechanism no invariant requires is a smell:
write the invariant or drop the pin.

- **<Decision.>** <Why; the alternative rejected in one clause; "satisfies
  invariant N".>
- … (≤ ~6)

## Scope (files)

- <Every file the phase touches, code and record alike.>

## Record updates (REQUIRED)

The explicit list of record files this phase must change. Checked off in the
report (checklist item 6); `make review-gate SPEC=…` diffs this list against
`git diff main...HEAD` — a listed file absent from the diff is a FAIL, a record
file in the diff but off the list is a WARN.

- [ ] `DECISIONS.md` — Phase N entry (every non-obvious choice; supersede
      pointers on any earlier entry this phase reverses)
- [ ] `docs/PHASES.md` — Phase N row: Done-when as landed; "Delivered" paragraph
- [ ] `CLAUDE.md` — Current status; Commands (every new/changed `make` target);
      allowlist (if a package was added); Event model facts (if a column
      changed); Repo map (if a package moved); BACKLOG count
- [ ] `docs/ARCHITECTURE.md` §8 Gotchas — every stack surprise found live
      (+ the §2/§3 clause if a column or arrow moved)
- [ ] `BACKLOG.md` — rows closed (strike-through + "DONE Phase N") and rows
      opened (deferred findings with a trigger)
- [ ] Spec amendments — every LATER spec this phase invalidates gets a
      "Pre-branch reconciliation required" banner naming the clauses
- [ ] `docs/RESULTS.md` / `docs/METRICS.md` / `docs/DEPLOYMENT.md` — only the
      blocks this phase regenerates
- [ ] `README.md` — demos / commands touched

(A row that applies to no file this phase is written WITHOUT backticks — e.g.
"- [ ] README — none" — the gate treats every backticked path on a `- [ ]` line
as a file that must be in the diff.)

## Threat model (REQUIRED when the phase adds a Makefile target that takes a variable, deletes anything, touches cloud resources, or takes user input)

For each such target, the behaviour — and the test pinning it — for:

- an **empty value** (`make <target> PROFILE=`);
- a **path-escaping value** (`PROFILE=../x`);
- a **shell-metacharacter value** (a value containing `"; `);
- the **variable exported from the environment** instead of the command line,
  and for any confirmation knob, **`$(origin)` gating** — `CONFIRM=yes` counts
  only from the command line;
- for a cloud target: what it costs if it runs twice, and what it destroys.

The settled shape: one Python process validates
the value (`[a-z0-9_]+`), derives every path from it (no path argument exists to
escape with), prompts on a tty, then acts; every recipe is one line; every user
variable reaches Python unexpanded and single-quoted (`$(call _Q,$(value VAR))`)
and is `unexport`ed; residuals (`MAKEFLAGS`) are STATED with the threat model
("mistakes, not a user who controls the environment").

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make <target>` | … | … | … | … | … | `tests/test_makefile.py::…` |

(If the phase adds no such target: keep the heading and write "None — no new
Makefile target takes a variable, deletes, touches cloud, or reads input.")

## Review & stack risk

- **code-reviewer** (mandatory): <what it checks here>.
- **security-reviewer** (<mandatory if CI / .env / infra / IAM / credentials /
  a destructive or cloud target are touched, else "not triggered — reason">).
- **functionality-tester**: DONE command + <the phase's specific negative tests>.
- **coherence-auditor** at exit: <the stale sentences it must find gone>; diffs
  the Record-updates list against the actual diff.
- Stack risk: <pinned-version / dialect features to verify in the first hour;
  STOP and report before any workaround; findings go under ARCHITECTURE §8>.

## Out of scope (deferred, recorded)

- <Each item with where it is recorded — BACKLOG row, a later phase's spec.>
