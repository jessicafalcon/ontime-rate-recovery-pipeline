-- Every deduplicated event matches exactly one dim_user row at its
-- client_event_time: zero matches (tz null) and two matches (a doubled row)
-- both fail (spec invariant 2).
with staged as (
    select
        count(*) as n_rows,
        count(distinct insert_id) as n_ids,
        count(tz) as n_with_tz
    from {{ ref('stg_events') }}
)

select *
from staged
where n_rows != n_ids or n_with_tz != n_rows
