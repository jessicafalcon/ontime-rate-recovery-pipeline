-- Per user, the local-hour histogram of ORGANIC app_opened events (the only
-- event with no prompt_id) inside the feature window. Prompt responses are
-- never an input: a response can only be observed at the hour the prompt was
-- sent (exposure bias, ARCHITECTURE §2.8). The window is
-- (horizon − feature_window_days, horizon] with horizon = max client_event_time
-- over ALL staged events — data-derived, never the clock. Each open counts at
-- its own client_event_time_local hour, so a user whose tz changed
-- mid-window keeps one histogram on one local clock (BACKLOG, Phase 5 item 2).
-- Sparse: no row for an empty bin. last_open_at feeds computed_as_of.

with horizon as (

    select
        max(client_event_time) as horizon
    from {{ ref('stg_events') }}

),

opens as (

    select
        e.user_id,
        cast(extract(hour from e.client_event_time_local) as integer) as hour_local,
        e.client_event_time
    from {{ ref('stg_events') }} as e
    cross join horizon as h
    where
        e.event_type = 'app_opened'
        and {{ timestamp_diff('second', 'e.client_event_time', 'h.horizon') }}
            < {{ var('feature_window_days') }} * 86400

)

select
    user_id,
    hour_local,
    cast(count(*) as bigint) as n_opens,
    max(client_event_time) as last_open_at
from opens
group by
    user_id,
    hour_local
