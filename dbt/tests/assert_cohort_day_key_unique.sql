-- Invariant 4 (Phase 4): (cohort_id, prompt_date) appears once.
select
    cohort_id,
    prompt_date
from {{ ref('ontime_rate_daily') }}
group by cohort_id, prompt_date
having count(*) > 1
