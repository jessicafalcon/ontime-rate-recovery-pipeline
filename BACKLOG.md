# BACKLOG.md — deferred findings and revisits

Items accepted "for now" with a concrete revisit trigger. Reviewed at every
phase exit (alongside the coherence audit); an item whose trigger has arrived
is either done in that phase or re-deferred here with a new trigger — never
silently dropped. Cite rows by TITLE (bold text); line numbers shift. A closed
row is struck through with "DONE Phase N", never deleted.

| Item | Source | Trigger |
|---|---|---|
| **Mutation sweep has no operator for dbt SQL** — an invariant upheld only in SQL is pinned by a dbt unit/data test named in the spec's Invariants table, not by `make mutate`. Candidate: a `swap-predicate` / `drop-where` operator over a model file, run through `dbt build` in the worktree. | Phase 0 | Phase 2 (staging dedupe is the first SQL-only invariant) |
| ~~**Phase 0 has no golden fixture** — `make test` runs offline but the frozen `fixtures/tiny/` that turns correctness into a diff is Phase 1's deliverable. Until it lands, the review gate proves tooling, not pipeline.~~ **DONE Phase 1** — `fixtures/tiny/MANIFEST.sha256` committed; gate check f. | Phase 0 | Phase 1 (resolved when `MANIFEST.sha256` is committed) |
| **Raw DDL and dbt `sources.yml` are not yet generated from `generator/models.py`** — the schema contract says generated-never-hand-edited; the generator side exists, the consumer does not. | Phase 1 | Phase 2 (first dbt source) |
| **Spanner 90-day trial expiry bills ~$65/mo after** — `enable_spanner` toggle + a teardown date in `docs/DEPLOYMENT.md`. | Architecture review 2026-08-24 | Phase 10 apply day; re-check at every phase exit after |
| **Budget alerts do not stop spend** — optional Pub/Sub → Cloud Function that disables billing at $150 is the real guardrail; documented as optional in Phase 9, built only if the author wants it. | Architecture review 2026-08-24 | Phase 9 |
| ~~**CI green on the Phase 0 PR is unverified until first push** — Done-when 6 / Evidence row 6; the workflow has never run (branch unpushed).~~ **DONE Phase 0** — `ci / lint-test` green on PR #1 (run 32880728851). | Review round 1 | First push of `phase-0-skeleton`: confirm `ci / lint-test` green, then strike |
| **Cross-warehouse dialect drift is caught only on DuckDB in CI** — BigQuery runs are manual. A scheduled or on-demand BigQuery CI job needs WIF; deferred until Phase 9 proves the four macros by hand. | Phase 0 | Phase 9 exit |
| **Round tags are phase-agnostic (`review-round-N`), so each phase's tags collide with the prior phase's leftovers** — Phase 0's `review-round-1`/`-2` tripped Phase 1's round-1 collision check; deleted by hand to proceed. Fix on its own `fix/round-tag-phase-reset` branch (Phase 0 tooling, kept out of the Phase 1 PR): recommended a `round_tag.py reset` subcommand deleting local `review-round-*`, run at phase start (+ `make round-reset`, a CLAUDE.md phase-start note); alternative is phase-scoped tag names. | Review round 1 | Phase 2 review round 1, or when the fix branch lands (whichever first) |
