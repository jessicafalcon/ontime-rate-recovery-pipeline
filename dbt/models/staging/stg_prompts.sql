-- One row per prompt_id: the prompt_sent event plus the FIRST prompt_delivered
-- receipt (by client_event_time, then insert_id — a content-derived tie-break).
-- delivered_at is null when no receipt exists (a delivery fault, Phase 3).

with sent as (

    select
        prompt_id,
        user_id,
        cohort_id,
        prompt_cohort_id,
        tz,
        window_minutes,
        client_event_time as sent_at,
        client_event_time_local as sent_at_local
    from {{ ref('stg_events') }}
    where event_type = 'prompt_sent'

),

delivered as (

    select
        prompt_id,
        client_event_time as delivered_at,
        client_event_time_local as delivered_at_local
    from {{ ref('stg_events') }}
    where event_type = 'prompt_delivered'
    qualify row_number() over (
        partition by prompt_id
        order by client_event_time, insert_id
    ) = 1

)

select
    s.prompt_id,
    s.user_id,
    s.cohort_id,
    s.prompt_cohort_id,
    s.tz,
    s.window_minutes,
    s.sent_at,
    s.sent_at_local,
    d.delivered_at,
    d.delivered_at_local
from sent as s
left join delivered as d
    on d.prompt_id = s.prompt_id
