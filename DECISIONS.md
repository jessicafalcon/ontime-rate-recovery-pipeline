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
  than earning it incident by incident. `check_docs.py` starts with an empty
  `TRACES` list, filled as phases name guards.
- **Phases re-cut by verifiable capability, not by layer.** The brief's Phase 1
  ("ingestion & staging") mixed contract, generator, loader and dbt; it is now
  Phases 1–2 with the frozen fixture landing first, so every later phase is a
  diff against it. The core risk (attribution recovers assigned causes; organic
  opens recover the latent window) is proven in Phases 1–5 before any cloud.
- **Mutation sweep covers Python only.** dbt SQL has no operator; an invariant
  upheld only in SQL names its dbt unit test in the Invariants table. BACKLOG
  row with trigger "Phase 3 lands the first SQL-only invariant".
- **The run-tests hook is wired locally, not committed.** A committed
  `settings.json` would auto-execute an inbound branch's hook + conftest for
  anyone opening the repo in Claude Code.

## Appendix — by phase

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
- **`check_docs.py` keeps the four-check shape with an empty trace list.** The
  link/anchor, make-target, and BACKLOG-count checks run from day one; TRACES
  fills as phases name guards. Why not drop it: the BACKLOG-count sentence is
  the one two branches always rewrite.
- **Plans are link-checked only.** `check_docs.py` scans CLAUDE.md, README and
  the living docs for `make <target>` existence, but ARCHITECTURE.md, PHASES.md
  and PROJECT_BRIEF.md name targets not built yet by design. First thing the
  gate caught on itself (eight future targets); the alternative — a `(Phase N)`
  marker syntax the scanner parses — was rejected as a second grammar.
- **PROJECT_BRIEF.md stays at the repo root as the origin record**; it is not a
  living doc (ARCHITECTURE/PHASES supersede it) and is link-checked only.
