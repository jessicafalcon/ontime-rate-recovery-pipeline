# DECISIONS.md — why-not-X log

One entry per non-obvious choice. **"Decisions still in force"** (first) is the
binding set — ≤ 20 entries, by component, each linking to the phase entry that
argued it. **"Process"** records how the phases are run. **"Appendix — by
phase"** is the full log, oldest first; an entry a later phase reverses is
annotated **Superseded by …** in place and never deleted.

## Decisions still in force

**Data & contract**

- **The generator emits the Amplitude raw-export shape, and the three clocks are
  the reliability signal.** `client_event_time` vs `server_received_time`
  disagreeing about the window *is* the upload fault; no heuristic. The stub and
  the production export are interchangeable at the dbt source. ([Phase 0](#phase-0))
- **Five labels, exclusive and exhaustive, with a tested bound on
  `unattributed`.** `delivery_fault` is its own cause (the prompt never
  arrived); `unattributed` is explicit, never folded into "late". ([Phase 0](#phase-0))
- **Truth never enters the pipeline.** Side-file under `truth/`; only `eval/`
  reads it; a test greps every pipeline directory for the word. ([Phase 0](#phase-0))

**Model**

- **The send-time model is a dbt model, cohort-constrained, on organic opens.**
  Per-user send times dissolve the product's shared moment, so the cohort band is
  the unit; prompt responses are exposure-biased, so organic `app_opened` is the
  signal; SQL keeps it versioned, unit-tested, and warehouse-portable.
  ([Phase 0](#phase-0))

**Validation**

- **Counterfactual simulation, not "offline A/B".** Re-simulating outcomes from
  the latent that generated the data is not an experiment; it is named as a
  simulation, and the production A/B is shipped as a spec. ([Phase 0](#phase-0))

**Infra**

- **Local-first, DuckDB for CI, BigQuery by profile switch; Composer and Spanner
  behind Terraform toggles.** Cloud runs are manual and asked-for; nothing
  billable is left up by default. ([Phase 0](#phase-0))

## Process

- **Workflow machinery established before any pipeline code (2026-08-24).**
  Three load-bearing mechanisms: a frozen spec layer (`specs/TEMPLATE.md`
  with Invariants / Evidence / Record updates / Threat model and one DONE
  command); phase = branch = PR = review gate (`/review-round`, STOP-on-findings,
  the two-round cap, the developer merges); determinism + golden fixtures so
  "did it work" is a command. The offline gates (`review_gate.py`, `mutate.py`,
  `check_docs.py`, `round_tag.py`, `review_common.py`) carry their hardening from
  day one (unexpanded `$(value)` + `_Q` quoting, `unexport`, spec path
  validation, literal-only `constant-return`, worktree-registry check) rather
  than earning it incident by incident. `check_docs.py` starts with three tooling
  traces in `TRACES`, extended as phases name guards.
- **Phases re-cut by verifiable capability, not by layer.** The brief's
  original Phase 0 was the generator and its Phase 1 ("ingestion & staging")
  mixed contract, loader and dbt; tooling is now Phase 0, the generator + frozen
  fixture Phase 1, staging Phase 2, so every later phase is a diff against the
  fixture. PROJECT_BRIEF.md §6 was renumbered to match (one numbering
  everywhere; `docs/PHASES.md` authoritative). The core risk (attribution recovers assigned causes; organic
  opens recover the latent window) is proven in Phases 1–5 before any cloud.
- **Mutation sweep covers Python only.** dbt SQL has no operator; an invariant
  upheld only in SQL names its dbt unit test in the Invariants table. BACKLOG
  row with trigger "Phase 2 (staging dedupe is the first SQL-only invariant)".
- **Review agents are selected by diff surface, not run wholesale.** A
  docs-only range gets the coherence-auditor (scoped) and the gate; code gets
  code-reviewer + functionality-tester; sensitive paths add security-reviewer.
  Table in CLAUDE.md; `/review-round` classifies `git diff --name-only` and
  prints the list before spawning. Why: running code-reviewer on prose
  produces noise findings and burns a round; the classification is a lookup so
  it cannot drift by judgment.
- **The run-tests hook is wired locally, not committed.** A committed
  `settings.json` would auto-execute an inbound branch's hook + conftest for
  anyone opening the repo in Claude Code.

## Appendix — by phase

### Phase 1

*Event contract, generator, frozen tiny fixture (`phase-1-event-contract`).*

- **`timing_gap` is delivery + no-action evidence alone (spec reconciliation,
  2026-08-25).** ARCHITECTURE §2.5 rule 4 used to require that "the user's
  organic activity says the window was outside their reachable hours" — that
  clause makes Phase 3 attribution read the Phase 5 reachability features, a
  layering inversion: the label would depend on a model downstream of it, and
  the generator, which assigns the true cause in this phase, would have to
  encode a reachability judgment into truth. Now: `timing_gap` = delivered in
  grace, no `capture_started`, no `response_recorded`, no upload chain; the
  former "delivered-and-reachable-but-no-action" case in rule 5 is absorbed
  by it. Phase 5 scores reachability separately from organic opens and reports
  how much of the timing-gap share it can move; it never feeds the label.
  Rejected: keeping the clause and computing a Phase 3 reachability stub
  (two definitions of reachability, one of them unversioned).
- **Cause-first generation.** The generator draws each prompt's cause, then
  emits the events that cause implies; injectors (duplicate, late arrival,
  skew) run after and never change it. Truth is exact by construction. Rejected:
  a behavioural model labelled afterwards — a second attribution to keep in sync.
- **One response function, shared with Phase 6.** `generator/response.py::
  responds` is pure and caller-seeded; `eval/simulate.py` imports it. Rejected:
  inlining the draw (Phase 6 would re-implement it).
- **`seed` writes `data/out/<p>/` only and self-checks against the manifest;
  `freeze` is the only writer of `fixtures/`.** A `seed` that wrote into
  `fixtures/` could repair its own golden and the DONE command would prove
  nothing. `freeze` takes `CONFIRM=yes` with command-line origin — the first
  live use of the Phase 0 `$(origin)` rule.
- **The freeze guard is a review-gate check, not a TRACES row.** `check_docs`
  tests token presence; read-only-ness is a diff property: any `fixtures/**`
  path in `git diff BASE...HEAD` FAILs unless the spec declares
  `Freeze: fixtures/<p>/MANIFEST.sha256`, and a fixture file changed without its
  manifest FAILs regardless.
- **Profiles are JSON, every knob required.** A missing knob is a validation
  error, never a silent default; `tiny` and `medium` are data, not code.
  Rejected: Python-module profiles (executable config), YAML (a package).
- **Layout.** `raw/events_<upload-date>.jsonl` (one file per UTC
  `server_upload_time` date — Phase 7's landing unit), `dims/dim_user.csv`,
  `truth/{users,prompts}.jsonl`; canonical JSON (`sort_keys`, no spaces),
  Amplitude timestamp strings, whole seconds, counter ids, `SIM_START` =
  2026-01-05 (January: no DST transition in any profile tz). Rejected: one
  `events.jsonl` (Phase 7 would split it).
- **`cli.py` joins `truth.py`/`models.py` as a file that may name truth.** The
  rule's property is "generation logic never names the side-file"; something
  has to call the writer, and the entry point is the least-logic place. The
  record types are `LatentUser` / `PromptCause` so `generate.py` and
  `response.py` stay clean. Rejected: exempting all of `generator/` (vacates
  the guard).
- **Skew injector is forward-only** (client ahead). Backward skew is
  observationally an upload delay (§8 Gotchas); the generator only produces
  what attribution can in principle detect, so truth stays scoreable.
- **Organic opens are drawn around the latent centre** (`organic_opens_per_day`
  knob, Gaussian with σ = width/2). Phase 5's signal has to exist in the
  fixture; the knob is named here because PHASES did not list it.
- **Single-tz fallback emits one row, and the fix keeps the RNG stream (review
  round 1, 2026-08-25).** `build_dims` used `others = [...] or tzs`, so a
  one-entry `tz_mix` with `tz_change_rate > 0` produced two contiguous rows with
  the *same* tz — a no-op SCD2 "change" that falsifies invariant 8. The `rng.random()`
  draw still fires whenever `days > 1` (only `rng.choice`/`rng.randint` are now
  skipped when there is no other tz), so multi-tz profiles consume the stream
  identically and `tiny`/`medium` re-freeze byte-for-byte (`seed OK … manifest
  match`). Rejected: short-circuiting before the `rng.random()` draw (would shift
  every later user's tz and drift the fixtures).
- **`dim_user` draws from its own seed-derived stream (`Random(profile.seed *
  7919 + 1)`), not the event `rng` (phase-exit audit, 2026-08-25).** Dims (tz
  assignment, the tz-change draw, signup dates) must not perturb the event
  stream's bytes, nor the reverse: with a separate `Random`, adding or removing
  an event-side draw never reshuffles `dim_user`, and each concern stays an
  independent, reproducible function of `profile.seed`. The `* 7919 + 1` prime
  offset decorrelates the two streams from the one seed. Rejected: threading the
  event `rng` into `build_dims` (an added event draw would move every user's tz).
- **`cli.py::seed`/`freeze` mutations are `constant-return:0`, not `delete-call`
  (review round 1).** `delete-call` removes statement-level calls *to* the named
  function, and `freeze` is only ever `return freeze(...)` in `main` (a value, not
  a statement), so `freeze delete-call` finds no hit and errors. `constant-return:0`
  keeps each function's normal success return while skipping every side effect —
  the exact "the drift-exit / write path never ran" mutation — killed by the two
  new `test_generator.py` tests.

### Phase 0

*Skeleton and workflow machinery (`phase-0-skeleton`).*

- **Python 3.12 pinned, not the system 3.14.** dbt-core / dbt-duckdb wheels and
  Airflow's constraint files lag the newest interpreter; 3.12 is the version
  every phase's allowlist supports today. Revisit when dbt publishes 3.14 wheels.
- **No runtime dependency in Phase 0.** `pyproject.toml` carries only the dev
  group; every runtime package lands in the phase that needs it (allowlist in
  CLAUDE.md) so `uv.lock` diffs stay attributable.
- **`OTR_INT` is the integration marker env**: the reduced child
  env passes it, `conftest.py` skips `tests/integration/` unless it is `1`. No
  integration suite exists yet; the plumbing is kept so Phase 8 (Airflow) and
  Phase 9 (BigQuery) add tests without touching the gate.
- **`check_docs.py` keeps the four-check shape from day one.** Link/anchor,
  make-target, trace (three tooling rows) and BACKLOG-count checks all run;
  TRACES grows as phases name guards. Why not drop it: the BACKLOG-count sentence is
  the one two branches always rewrite.
- **`unexport` is hygiene, not a guard (review round 1).** All three review
  agents falsified the threat-model cell "env value never reaches the recipe":
  make imports environment variables into its table regardless. The real
  guard is `$(call _Q,$(value VAR))` (unexpanded, single-quoted — no shell, no
  make function, from either origin) plus Python validation; the only way to
  tell command line from environment is `$(origin VAR)`, which every future
  `CONFIRM` knob must test inside its one-line recipe. Pinned by
  `tests/test_makefile.py` running `make -n` under both origins.
- **The sweep runs a baseline first.** A red HEAD used to print `KILLED` for
  every mutation and `mutate OK`; now the unmutated suite must be green in the
  worktree or the sweep refuses. Rejected: trusting `make test` from the gate —
  `make mutate` is also run standalone.
- **`PIPELINE_DIRS` is derived from the tree.** Every top-level package not in
  an explicit exemption set (`tests`, `scripts`, `eval`, `generator`, docs,
  specs, fixtures, infra, data, dotdirs) is a pipeline directory; a new package is
  guarded the day it appears, and a positive-control test proves the grep
  finds a planted reference. Rejected: a hand-maintained list (vacuous on day
  one, forgotten later).
- **Every docs-guard check has a negative pin.** Round 1 showed all four could
  be disabled without a red test; each now has a tmp-tree test and a mutation
  line. The `make <target>` regex requires backticks, like the gate's.
- **CI hardening.** `permissions: contents: read`, `persist-credentials:
  false`, pre-commit `rev` pinned to the tag's commit SHA, a concurrency group.
  The run-tests hook runs pytest under the same reduced env as the sweep.
- **Plans are link-checked only.** `check_docs.py` scans CLAUDE.md, README and
  the living docs for `make <target>` existence, but ARCHITECTURE.md, PHASES.md
  and PROJECT_BRIEF.md name targets not built yet by design. First thing the
  gate caught on itself (the plans' future targets); the alternative — a `(Phase N)`
  marker syntax the scanner parses — was rejected as a second grammar.
- **PROJECT_BRIEF.md stays at the repo root as the origin record**; it is not a
  living doc (ARCHITECTURE/PHASES supersede it) and is link-checked only.
