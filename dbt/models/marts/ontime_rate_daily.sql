-- One row per cohort × local prompt date (docs/METRICS.md). The denominator is
-- prompts_delivered = prompts delivered inside delivery_grace_min (the predicate
-- §2.5 rule 1 negates) — never user-days, never prompts_sent. The four delivered
-- labels sum to it; delivery_fault is counted beside it and completes
-- prompts_sent (dbt/tests/assert_cohort_day_partition.sql). prompt_date is the
-- LOCAL date: cohorts are defined by the local send hour, and a Tokyo 08:00
-- prompt is the previous UTC day (ARCHITECTURE §8). ontime_rate is NULL on a
-- day with nothing delivered (safe_divide's contract) and 0 on a day with
-- delivered prompts and none on time; rounded so the frozen golden is stable.

with labelled as (

    select
        cohort_id,
        cast(sent_at_local as date) as prompt_date,
        label,
        delivered_in_grace
    from {{ ref('attribution') }}

),

daily as (

    select
        cohort_id,
        prompt_date,
        cast(count(*) as bigint) as prompts_sent,
        cast(sum(case when delivered_in_grace then 1 else 0 end) as bigint) as prompts_delivered,
        cast(sum(case when label = 'on_time' then 1 else 0 end) as bigint) as on_time,
        cast(sum(case when label = 'upload_fault' then 1 else 0 end) as bigint) as upload_fault,
        cast(sum(case when label = 'timing_gap' then 1 else 0 end) as bigint) as timing_gap,
        cast(sum(case when label = 'unattributed' then 1 else 0 end) as bigint) as unattributed,
        cast(sum(case when label = 'delivery_fault' then 1 else 0 end) as bigint) as delivery_fault
    from labelled
    group by cohort_id, prompt_date

)

select
    cohort_id,
    prompt_date,
    prompts_sent,
    prompts_delivered,
    on_time,
    upload_fault,
    timing_gap,
    unattributed,
    delivery_fault,
    round({{ safe_divide('on_time', 'prompts_delivered') }}, 6) as ontime_rate
from daily
