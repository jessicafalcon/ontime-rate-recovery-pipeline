# METRICS.md — the single definition of every served metric

One block per metric: grain, numerator, denominator, null policy, and the
test that pins it. `dbt/models/marts/schema.yml` links here and never
restates a formula (`tests/test_metrics_doc.py`); a metric quoted anywhere
else (RESULTS, the A/B design, a dashboard) cites this file. Every block is a
function of raw + dims + vars — no clock, no truth (CLAUDE.md → Determinism
policy). Spec: `ARCHITECTURE.md` §2.6; landed in
`../specs/phase-4-marts.md`.

## ontime_rate_daily

Mart `dbt/models/marts/ontime_rate_daily.sql`, one row per
`(cohort_id, prompt_date)`. `cohort_id` is the prompt's own — the cohort the
notification service sent it as (DECISIONS Phase 3), never the user's
current assignment. Golden: `fixtures/tiny/expected/ontime_rate_daily.csv`,
checked by `make report PROFILE=tiny`.

### `prompt_date`

- **Grain:** one per prompt.
- **Numerator:** the LOCAL calendar date of `sent_at` —
  `cast(sent_at_local as date)`, where `sent_at_local` is computed once in
  staging via `to_local_time` from the user's SCD2 `tz`.
- **Denominator:** n/a.
- **Null policy:** never null (`sent_at` is required on `prompt_sent`).
- **Pinned by:** unit test `ontime_rate_daily_prompt_date_is_local` (a
  Tokyo 08:00 prompt sent at 23:00 UTC the day before lands on the local
  date); `tests/test_marts.py::test_prompt_date_is_local_on_tiny` (34 tiny
  prompts straddle the UTC date).
- Why local: cohorts are defined by the local send hour, so "how did this
  cohort's send time do on day D" is a question about the user's day; a UTC
  date splits one cohort's morning across two rows (ARCHITECTURE §8, Tokyo).

### `prompts_sent`

- **Grain:** cohort-day.
- **Numerator:** count of prompts (`stg_prompts` rows = `attribution` rows)
  sent to the cohort on the local date. A user with two prompts on a day
  counts 2 — prompts, never user-days.
- **Denominator:** n/a (a count).
- **Null policy:** never null; a cohort-day with no prompts has no row.
- **Pinned by:** unit test `ontime_rate_daily_counts_prompts_not_user_days`;
  `dbt/tests/assert_cohort_day_partition.sql` (`prompts_delivered +
  delivery_fault = prompts_sent`).

### `prompts_delivered`

**The on-time denominator** (ARCHITECTURE §4 invariant 6).

- **Grain:** cohort-day.
- **Numerator:** count of prompts with `delivered_in_grace` — a
  `prompt_delivered` receipt within `delivery_grace_min` of `sent_at`
  (the predicate §2.5 rule 1 negates, so this count and the
  `delivery_fault` label cannot drift apart).
- **Denominator:** n/a (a count).
- **Null policy:** never null; 0 on a day where nothing was delivered.
- **Pinned by:** `dbt/tests/assert_cohort_day_partition.sql`; unit test
  `ontime_rate_daily_five_labels_one_day`;
  `tests/test_marts.py::test_partition_holds_on_every_tiny_cohort_day`
  (tiny: 123 of 140).
- Why not user-days: a user-day denominator makes delivery faults vanish
  (§2.6); why not `prompts_sent`: a delivery outage would then read as a
  timing problem.

### `on_time`

- **Grain:** cohort-day.
- **Numerator:** count of prompts labelled `on_time` (§2.5, exactly one
  label per prompt). The numerator of `ontime_rate`.
- **Denominator:** n/a (a count).
- **Null policy:** never null; 0 when absent.
- **Pinned by:** `dbt/tests/assert_cohort_day_partition.sql` — the four delivered labels (`on_time`, `upload_fault`, `timing_gap`, `unattributed`) sum to `prompts_delivered`; unit
  test `ontime_rate_daily_five_labels_one_day`; tiny 75.

### `upload_fault`

- **Grain:** cohort-day.
- **Numerator:** count of prompts labelled `upload_fault` (§2.5, exactly one
  label per prompt). A delivered label.
- **Denominator:** n/a (a count).
- **Null policy:** never null; 0 when absent.
- **Pinned by:** `dbt/tests/assert_cohort_day_partition.sql` — the four delivered labels (`on_time`, `upload_fault`, `timing_gap`, `unattributed`) sum to `prompts_delivered`; unit
  test `ontime_rate_daily_five_labels_one_day`; tiny 8.

### `timing_gap`

