-- (user_id, valid_from) identifies one SCD2 row (spec invariant 6; dbt's
-- built-in `unique` is single-column and no dbt package is on the allowlist).
select
    user_id,
    valid_from,
    count(*) as n
from {{ source('raw', 'dim_user') }}
group by 1, 2
having count(*) > 1
