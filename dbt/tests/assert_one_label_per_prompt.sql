-- Invariant 1 (exhaustive-exclusive): attribution has exactly one row per
-- stg_prompts row and no null label. A row here is a failure.
with counts as (
    select
        (select count(*) from {{ ref('stg_prompts') }}) as prompts,
        (select count(*) from {{ ref('attribution') }}) as labelled,
        (select count(*) from {{ ref('attribution') }} where label is null) as unlabelled
)
select *
from counts
where prompts != labelled or unlabelled != 0