- **Grain:** cohort-day.
- **Numerator:** count of prompts labelled `timing_gap` (§2.5, exactly one
  label per prompt). A delivered label.
- **Denominator:** n/a (a count).
- **Null policy:** never null; 0 when absent.
- **Pinned by:** `dbt/tests/assert_cohort_day_partition.sql` — the four delivered labels (`on_time`, `upload_fault`, `timing_gap`, `unattributed`) sum to `prompts_delivered`; unit
  test `ontime_rate_daily_five_labels_one_day`; tiny 34.

### `unattributed`

- **Grain:** cohort-day.
- **Numerator:** count of prompts labelled `unattributed` (§2.5, exactly one
  label per prompt). A delivered label; share bounded by `unattributed_max` (`assert_unattributed_share_bounded.sql`).
- **Denominator:** n/a (a count).
- **Null policy:** never null; 0 when absent.
- **Pinned by:** `dbt/tests/assert_cohort_day_partition.sql` — the four delivered labels (`on_time`, `upload_fault`, `timing_gap`, `unattributed`) sum to `prompts_delivered`; unit
  test `ontime_rate_daily_five_labels_one_day`; tiny 6.

### `delivery_fault`

- **Grain:** cohort-day.
- **Numerator:** count of prompts labelled `delivery_fault` (§2.5, exactly one
  label per prompt). NOT in the denominator: counted beside it, `prompts_delivered + delivery_fault = prompts_sent`.
- **Denominator:** n/a (a count).
- **Null policy:** never null; 0 when absent.
- **Pinned by:** `dbt/tests/assert_cohort_day_partition.sql` — the four delivered labels (`on_time`, `upload_fault`, `timing_gap`, `unattributed`) sum to `prompts_delivered`; unit
  test `ontime_rate_daily_five_labels_one_day`; tiny 17.

### `ontime_rate`

- **Grain:** cohort-day (the overall figure `make report` prints is
  `sum(on_time) / sum(prompts_delivered)` over the mart — the same
  denominator, never an average of daily rates).
- **Numerator:** `on_time`.
- **Denominator:** `prompts_delivered`.
- **Null policy:** `safe_divide` — NULL when `prompts_delivered = 0` (a
  day where nothing was delivered has an undefined rate; its counts are
  still populated); **0, never NULL, when prompts were delivered and none
  was on time**. Rounded to 6 places in the mart so the frozen golden is
  stable across engines.
- **Pinned by:** unit tests `ontime_rate_daily_zero_on_time_is_zero`,
  `ontime_rate_daily_nothing_delivered_is_null`;
  `tests/pins.py::ONTIME_RATE` (tiny 75 / 123 = 0.610) via
  `tests/test_marts.py::test_overall_rate_matches_pin` and `make report`.

## ontime_retention

Mart `dbt/models/marts/ontime_retention.sql`, one row per `user_id`.
**Descriptive only.** Synthetic data cannot prove retention lift; the
retention gap in generated data is a designed property of the generator,
never a finding (ARCHITECTURE §7, PROJECT_BRIEF §5). Not golden-frozen: on
tiny (7 days) every `retained` is NULL, so the frozen file would pin nothing;
`tests/pins.py::RETENTION_ROWS` and `ORGANIC_OPEN_ROWS` are the pins.

Columns `anchor_date` (the user's first `prompt_date`), `observed_through`
(the data-derived horizon — `max` local event time over `stg_events`, never
the clock), `prompts_delivered` and `on_time` (the user's counts over prompts
whose `prompt_date` is within `retention_days` of the anchor), and the user's
`ontime_rate` (`safe_divide(on_time, prompts_delivered)`, the same null
policy as above) are the inputs to:

### `retained`

- **Grain:** user.
- **Numerator:** an organic `app_opened` (the reachability signal — the one
  event with no `prompt_id`) whose local date is on or after
  `anchor_date + retention_days`; day arithmetic is `timestamp_diff('day',
  …)` on midnight timestamps, no date-add dialect.
- **Denominator:** n/a (a boolean per user).
- **Null policy:** three states. `true` — such an open exists; `false` —
  none exists AND `observed_through ≥ anchor_date + retention_days` (the
  window closed with no return); **NULL — the window has not closed** in
  the data (`observed_through` is earlier), which is every tiny row. An
  unobservable user is never reported as churned.
- **Pinned by:** unit test `ontime_retention_three_states` (a 30-day
  synthetic input: retained / churned / unobservable);
  `tests/test_marts.py::test_retention_is_all_null_on_tiny`;
  `tests/test_marts.py::test_retention_var_equals_pin` (`retention_days`
  = 28, ARCHITECTURE §2.6).
