-- The current (open SCD2) dim_user row per user: the serving zone the write-back
-- stamps into send_schedule (ARCHITECTURE §2.9). `valid_to is null` is the open
-- row (generator/dims.py); one row per user. The write-back reads this dbt output
-- for tz, never raw.dim_user (§3.1 bars the write-back from raw).
with current_row as (

    select
        user_id,
        cohort_id,
        tz
    from {{ source('raw', 'dim_user') }}
    where valid_to is null

)

select
    user_id,
    cohort_id,
    tz
from current_row
