-- Invariant 10: the prompt's own cohort (prompt_sent event) equals the user's
-- dim_user cohort at send time. attribution uses the prompt's; the first
-- divergence is a red build, not a silent choice (BACKLOG, Phase 3).
select
    prompt_id,
    cohort_id,
    prompt_cohort_id
from {{ ref('stg_prompts') }}
where prompt_cohort_id is null or prompt_cohort_id != cohort_id
