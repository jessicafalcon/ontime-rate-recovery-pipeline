# Phase 4 — On-time marts and metric definitions (PROPOSED)

Contract for the `phase-4-marts` branch. Source: `docs/PHASES.md` Phase 4.
Depends on Phase 3 merged (PR #5, `38c0f36`).

**Status: PROPOSED — do not start until approved.** No new dependencies:
Phase 4 has no allowlist entry; the marts are dbt SQL, `make report` uses
`duckdb` (Phase 2) and the standard library. A need for any package is a
STOP-and-ask.

## Reconciliation against main (first commit on the branch)

Drift between the plans and what Phase 3 shipped, and the carry-overs due
this phase. Items marked **design change** or **re-freeze** need approval
before any implementation; the rest are facts the spec pins. Numbers are
read off the built tiny (`data/tiny.duckdb` after `make dbt-build
PROFILE=tiny` on `main`).

1. **BACKLOG "The denominator contract's sum must exclude `delivery_fault`"
   — wording, not a design change** (carry-over 1, the row due this phase).
   The five labels partition every `stg_prompts` row (Phase 3 invariant 1),
   and `delivery_fault` is by construction the undelivered prompt (§2.5 rule
   1), so `sum(all five) = prompts_delivered + delivery_fault` on every
   cohort-day and the literal `sum(label counts) == prompts_delivered` in
   CLAUDE.md and the PHASES Done-when is false whenever a delivery fault
   exists (13 of tiny's 14 cohort-days). The identity this phase pins as the
   dbt test is `on_time + upload_fault + timing_gap + unattributed ==
   prompts_delivered` per `(cohort_id, prompt_date)`, with
   `prompts_delivered := count(delivered_in_grace)` — the same predicate rule
   1 negates, so the two cannot drift — and `delivery_fault` counted
   separately (`prompts_sent = prompts_delivered + delivery_fault` is the
   second half of the same test). Why wording: no data structure, write path
   or writer moves — the contract's intent (§2.6 "never user-days, or
   delivery faults vanish"; §4 invariant 6 "the denominator is
   `prompts_delivered`") is unchanged, the sentence just named the wrong sum
   before the table that would have caught it existed. CLAUDE.md's
   "Denominator contract" sentence and the PHASES Done-when are corrected in
   this branch's record updates; DECISIONS gets the one-line why. Rejected:
   reading the sentence as "sum over delivered labels" silently (the next
   reader would write the five-way test and watch it fail).
2. **`docs/METRICS.md` is a new LIVING doc, one definition per metric.**
   `scripts/check_docs.py` treats every `docs/*.md` outside the three plans
   as LIVING (links, anchors and every `make <target>` it names are checked),
   so the file is covered the moment it exists. Each metric gets exactly one
   block — grain, numerator, denominator, null policy, the dbt test that
   pins it — and `dbt/models/marts/schema.yml` descriptions point at the
   METRICS anchor rather than restating (a restatement is a second
   definition that drifts). A `TRACES` token for the partition test's file
   name keeps the doc honest about the test's existence. Fact.
3. **`ontime_retention` is "retained-at-28d" (§2.6) and tiny is 7 days —
   mismatch surfaced; recommendation: keep 28 as the definition, make
   "not observable" a NULL, never a false.** tiny's prompts span
   2026-01-05 → 01-11 local, its last event is 01-13, so no user's 28th day
   is inside the data and a boolean `retained_at_28d` would read `false` for
   all 20 — an all-churned cohort that is an artefact of the window, exactly
   the finding §7 says synthetic data cannot make. The mart is per user
   (grain `user_id`): `anchor_date` = the user's first `prompt_date`,
   `ontime_rate` = the user's on-time share over its delivered prompts in
   the trailing `retention_days` window from the anchor, `observed_through`
   = the data-derived `max(client_event_time_local)` over all events
   (`computed_as_of` per the determinism policy — never the clock),
   `retained` = `true` if an organic `app_opened` (the reachability signal,
   the only event with no `prompt_id`) falls on or after `anchor_date +
   retention_days`, `false` if `observed_through ≥ anchor_date +
   retention_days` and none does, and **NULL when the window has not
   closed**. Var `retention_days: 28`, defaulted in `dbt_project.yml`, named
   here. On tiny every `retained` is NULL and every `ontime_rate` is a
   number; the unit test builds a synthetic 30-day input where one user is
   retained, one is churned and one is unobservable, so the three states are
   proven without the fixture. The doc line (METRICS + schema.yml) is §7's:
   descriptive only, the retention gap in synthetic data is a designed
   property, never a finding. Rejected: `retention_days` defaulted to
   something tiny can close (the definition would bend to the fixture; a
   `medium` run would then compare a 7-day number to the plan's 28); scoping
   the mart to `medium` and stopping (`medium` is not frozen — PHASES Phase
   1 — so the mart would ship with no test data at all). Fact plus one var;
   no design change.
4. **Grain is local: `prompt_date := cast(sent_at_local as date)`** —
   recommendation, one paragraph. Cohorts are defined by the local send hour
   (`c-morning` 08, `c-evening` 20 — `generator/profiles/tiny.json`), so
   "how did this cohort's send time do on day D" is a question about the
   user's day, and the UTC date splits one cohort's morning across two rows:
   every Tokyo `c-morning` prompt (08:00 JST = 23:00 UTC the day before)
   would land on the previous UTC date, and on tiny 34 of 140 prompts have
   `cast(sent_at as date) ≠ cast(sent_at_local as date)` — every
   `c-morning` cohort-day's UTC range straddles two dates. Local is also
   what §2.7 partitions by (`prompt_date`) and what §8's Tokyo gotcha
   already warns about. The cast is ANSI `cast(… as date)`; the denylist
   forbids `::` in a model and `sent_at_local` is already computed once in
   staging via `to_local_time`, so no tz logic is repeated in the mart. Unit
   test `ontime_rate_daily_prompt_date_is_local`: one Tokyo prompt at
   `sent_at = 2026-01-04 23:00 UTC`, `sent_at_local = 2026-01-05 08:00` →
   one row dated `2026-01-05`, none dated `2026-01-04`. Where `prompt_date`
   is computed: in the mart, from the attribution column; Phase 7 moves it
   upstream when partitioning needs it on event-level models (a DECISIONS
   note then, not a column now). Rejected: UTC date (splits the cohort's
   moment; the metric would move when a user changes tz — SCD2 already
   handles that in staging). Fact.
5. **`make report` and the rate reading** — three parts.
   - *What it prints, where.* Console only (§3.1 eval row; `docs/RESULTS.md`
     is Phase 6's): one line `report OK: tiny, 14 cohort-days, 0 differ,
     ontime_rate 0.610 (pin 0.610)` — the golden diff of `ontime_rate_daily`
     against `fixtures/<p>/expected/ontime_rate_daily.csv` plus the overall
     on-time rate `sum(on_time) / sum(prompts_delivered)` over the mart
     (tiny: 75 / 123 = 0.6098), asserted against `tests/pins.py::
     ONTIME_RATE`; exit 1 on any differing row or a rate off the pin. `make
     report PROFILE=<p> WRITE=yes` writes `data/out/<p>/expected/
     ontime_rate_daily.csv` instead, the `attribution-golden` shape.
   - *The golden and its writer* — **re-freeze (needs approval)**. The
     golden is `cohort_id,prompt_date,prompts_sent,prompts_delivered,
     on_time,upload_fault,timing_gap,unattributed,delivery_fault,
     ontime_rate`, canonical CSV sorted by `(cohort_id, prompt_date)` — the
     key is unique, so the tie-break is the key itself. `eval/golden.py` is
     generalised, not copied: `export_rows(db, relation, columns, key)`,
     `render(rows, columns)`, `parse(text, columns)`, `diff_rows(built,
     frozen, key_width)` take the table and column list as arguments; the
     attribution golden becomes one call with its existing constants, byte-
     identical output (`fixtures/tiny/expected/attribution.csv` does not
     move — `tests/test_fixture.py` pins every existing hash). One writer
     path: `WRITE=yes` → `data/out/<p>/expected/` → `make freeze PROFILE=tiny
     CONFIRM=yes`, which adds exactly one manifest line. This spec carries
     `Freeze: fixtures/tiny/MANIFEST.sha256`; the fourteen existing lines
     (raw, dims, truth, `expected/attribution.csv`) are byte-identical before
     and after — a moved hash is a STOP. Rejected: a second golden module
     (two `COLUMNS`, two sort keys, two diff loops to keep in step); a second
     writer of `fixtures/` (Phase 3 decision, still in force).
   - *The rate on a day with nothing delivered* — wording in PHASES, the
     reading pinned here. `ontime_rate := safe_divide(on_time,
     prompts_delivered)`, §2.6's denominator and §4 invariant 6. Its contract
     is NULL on a zero denominator, so a cohort-day with **zero delivered
     prompts** (the literal "delivery-fault-only day") shows `ontime_rate`
     NULL with `prompts_sent`, `delivery_fault` and `prompts_delivered = 0`
     populated — the day is in the table, its faults are counted, and the
     rate is honestly undefined. A cohort-day with **delivered prompts and
     zero on time** shows `0`, not NULL — that is the case the Done-when
     means (a rate that goes missing when everything failed would be the
     "delivery faults vanish" failure §2.6 names, in the other direction).
     `on_time / prompts_sent` is rejected: it changes the denominator §4.6
     fixes and would make a delivery outage look like a timing problem. The
     PHASES clause becomes "a cohort-day with delivered prompts and none on
     time shows on-time rate 0, not null; a cohort-day with nothing delivered
     shows NULL with its counts populated". Neither case exists on tiny
     (every cohort-day has ≥ 6 delivered and ≥ 3 on time), so both are unit
     tests: `ontime_rate_daily_zero_on_time_is_zero` and
     `ontime_rate_daily_nothing_delivered_is_null` — the first test anywhere
     that renders `safe_divide`'s DuckDB body (BACKLOG-style "no caller"
     seam, now called).
6. **Other drift, facts.** (a) CLAUDE.md Repo map says `models/attribution
   (Phase 3; later marts, features, scores)` — `models/marts` lands, the
   line is updated. (b) `tests/test_dbt_conventions.py::
   test_every_model_has_description_and_a_test` iterates `("staging",
   "attribution")` — gains `"marts"`; `SINGULAR` gains the partition test.
   (c) ARCHITECTURE §3's diagram row for marts already matches; §2.6 gains
   the `prompt_date` grain sentence, the NULL policy and the `retention_days`
   var. (d) The struck BACKLOG row "Staging pins are counts only" asked at
   Phase 4 whether a mart reads a staged column no label does:
   `ontime_retention` reads `stg_events.client_event_time_local` for
   `app_opened` (no label reads organic opens) — the retention golden is
   not frozen (item 3: all-NULL on tiny pins nothing), so the answer is one
   pin, `tests/pins.py::ORGANIC_OPEN_ROWS` (count of staged `app_opened`),
   recorded on the row's revisit note rather than a staging row-hash.
   (e) §2.6's "counts per label" is the five counts plus `prompts_sent` and
   `prompts_delivered` — ten columns, listed in item 5.

Items 5 (the re-freeze adding `expected/ontime_rate_daily.csv`) is the one
approval gate; items 1 and 5's rate reading rewrite record sentences. STOP
here for approval; the spec body (Invariants, Evidence, Pinned decisions,
Threat model) follows in the next commit.
