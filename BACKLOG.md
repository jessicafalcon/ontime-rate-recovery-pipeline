# BACKLOG.md — deferred findings and revisits

Items accepted "for now" with a concrete revisit trigger. Reviewed at every
phase exit (alongside the coherence audit); an item whose trigger has arrived
is either done in that phase or re-deferred here with a new trigger — never
silently dropped. Cite rows by TITLE (bold text); line numbers shift. A closed
row is struck through with "DONE Phase N", never deleted.

| Item | Source | Trigger |
|---|---|---|
| **Mutation sweep has no operator for dbt SQL** — an invariant upheld only in SQL is pinned by a dbt unit/data test named in the spec's Invariants table, not by `make mutate`. Candidate: a `swap-predicate` / `drop-where` operator over a model file, run through `dbt build` in the worktree. | Phase 0 | Phase 2 (staging dedupe is the first SQL-only invariant) |
| **Phase 0 has no golden fixture** — `make test` runs offline but the frozen `fixtures/tiny/` that turns correctness into a diff is Phase 1's deliverable. Until it lands, the review gate proves tooling, not pipeline. | Phase 0 | Phase 1 (resolved when `MANIFEST.sha256` is committed) |
| **Spanner 90-day trial expiry bills ~$65/mo after** — `enable_spanner` toggle + a teardown date in `docs/DEPLOYMENT.md`. | Architecture review 2026-08-24 | Phase 10 apply day; re-check at every phase exit after |
| **Budget alerts do not stop spend** — optional Pub/Sub → Cloud Function that disables billing at $150 is the real guardrail; documented as optional in Phase 9, built only if the author wants it. | Architecture review 2026-08-24 | Phase 9 |
| **Cross-warehouse dialect drift is caught only on DuckDB in CI** — BigQuery runs are manual. A scheduled or on-demand BigQuery CI job needs WIF; deferred until Phase 9 proves the four macros by hand. | Phase 0 | Phase 9 exit |
