-- Phase 5 invariants 5, 6: every served time is within max_user_shift_min
-- (circular) of its cohort moment, and the hour/minute columns are in range.
with served as (
    select
        user_id,
        send_hour_local,
        send_minute_local,
        cohort_hour_local,
        send_hour_local + send_minute_local / 60.0 - cohort_hour_local as raw_diff
    from {{ ref('scores_send_time') }}
)
select user_id
from served
where
    abs(raw_diff - 24 * floor((raw_diff + 12) / 24)) > {{ var('max_user_shift_min') }} / 60.0 + 1e-9
    or send_hour_local < 0 or send_hour_local > 23
    or send_minute_local < 0 or send_minute_local > 59
