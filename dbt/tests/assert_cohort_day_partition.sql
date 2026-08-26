-- Invariant 1 (Phase 4): on every cohort-day the four delivered labels sum to
-- prompts_delivered, and prompts_delivered + delivery_fault = prompts_sent.
-- delivery_fault is counted, never in the delivered sum (docs/METRICS.md).
select
    cohort_id,
    prompt_date
from {{ ref('ontime_rate_daily') }}
where
    on_time + upload_fault + timing_gap + unattributed <> prompts_delivered
    or prompts_delivered + delivery_fault <> prompts_sent
