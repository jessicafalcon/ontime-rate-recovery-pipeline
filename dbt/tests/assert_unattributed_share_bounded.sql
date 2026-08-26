-- Invariant 4: the unattributed share is bounded by var unattributed_max —
-- the bound that keeps the metric honest (ARCHITECTURE §2.5). A row = failure.
with share as (
    select
        {{ safe_divide("sum(case when label = 'unattributed' then 1 else 0 end)", 'count(*)') }}
            as unattributed_share
    from {{ ref('attribution') }}
)
select *
from share
where unattributed_share > {{ var('unattributed_max') }}
