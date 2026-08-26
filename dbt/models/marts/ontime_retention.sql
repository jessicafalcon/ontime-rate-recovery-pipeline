-- One row per user (docs/METRICS.md). Descriptive only: the retention gap in
-- synthetic data is a designed property, never a finding (ARCHITECTURE §7).
-- anchor_date = the user's first local prompt date; ontime_rate = the user's
-- on-time share over prompts with prompt_date in [anchor, anchor + retention_days)
-- — half-open; the close day is the first day of retained's window;
-- observed_through = the data-derived horizon (max local event time — never the
-- clock); retained = NULL while the horizon is before anchor + retention_days
-- (unobservable — on tiny every row), else whether an organic app_opened falls
-- on or after that day. Day arithmetic goes through timestamp_diff on
-- midnight timestamps so no date-add dialect enters the model.

with prompts as (

    select
        user_id,
        cast(sent_at_local as date) as prompt_date,
        label,
        delivered_in_grace
    from {{ ref('attribution') }}

),

anchors as (

    select
        user_id,
        min(prompt_date) as anchor_date
    from prompts
    group by user_id

),

windowed as (

    select
        p.user_id,
        cast(sum(case when p.delivered_in_grace then 1 else 0 end) as bigint) as prompts_delivered,
        cast(sum(case when p.label = 'on_time' then 1 else 0 end) as bigint) as on_time
    from prompts as p
    inner join anchors as a
        on a.user_id = p.user_id
    where
        {{ timestamp_diff('day', 'cast(a.anchor_date as timestamp)', 'cast(p.prompt_date as timestamp)') }}
        < {{ var('retention_days') }}
    group by p.user_id

),

horizon as (

    select
        max(cast(client_event_time_local as date)) as observed_through
    from {{ ref('stg_events') }}

),

opens as (

    select
        e.user_id,
        max(case
            when {{ timestamp_diff('day', 'cast(a.anchor_date as timestamp)', 'cast(cast(e.client_event_time_local as date) as timestamp)') }}
                >= {{ var('retention_days') }}
            then 1 else 0
        end) = 1 as opened_after_close
    from {{ ref('stg_events') }} as e
    inner join anchors as a
        on a.user_id = e.user_id
    where e.event_type = 'app_opened'
    group by e.user_id

),

joined as (

    select
        a.user_id,
        a.anchor_date,
        h.observed_through,
        {{ timestamp_diff('day', 'cast(a.anchor_date as timestamp)', 'cast(h.observed_through as timestamp)') }}
            >= {{ var('retention_days') }} as window_closed,
        coalesce(o.opened_after_close, false) as opened_after_close,
        coalesce(w.prompts_delivered, 0) as prompts_delivered,
        coalesce(w.on_time, 0) as on_time
    from anchors as a
    cross join horizon as h
    left join windowed as w
        on w.user_id = a.user_id
    left join opens as o
        on o.user_id = a.user_id

)

select
    user_id,
    anchor_date,
    observed_through,
    prompts_delivered,
    on_time,
    round({{ safe_divide('on_time', 'prompts_delivered') }}, 6) as ontime_rate,
    case
        when not window_closed then null
        when opened_after_close then true
        else false
    end as retained
from joined
