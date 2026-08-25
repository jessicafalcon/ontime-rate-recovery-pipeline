# Phase 1 — Event contract, generator, frozen tiny fixture (PROPOSED)

Contract for the `phase-1-event-contract` branch. Source: `docs/PHASES.md`
Phase 1. Depends on Phase 0 merged (PR #1, `adfab2e`).

**Status: PROPOSED — do not start until approved.** One new runtime
dependency: `pydantic` (the Phase 1 allowlist entry, CLAUDE.md → Conventions).
Nothing else; a profile format needing PyYAML is a STOP-and-ask that this
spec avoids by using JSON (stdlib).

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 0 shipped, found before drafting:

1. **`timing_gap` depended on Phase 5** (ARCHITECTURE §2.5 rule 4). Amended in
   `1b50870`, committed alone and approved: delivery + no-action evidence
   alone; Phase 5 scores reachability separately (DECISIONS Phase 1).
2. **No guard knows what a fixture is.** PHASES names `MANIFEST.sha256`; no
   Phase 0 check reads it (BACKLOG "Phase 0 has no golden fixture"). This spec
   adds it as invariant 6 with three tests.
3. **`test_generator_truth_writer_is_confined` is vacuous on main** —
   `generator/` does not exist, the test returns early. Phase 1 makes it live;
   its positive control is invariant 2's second test.
4. **ARCHITECTURE §2.4 puts a per-prompt record in a per-user file**
   ("`truth/users.jsonl`: per user … ; per prompt the assigned cause"). Pinned
   here as two files, `truth/users.jsonl` + `truth/prompts.jsonl`; §2.4 is
   corrected in the Record updates.
5. **PR #1 was merged with a merge commit, not squashed** (`adfab2e`, 15
   commits on main). No action; noted so the git-workflow sentence in
   CLAUDE.md is read as intent, not history.

## Why

Every later phase is judged as a diff against `fixtures/tiny/`. That fixture
has to exist, be exactly reproducible from `(seed, profile)`, carry the truth
the eval will score against, and be unable to drift without a signed-off
re-freeze — or Phases 2–8 are reviewed by judgment again.

## The central constraint

**The generator is a pure function of `(seed, profile)`, and `fixtures/` is
read-only after this phase.** No wall clock, no UUID, no filesystem or dict
iteration order reaches the output; `make seed` never writes under
`fixtures/`; a change to any fixture byte is a gate FAIL unless this spec (or a
later one) declares the re-freeze.

## DONE command

```
make review-gate SPEC=specs/phase-1-event-contract.md && make seed PROFILE=tiny && make seed PROFILE=tiny
```

- `make review-gate SPEC=…` — offline suite (every knob test, determinism,
  truth isolation, manifest, freeze guard) + lint + check-docs + Evidence /
  Record checks + the new fixture check.
- `make seed PROFILE=tiny` twice — each run regenerates into
  `data/out/tiny/`, hashes its own output, compares to
  `fixtures/tiny/MANIFEST.sha256`, prints `seed OK: <n> files, manifest
  match`; exit 1 on any mismatch. Two runs = two matches = byte-identical to
  the committed fixture.

## Done-when

1. **Byte-identical generation.** For `(seed, profile)` fixed, two runs of the
   generator — in one process, in two processes, and under a non-UTC `TZ` —
   write identical bytes, equal to the committed `fixtures/tiny/`.
   *Evidence: row 1.*
2. **Every knob is exercised by a test that observes its effect.** users,
   days, tz mix (incl. the SCD2 change rate), upload-fault rate,
   delivery-fault rate, reachable-width, duplicate injector, late-arrival
   injector, clock-skew injector — nine tests, each turning one knob and
   asserting the output moves the documented way. *Evidence: row 2.*
3. **Truth is written, never read.** `test_truth_isolation.py` green with
   `generator/` real; the confinement test is no longer vacuous. *Evidence:
   row 3.*
