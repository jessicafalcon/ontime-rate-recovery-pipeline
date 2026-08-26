# Phase 3 — Attribution ⭐ checkpoint (PROPOSED)

Contract for the `phase-3-attribution` branch. Source: `docs/PHASES.md`
Phase 3. Depends on Phase 2 merged (PR #4, `07dfb51`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
Phase 3 has no allowlist entry; `eval/` uses `duckdb` (Phase 2) and the
standard library only. A need for any package is a STOP-and-ask.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 2 shipped, and the five carry-overs
due this phase. Items marked **design change** need approval before any
implementation; the rest are facts the spec pins.

1. **Skew is a gate on the clock evidence, evaluated before rules 2–4** —
   *design change* (ARCHITECTURE §2.5 wording). §2.5 lists `unattributed`
   fifth, "everything else (skew beyond bound, …)". But the skew injector
   shifts only the CLIENT-side events of a prompt (`prompt_opened`,
   `capture_started`, `upload_*`; `generator/generate.py::CLIENT_SIDE`);
   `prompt_delivered` and `response_recorded` are server-stamped and keep
   `client_event_time = server_received_time`. So every one of tiny's six
   skewed prompts carries a `response_recorded` whose two clocks are both
   inside the window — rule 2 matches first and labels it `on_time`, and the
   "first matching rule" reading of §2.5 is wrong by construction. Now: rule
   order is `delivery_fault` → **skew** (`min(upload_delay_seconds)` over the
   prompt's events `< −SKEW_MAX_MIN·60` → `unattributed`) → `on_time` →
   `upload_fault` → `timing_gap` → `unattributed` (residual). `delivery_fault`
   stays first because the receipt is server-stamped and skew cannot forge it.
   Invariant restored: §2.1 "skew beyond ±`SKEW_MAX_MIN` is `unattributed`,
   never guessed" — a rule that reads a skewed clock is a guess. §2.5's list
   gets the skew gate as its own line between 1 and 2, and the §8 gotcha's
   "Phase 3 pins the rule on the negative side" becomes the rule: only
   `upload_delay_seconds < −SKEW_MAX_MIN·60` is skew; a positive delay of any
   size is an upload delay and never `unattributed` on its own. Rejected:
   keeping `unattributed` last and excluding skewed prompts from rules 2–4
   by a predicate in each arm (four copies of the bound, and a survivor
   hides in whichever copy is dropped).
2. **Which delay is compared to the bound.** `stg_events.upload_delay_seconds`
   is per event; the label is per prompt. The prompt's skew signal is the
   minimum delay over its events (`prompt_id` join), so one skewed
   `capture_started` is enough. Events with no `prompt_id` (`app_opened`)
   never enter attribution. Fact, pinned by the skew unit test.
3. **BACKLOG "Mutation sweep has no operator for dbt SQL" — build it** (trigger
   arrived twice; carry-over 1). Two operators, SQL-text only, in
   `scripts/mutate.py`, line shape `dbt/models/attribution/attribution.sql::label
   <op>`: `drop-arm:<n>` deletes the n-th `when … then …` arm of the named
   `case` (the column alias after `end as`), `swap-arms:<i>,<j>` exchanges two
   arms. Killed the same way Python mutations are: the worktree suite runs
   `tests/test_staging.py`'s in-process `dbt build`, so a dbt unit test going
   red is a KILLED line. Each precedence arm gets one `drop-arm` line (its
   own unit test kills it) and each adjacent pair one `swap-arms` line (the
   overlap unit test kills it). Python otherwise unchanged; the `mutations`
   block stays one block. Rejected: re-deferring again (the five-arm `case`
   was the named trigger); a generic `swap-predicate` (which predicate to
   swap is undecidable without parsing SQL — arms are the unit the tests are
   written against).
4. **BACKLOG "`stg_prompts` carries two cohort keys" — the denominator key is
   `prompt_cohort_id`** (carry-over 2). Recommendation: attribution exposes
   `cohort_id := stg_prompts.prompt_cohort_id`, the cohort the notification
   service sent the prompt AS. Phase 4's rate is "how did this cohort's send
   time perform", so the cohort that chose the send hour is the grouping
   key; `dim_user.cohort_id` is the user's assignment at `client_event_time`
   and would move a prompt between cohorts on a later reassignment, changing
   a `final` label's denominator row. Today the two agree on every tiny row;
   a singular test `assert_prompt_cohort_matches_dim` (over `stg_prompts`)
   pins that and turns the first divergence into a red build rather than a
   silent choice. Rejected: `dim_user.cohort_id` (SCD2 on tz only — a cohort
   change today is a new row with no history; Phase 10's Spanner dim may add
   one, and the denominator must not depend on it).
5. **BACKLOG "Staging pins are counts only" — `fixtures/tiny/expected/attribution.csv`
   is the content pin** (carry-over 3) — *design change* (a new writer path).
   `make freeze` is the only writer of `fixtures/`, and it copies
   `data/out/<p>/` whole. So the golden is produced under `data/out/<p>/
   expected/attribution.csv` by `make attribution-golden PROFILE=<p>
   WRITE=yes` (canonical CSV: header, sorted by `prompt_id`, `\n`, no
   quoting needed — ids and labels are `[a-z0-9_-]+`) and reaches
   `fixtures/` only through `make freeze PROFILE=tiny CONFIRM=yes`, which
   re-renders the manifest with the new file. Without `WRITE`, the target
   diffs the built table against `fixtures/<p>/expected/attribution.csv` and
   exits 1 on any differing row (the DONE half). Two consequences the code
   must pin: (a) `make seed`'s self-check compares only the keys the
   generator wrote (`raw/`, `dims/`, `truth/`) — with `expected/` in the
   manifest the current whole-manifest diff would report it `missing` on
   every seed; (b) `freeze` refuses when `data/out/<p>/` lacks a file the
   current manifest lists (a freeze after a bare `seed` would silently drop
   `expected/`). This spec carries `Freeze: fixtures/tiny/MANIFEST.sha256`;
   the raw/dims/truth lines of the manifest are byte-identical before and
   after (a moved hash is a STOP), and `loader/load.py::manifest_drift`
   keeps hashing `raw/` + `dims/` only. Invariant restored: "did it work is a
   diff against a frozen file", with one writer of `fixtures/`. Rejected: a
   second writer that puts `expected/` under `fixtures/` directly (the
   Phase 1 rule exists so no tool can repair its own golden); keeping
   `expected/` out of the manifest (the gate's "fixture file changed without
   its manifest" check would then never see it).
6. **`eval/` is a new top-level package** (carry-over 4). Already in
   `test_truth_isolation.py::EXEMPT` and the Repo map. It reads the built
   DuckDB file and `fixtures/<p>/truth/prompts.jsonl`, writes console only
   (no `docs/RESULTS.md` block until Phase 6). Nothing under `dbt/` may
   `ref`/`source` anything it writes — it writes no table. The golden
   export (item 5) lives in `eval/` too: it reads dbt output and writes
   `data/out/`, which §3.1 allows (eval may not write a table the pipeline
   READS; `expected/` is read by nothing in `dbt/`). §3.1's eval row gains
   "`data/out/<p>/expected/`" in its writes column. Fact, no design change:
   the boundary is unchanged, the row is completed.
7. **The three vars** (carry-over 5): `skew_max_min: 5` (equal to
   `generator/models.py::SKEW_MAX_MIN`; a test asserts equality so the two
   pins cannot drift), `delivery_grace_min: 10` (the generator delivers 5–120
   s after send; a receipt later than grace is a delivery fault even though
   `stg_prompts.delivered_at` is populated), `unattributed_max: 0.10` (tiny
   is 6/140 = 0.043; the profile's `clock_skew_rate` is 0.05). All three
   defaulted in `dbt_project.yml`, lowercase as dbt vars, named here. The
   skew rule is negative-side only (item 1).
8. **PHASES Phase 3 says "`eval` scores labels vs truth" but not where the
   pin lives.** `tests/pins.py::LABEL_ACCURACY` and the per-label truth
   counts (75/34/17/8/6) are the pins; `eval` prints and asserts the same
   numbers. Fact.
9. **`provisional`/`final` status is Phase 7.** §2.5's last paragraph
   describes it; this phase's `attribution` is a full-rebuild table with no
   status column. Recorded as out of scope; no clause moves.
10. **ARCHITECTURE §3 diagram row "attribution — exhaustive label per
    prompt×user"** — the grain is `prompt_id` (one user per prompt,
    `stg_prompts` unique on `prompt_id`); the row stays, the spec says
    "prompt×user = prompt_id" once. Fact.

Items 1 and 5 are the design changes. STOP here for approval; the spec body
(Invariants, Evidence, Pinned decisions, Threat model) follows in the next
commit.
