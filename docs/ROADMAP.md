# ROADMAP.md — after Phase 13

Phases 0–13 closed the project on correctness ([PHASES.md](PHASES.md)). This
file records the ordered plan that follows, decided 2026-09-01 against a
staff-data-engineer read of the repo (`docs/INSIGHT.md` states what is proven;
this states what is next and why). Every item is its own `fix/<slug>` branch
from `main`, one PR each, the same gate as a phase. Items that were already
[BACKLOG.md](../BACKLOG.md) rows are cited by row title; the four that were not
got a row in the same commit as this file; a roadmap row in BACKLOG carries
only the title, a pointer here and the trigger — the detail lives in this file
alone, so the two cannot drift. This is a living doc for `check-docs` (every
`make` target it names must exist), except a target named before its branch
builds it, admitted by name from the exact `FUTURE_TARGETS` set in
`scripts/check_docs.py` and removed there when it lands. BACKLOG is reviewed
at every `fix/` branch exit now that phases are over.

## The gap the plan closes

The repo proves correctness at 2,000 users. It does not yet show scale, a
scheduled cloud run, or a non-circular offline evaluation. The plan is ordered
by application value per day, cheapest first; items 1–4 need no cloud spend
beyond cents.

## The ordered plan

| # | Branch | What changes | Proof | Size | Spend |
|---|---|---|---|---|---|
| 1 | `fix/front-door` (1a, landed 2026-09-01: the README retold as a story — problem, what it does, what it found with a generated headline, why to trust it — no phase language) → `fix/process-doc` (1b, landed 2026-09-01: `docs/PROCESS.md`, the INSIGHT closing pass, and `tests/test_insight.py` pinning the essay's typed figures to `tests/pins.py`) | Reframe, not hide: strip phase/amendment language from README (1a) and INSIGHT prose (1b); add `docs/PROCESS.md` (1b) — one page on the AI-assisted spec-and-review loop and what kept it honest (pins, mutation sweep, truth-isolation grep, frozen fixture) | `make check-docs`; coherence-auditor over the changed docs | 1 day | none |
| 2 | `fix/tf-remote-state` (landed 2026-09-01) | Bootstrap the state bucket once by hand (a bucket cannot create its own backend), uncomment the drafted `gcs` backend in `infra/main.tf` (as a PARTIAL config — the bucket is a `-backend-config` from the validated `PROJECT`, no id in the `.tf`), then migrate state through a NEW gated target `make tf-migrate-state PROJECT=<id> CONFIRM=yes` (`infra/cli.py` runs `init -migrate-state` under the same `ENV_ALLOW` / cloud-env / no-`TF_VAR_*` gates as every `tf-*` — never a hand-run terraform outside the credential standard), then `tf-freeze` | `make tf-plan` after the migration reads the remote state (`tf-validate` inits with `-backend=false`, so it proves nothing here); the gate tests over the new target | 1 day | cents |
| 3 | `fix/scores-dim-current` (landed 2026-09-02) | `scores_send_time` reads `ref('dim_user_current')` for the open dim row instead of the raw source | the three goldens unchanged byte-for-byte (`make scores-golden`, `make report`, `make attribution-golden`); the federation seam still resolves (`test-int-spanner` asserts the manifest resolved `raw.dim_user` to the view, now consumed through `dim_user_current`) — an optional ask-first parity run, not required for the one-week cut | ½ day | none |
| 4 | `fix/holdout-eval` | Temporal holdout: build medium with the landing cut at an UPLOAD date (`THROUGH` selects files by upload date, not simulation day; medium carries 72 h late arrivals, so the held-out set is exactly the events uploaded after the cut), serve, then a new `eval/cli.py holdout` command reads the RAW organic opens uploaded after the cut (raw, never truth — no reachable-window or centre vocabulary, those are truth concepts) and scores served-hour vs cohort send-hour on them: share inside a fixed window around the served hour, circular distance to the nearest open; pinned in `tests/pins.py`, a generated block in `docs/RESULTS.md` beside the simulation | the new pin under `make test`; check-mode block diff | 2 days | none |
| 5 | `fix/large-profile` (landed 2026-09-02) | A `large` profile (200,000 users × 30 days ≈ 35 M events ≈ 10 GB); the generator sharded by a derived `(SEED, shard)` seed, emit order preserved within a shard — an amendment to the generator's one-`Random` invariant (CLAUDE.md Repo map; determinism policy), committed alone first; `tiny` and `medium` must reproduce byte-for-byte at shard count 1 (the manifest match is the proof); ask-first: one full `TARGET=bigquery` build and one `THROUGH` incremental build; RESULTS.md records bytes scanned per model, slot-ms, wall clock, dollars, and the pruning proof. **Delivered:** 200 shards, byte-identical `tiny`/`medium` at shard 1, DuckDB goldens untouched; full build 18.33 GB / ≈ $0.11, and the measured item-6 case — unpartitioned raw means the incremental re-run does not prune the source scan (`docs/RESULTS.md`). | the recorded table; DuckDB goldens untouched | 3–4 days | single-digit $ |
| 6 | `fix/append-landing` | Raw becomes append-only, partitioned by upload date; each export file loads once into its partition; `THROUGH` becomes a partition predicate; the writer emits gzipped hourly files like the real Amplitude export. A write-path change AND a re-freeze (gzipped files change `fixtures/tiny/raw`, which the review gate admits only with a spec's `Freeze:` line): a spec from `specs/TEMPLATE.md` with a DECISIONS entry, not a bare amendment | `make test-int-bigquery` byte parity; a re-land writes 0 new rows | 3 days | cents |
| 7 | `fix/composer-cosmos` | `astronomer-cosmos` in the Composer image only (never `uv.lock`): `DbtDag` renders every model as a task; the write-back runs as a `KubernetesPodOperator` over a small `serving/` + `landing/` image; `dbt source freshness` first, an `on_failure_callback` that emails; one green scheduled run on real BigQuery + Spanner, then the toggle-flip teardown the same session | the dated run + teardown lines in `docs/DEPLOYMENT.md` | 4–5 days | ≈ $30 |
| 8 | `fix/ci-bigquery-parity` | `enable_ci_wif=true` + `github_repository=<owner/repo>` apply (no default repo is trusted); a `workflow_dispatch` job running the existing `test-int-bigquery`. No date constraint: the SA-id soft-delete window that once dated this closed at Phase 9b (the SA was imported) | a green dispatched run | 1 day | cents |

BACKLOG rows this plan cites: **Terraform state is a local, unversioned
`infra/terraform.tfstate`** (item 2), **No real-scale cost/performance numbers
are published** (item 5), **The make-based DAG cannot run on Composer (Option A leaves the scheduled cloud run unproven)** (item 7),
**Cross-warehouse dialect drift is caught only on DuckDB in CI** (item 8). New
rows for items 1, 3, 4 and 6.

## The one-week cut

Decided 2026-09-01: build **items 1, 2, 3 and 5** first, in that order (item 1
landed as two PRs, 1a the README and 1b the process page — the one exception to
"one PR each", recorded in DECISIONS). The
reframed front door plus a real cost table changes the application more than
anything else. Item 4 is next if a second week appears, then 7, then 6 and 8.

The repository went public on 2026-09-01, after `fix/public-release` (the
redaction half — every record names `<project_id>` / `<operator>`, pinned by
`make check-docs`) and `fix/front-door` (1a), and BEFORE item 2 by the developer's
decision: the local state file was never in any ref, so the flip exposed nothing.
Item 2 keeps its place in the cut; the BACKLOG row **Terraform state is a local,
unversioned `infra/terraform.tfstate`** now dates it to the next `tf-apply`
session. The flip-day steps are recorded on the row **The public-repo GitHub-side
settings are outside the tree**.

## Why this order

- Item 1 before anything: every later PR's README text should already read as
  a product, not a phase log.
- Item 2 before item 5: the first apply that stays up between sessions needs
  versioned state (the BACKLOG trigger).
- Item 3 before item 5: the large build should exercise the layering the
  write-back already uses.
- Item 5 before item 6: the large build is the workload that shows why the
  landing must be append-only; the amendment is written against measured
  numbers.
- Item 7 last of the big ones: it depends on the image item 6 shapes and is
  the only item that bills by the hour.

## Approvals recorded

- Item 4 is a feature outside `docs/ARCHITECTURE.md`'s scope; its branch opens
  with a one-paragraph amendment committed alone — STOP for approval before
  implementing (CLAUDE.md, Fix amendments). Nothing here pre-approves it; the
  same holds for item 5's generator amendment and item 6's spec.
- Item 7 adds a package (`astronomer-cosmos`, Composer image only); ask-first
  at the branch, like every dependency.
- Items 5, 7 and 8 are cloud-cost: ask-first per step, with a budget cap named
  in the ask.