4. **Pydantic models are the schema.** Every emitted event, dim row and truth
   record is a validated model instance; an event that fails validation is
   never written; the envelope column set is exactly ARCHITECTURE §2.1.
   *Evidence: row 4.*
5. **The fixture cannot drift silently.** Committed fixture bytes match the
   manifest; the gate FAILs when a manifest or a fixture file is in
   `git diff BASE...HEAD` without a `Freeze:` declaration in the SPEC; `make
   freeze` refuses without `CONFIRM=yes` from the command line. *Evidence:
   row 5.*
6. **`medium` is defined, not committed.** `generator/profiles/medium.json`
   validates and generates; nothing under `fixtures/medium/`. *Evidence:
   row 6.*

## Evidence (REQUIRED)

| Done-when | Proof |
|---|---|
| 1 | `tests/test_generator.py::test_two_runs_are_byte_identical`, `::test_two_processes_under_different_tz_are_byte_identical`, `tests/test_fixture.py::test_regenerated_tiny_matches_manifest`; `make seed PROFILE=tiny` output line `seed OK` |
| 2 | `tests/test_knobs.py::test_users_knob`, `::test_days_knob`, `::test_tz_mix_knob`, `::test_upload_fault_rate_knob`, `::test_delivery_fault_rate_knob`, `::test_reachable_width_knob`, `::test_duplicate_injector_knob`, `::test_late_arrival_injector_knob`, `::test_clock_skew_injector_knob` |
| 3 | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth`, `::test_generator_truth_writer_is_confined`, `::test_generator_confinement_is_not_vacuous` |
| 4 | `tests/test_models.py::test_envelope_columns_are_exactly_the_contract`, `::test_invalid_event_is_never_written`, `::test_event_type_and_properties_agree`, `::test_truth_cause_is_one_of_five` |
| 5 | `tests/test_fixture.py::test_committed_tiny_matches_manifest`, `tests/test_review_tools.py::test_gate_fails_on_manifest_change_without_freeze_declaration`, `::test_gate_fails_on_fixture_change_without_manifest_change`, `tests/test_makefile.py::test_freeze_requires_confirm_from_the_command_line` |
| 6 | `tests/test_profiles.py::test_every_profile_validates`, `::test_medium_generates_and_is_not_committed` |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| 1. For all `(seed, profile)`, the generator's bytes are identical across runs, processes and host time zones; emit order is arrival order (`server_upload_time`, then `insert_id`), never insertion order. | `tests/test_generator.py::test_two_runs_are_byte_identical`, `::test_two_processes_under_different_tz_are_byte_identical` (`TZ=America/New_York` vs `TZ=UTC` subprocesses), `::test_emit_order_is_arrival_order_with_insert_id_tiebreak` (two events with equal upload time) |
| 2. For all source files outside `generator/truth.py`, `generator/models.py`, `eval/` and `tests/`, the word truth does not appear; the guard finds a planted reference in `generator/`. | `tests/test_truth_isolation.py::test_pipeline_dirs_never_mention_truth`, `::test_generator_truth_writer_is_confined`, `::test_generator_confinement_is_not_vacuous` (tmp tree with `generator/x.py` naming truth) |
| 3. For all emitted records, the record is a validated pydantic instance whose columns are exactly the contract's; `event_properties` carries exactly the keys §2.2 names for its `event_type`. | `tests/test_models.py::test_envelope_columns_are_exactly_the_contract`, `::test_invalid_event_is_never_written` (a bad record raises before any byte is written), `::test_event_type_and_properties_agree` |
| 4. For all prompts, truth carries exactly one cause from the five-label set, and the emitted events are consistent with it: `delivery_fault` ⇒ no `prompt_delivered`; `on_time` ⇒ both clocks inside the window; `upload_fault` ⇒ client inside, received outside (or `upload_failed` with no `response_recorded`); `timing_gap` ⇒ delivered, no `capture_started`/`response_recorded`; `unattributed` ⇒ skew beyond `SKEW_MAX_MIN`. | `tests/test_models.py::test_truth_cause_is_one_of_five`, `tests/test_generator.py::test_events_are_consistent_with_assigned_cause` (checks every prompt in a seeded run against the rule for its cause) |
| 5. For all knobs, turning the knob changes the output in its documented direction and touches nothing else (a rate of 0 produces zero of that fault; duplicate/late/skew injectors never change a prompt's cause except skew ⇒ `unattributed`). | the nine `tests/test_knobs.py` tests (Evidence row 2), each a two-profile comparison at rate 0 vs rate > 0 |
| 6. For all fixture profiles, the committed bytes hash to the manifest, a fresh regeneration hashes to the manifest, and no fixture byte changes in a range without a `Freeze: fixtures/<p>/MANIFEST.sha256` line in that range's SPEC. | `tests/test_fixture.py::test_committed_tiny_matches_manifest`, `::test_regenerated_tiny_matches_manifest` (into `tmp_path`), `tests/test_review_tools.py::test_gate_fails_on_manifest_change_without_freeze_declaration`, `::test_gate_fails_on_fixture_change_without_manifest_change`, `::test_gate_accepts_a_declared_freeze` |
| 7. For all `seed` runs, nothing under `fixtures/` is written; for all `freeze` runs, the copy happens only with `CONFIRM=yes` from the command line. | `tests/test_generator.py::test_seed_never_writes_under_fixtures`, `tests/test_makefile.py::test_freeze_requires_confirm_from_the_command_line` (`make -n` under env-exported vs command-line `CONFIRM`) |
| 8. For all users, `dim_user` is a valid SCD2 history: rows per user are contiguous, non-overlapping, exactly one open (`valid_to` empty), and the tz valid at each event's `client_event_time` is the one the generator used for that event's local time. | `tests/test_dims.py::test_dim_user_is_valid_scd2`, `::test_tz_change_users_have_two_rows_and_events_use_the_right_one` |

```mutations
generator/generate.py::arrival_order            swap-sort-key
generator/generate.py::assign_cause             constant-return:"on_time"
generator/generate.py::inject_duplicates        delete-call
generator/generate.py::inject_late_arrival      delete-call
generator/generate.py::inject_clock_skew        delete-call
generator/response.py::responds                 constant-return:True
generator/dims.py::tz_at                        constant-return:"UTC"
generator/manifest.py::compute                  constant-return:{}
generator/manifest.py::matches                  constant-return:True
generator/writer.py::write_jsonl                invert-guard
generator/profiles.py::load                     invert-guard
scripts/review_gate.py::check_fixtures          constant-return:True
scripts/review_gate.py::freeze_declarations     constant-return:set()
```

## Pinned decisions (do not re-litigate)

- **Cause-first generation.** For each prompt×user the generator draws the
  cause, then emits the events that cause implies; injectors run after and
  may not change it (skew excepted — it sets `unattributed` by definition).
  Truth is exact by construction, not inferred — satisfies invariants 4, 5.
  Rejected: emit events from a behavioural model and label afterwards (truth
  becomes a second attribution implementation to keep in sync).
- **One response function, shared with Phase 6.** `generator/response.py::
  responds(local_send_time, user_truth, window_minutes, rng) -> bool` is pure
  and seeded by the caller; `eval/simulate.py` imports it unchanged in
  Phase 6 — satisfies invariant 1 (no hidden state). Rejected: inlining the
  draw in the event loop (Phase 6 would re-implement it).
- **Output layout and serialization.** `raw/events_<YYYY-MM-DD>.jsonl`, one
  file per UTC `server_upload_time` date (the landing unit Phase 7 replays);
  `dims/dim_user.csv` (dbt seed shape); `truth/users.jsonl` +
  `truth/prompts.jsonl`. Lines are `json.dumps(sort_keys=True,
  separators=(",", ":"))` + `\n`; timestamps are Amplitude export strings
  `YYYY-MM-DD HH:MM:SS.ffffff` in UTC; ids are zero-padded counters
  (`u-000001`, `p-000001`, `d-000001`, `insert_id` = `e-<counter>`);
  `sim_start` = `2026-01-05 00:00:00` UTC, fixed — satisfies invariants 1, 3.
  Rejected: one `events.jsonl` (Phase 7 would need to split it).
- **`seed` writes `data/out/<profile>/` only and self-checks; `freeze` is the
  only writer of `fixtures/`.** `make seed PROFILE=<p>` validates
  `[a-z0-9_]+`, generates, computes the manifest of its output and compares
  to `fixtures/<p>/MANIFEST.sha256` when one exists (exit 1 on mismatch);
  `make freeze PROFILE=<p> CONFIRM=yes` (command-line origin only) copies
  `data/out/<p>/` over `fixtures/<p>/` and writes the manifest — satisfies
  invariants 6, 7. Rejected: `seed` writing straight into `fixtures/` (the
  DONE command would then be able to "fix" its own golden).
- **Freeze guard lives in the review gate.** `review_gate.py::check_fixtures`
  FAILs when `git diff BASE...HEAD` touches `fixtures/**` unless the SPEC
  contains `Freeze: fixtures/<p>/MANIFEST.sha256` naming that profile, and
  FAILs when a `fixtures/<p>/` file changes without its manifest changing;
  with no SPEC a fixture change is FAIL outright. Manifest format is
  `sha256sum`-compatible (`<hex>  <path>` per line, paths sorted, relative to
  `fixtures/<p>/`) — satisfies invariant 6. Rejected: a `check_docs` TRACES
  row (it checks token presence, not diff membership).
- **Profiles are JSON validated by `generator/profiles.py::Profile`.** Every
  knob is a required field (no defaults — a missing knob is an error, not a
  silent default): `seed`, `users`, `days`, `tz_mix` (`{tz: weight}`),
  `tz_change_rate`, `cohorts`, `window_minutes`, `upload_fault_rate`,
  `delivery_fault_rate`, `reachable_width_hours`, `duplicate_rate`,
  `late_arrival_rate`, `late_arrival_max_hours`, `clock_skew_rate`,
  `clock_skew_min` (> `SKEW_MAX_MIN`), `organic_opens_per_day`. `tiny` = 20
  users × 7 days, `medium` = 2 000 × 30 — satisfies invariant 5. Rejected:
  Python-module profiles (executable config), YAML (a package).

**Freeze: fixtures/tiny/MANIFEST.sha256** — this phase's initial freeze; the
declaration the gate reads.

## Scope (files)

- `generator/{__init__,models,profiles,generate,response,dims,truth,writer,manifest,cli}.py`,
  `generator/profiles/{tiny,medium}.json`
- `fixtures/tiny/{raw/*.jsonl,dims/dim_user.csv,truth/users.jsonl,truth/prompts.jsonl,MANIFEST.sha256}`
- `scripts/review_gate.py` (`check_fixtures`, `freeze_declarations`),
  `scripts/check_docs.py` (TRACES rows for the new guards)
- `Makefile` (`seed`, `freeze`), `pyproject.toml` + `uv.lock` (pydantic),
  `.gitignore` (`data/` already)
- `tests/{test_generator,test_knobs,test_models,test_dims,test_fixture,test_profiles}.py`,
  `tests/test_truth_isolation.py`, `tests/test_review_tools.py`,
  `tests/test_makefile.py`
- `specs/phase-1-event-contract.md`, `DECISIONS.md`, `BACKLOG.md`,
  `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/PHASES.md`

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — Phase 1 entry: cause-first, shared response function,
      layout/serialization, seed/freeze split, gate freeze guard, JSON profiles
- [ ] `docs/PHASES.md` — Phase 1 Done-when as landed; "Delivered" paragraph
- [ ] `CLAUDE.md` — Current status; Commands (`seed`, `freeze`); Repo map
      (`generator/`, `fixtures/tiny/` real); Event model facts if any column
      moved; BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §2.4 two truth files; §3.3 stub row names the
      per-upload-date landing files; §8 Gotchas for any pydantic / macOS
      surprise
- [ ] `BACKLOG.md` — strike "Phase 0 has no golden fixture" (DONE Phase 1);
      open "dbt raw DDL + sources.yml generated from models" (trigger Phase 2)
- [ ] Spec amendments — none (no later spec exists)
- [ ] RESULTS / METRICS / DEPLOYMENT — none
- [ ] README — none (Phase 13)

## Threat model (REQUIRED when the phase adds a Makefile target that takes a variable, deletes anything, touches cloud resources, or takes user input)

`seed` takes `PROFILE`; `freeze` takes `PROFILE` and `CONFIRM` and overwrites
`fixtures/<p>/` (destructive to a committed golden — the Phase 0 "trusted
origin" invariant applies: `$(call _Q,$(value VAR))`, Python validates
`[a-z0-9_]+`, every path derived from the validated name, one-line recipes,
`$(origin CONFIRM)` = `command line` tested inside the recipe).

| Target | empty | `../x` | `"; ` | env-exported | `$(origin)` on CONFIRM | Pinned by |
|---|---|---|---|---|---|---|
| `make seed PROFILE=` | refused: PROFILE is empty, exit 2 | refused by `[a-z0-9_]+`, exit 2, one line | one literal argv token; refused | reaches the recipe, validated in Python identically | n/a | `tests/test_makefile.py::test_profile_reaches_python_as_one_literal_from_both_origins`, `tests/test_profiles.py::test_profile_name_is_validated` |
| `make freeze PROFILE= CONFIRM=` | refused (PROFILE empty); without CONFIRM prints `freeze: refused — pass CONFIRM=yes on the command line`, exit 2, no write | refused | one literal; refused | PROFILE: same as seed; `CONFIRM=yes` exported from the environment is NOT accepted | recipe tests `$(origin CONFIRM)` = `command line`; anything else refuses before Python runs | `tests/test_makefile.py::test_freeze_requires_confirm_from_the_command_line`, `::test_profile_reaches_python_as_one_literal_from_both_origins` |

Residual (stated): `MAKEFLAGS='CONFIRM=yes'` has command-line origin; the
threat model is "mistakes, not a user who controls the environment". `freeze`
copies into a committed directory; the diff is what gets reviewed, and the
gate's `Freeze:` check is the backstop.

## Review & stack risk

- **code-reviewer** (triggered — code in Scope): determinism (no `datetime.now`,
  no `uuid`, no unsorted `set`/`dict` iteration on an output path), truth
  confinement, envelope exactness, allowlist (pydantic only), the four-file
  generator boundary (§3.1: reads profile + seed only).
- **security-reviewer** (mandatory — `freeze` overwrites a committed
  directory and takes `CONFIRM`): origin gating, path derivation, no
  `shutil.rmtree` on anything not derived from the validated name.
- **functionality-tester** (same trigger): DONE command; `make seed
  PROFILE=tiny` twice; `make seed PROFILE=` / `PROFILE=../x`; `make freeze`
  without CONFIRM; the nine knob tests actually flip on their knob (mutate
  each injector — the ```mutations block).
- **coherence-auditor** at exit (mandatory): §2.4 says two truth files;
  CLAUDE.md Repo map marks `generator/` and `fixtures/tiny/` as real; BACKLOG
  "no golden fixture" struck; PHASES Phase 1 matches the spec as landed.
- Stack risk: pydantic v2 on Python 3.12 (expected fine); `random.Random`
  sequence stability across CPython 3.12.x patch releases is documented
  stable for `random()`/`randrange`/`choices` — verified by the
  two-process test on CI vs local; if CI and macOS differ, STOP and log §8.

## Out of scope (deferred, recorded)

- dbt `sources.yml` / raw DDL generated from the models — Phase 2 (BACKLOG
  row opened here, trigger Phase 2).
- `fixtures/tiny/expected/attribution.csv` — Phase 3.
- Late-arrival split into two landings — the knob lands here; the replay is
  Phase 7.
- dbt SQL mutation operator — BACKLOG (unchanged).
