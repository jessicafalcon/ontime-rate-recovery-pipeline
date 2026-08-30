-- One row per prompt_id (= prompt×user: stg_prompts is unique on prompt_id),
-- exactly one of five labels (ARCHITECTURE §2.5 as amended in Phase 3):
--   1 delivery_fault  no receipt within delivery_grace_min
--   2 unattributed    a client clock AHEAD of the server past skew_max_min —
--                     a gate on the clock evidence below, never a positive delay
--   3 on_time         a response whose client AND received clocks are in the window
--   4 upload_fault    a response exists but its capture / upload (the client-side
--                     events; response_recorded is backend-stamped) has a client
--                     time inside the window and a received time outside it, or a
--                     failed upload chain with no response
--   5 timing_gap      delivered, no capture / response in the window, no upload
--   else unattributed contradictory evidence (e.g. a capture with no upload)
-- The window is [sent_at, sent_at + window_minutes) — half-open, via
-- timestamp_diff so both dialects agree. Evidence is aggregated once per prompt
-- and exposed as columns so every arm reads a boolean; the arms are the unit
-- the mutation sweep drops and swaps (specs/phase-3-attribution.md). The
-- existence flags are max(case … 1 else 0 end) = 1 — ANSI on both dialects
-- (the boolean aggregate is DuckDB/Postgres-only; a dialect call belongs behind a macro or not
-- in a model: tests/test_dbt_conventions.py).
--
-- Incremental (Phase 7) on prompt_date (moved upstream from the mart). status is
-- `final` once the partition is >= lookback_days behind the data-derived horizon
-- (max(server_upload_time)), else `provisional`; a final label never changes
-- (§2.5) because a closed partition is out of the reprocessing window.

{{ config(
    materialized='incremental',
    incremental_strategy=('insert_overwrite' if target.type == 'bigquery' else 'partition_overwrite'),
    meta={'overwrite_partition_col': 'prompt_date'},
    partition_by=({'field': 'prompt_date', 'data_type': 'date'} if target.type == 'bigquery' else none),
    unique_key='prompt_id',
) }}

with prompts as (

    select
        prompt_id,
        user_id,
        prompt_cohort_id as cohort_id,
        tz,
        window_minutes,
        sent_at,
        sent_at_local,
        delivered_at
    from {{ ref('stg_prompts') }}

),

events as (

    select
        e.prompt_id,
        e.event_type,
        e.upload_delay_seconds,
        {{ timestamp_diff('second', 'p.sent_at', 'e.client_event_time') }}
            as client_offset_seconds,
        {{ timestamp_diff('second', 'p.sent_at', 'e.server_received_time') }}
            as received_offset_seconds,
        p.window_minutes * 60 as window_seconds
    from {{ ref('stg_events') }} as e
    inner join prompts as p
        on p.prompt_id = e.prompt_id

),

flagged as (

    select
        prompt_id,
        event_type,
        upload_delay_seconds,
        client_offset_seconds >= 0 and client_offset_seconds < window_seconds
            as client_in_window,
        received_offset_seconds >= 0 and received_offset_seconds < window_seconds
            as received_in_window
    from events

),

evidence as (

    select
        prompt_id,
        min(upload_delay_seconds) as min_upload_delay_seconds,
        max(case when event_type = 'response_recorded' and client_in_window and received_in_window then 1 else 0 end) = 1
            as response_on_time,
        max(case
            when event_type in ('capture_started', 'upload_started', 'upload_completed')
                and client_in_window and not received_in_window
            then 1 else 0
        end) = 1 as captured_in_window_received_late,
        max(case when event_type = 'response_recorded' then 1 else 0 end) = 1 as has_response,
        max(case when event_type = 'response_recorded' and client_in_window then 1 else 0 end) = 1
            as has_response_in_window,
        max(case when event_type = 'capture_started' and client_in_window then 1 else 0 end) = 1
            as has_capture_in_window,
        max(case when event_type = 'upload_failed' then 1 else 0 end) = 1 as has_upload_failed,
        max(case when event_type in ('upload_started', 'upload_failed', 'upload_completed') then 1 else 0 end) = 1
            as has_upload_event
    from flagged
    group by prompt_id

),

labelled as (

    select
        p.prompt_id,
        p.user_id,
        p.cohort_id,
        p.tz,
        p.window_minutes,
        p.sent_at,
        p.sent_at_local,
        cast(p.sent_at_local as date) as prompt_date,
        p.delivered_at,
        p.delivered_at is not null
        and {{ timestamp_diff('second', 'p.sent_at', 'p.delivered_at') }}
            <= {{ var('delivery_grace_min') }} * 60
            as delivered_in_grace,
        coalesce(v.min_upload_delay_seconds, 0) as min_upload_delay_seconds,
        coalesce(v.response_on_time, false) as response_on_time,
        coalesce(v.captured_in_window_received_late, false) as captured_in_window_received_late,
        coalesce(v.has_response, false) as has_response,
        coalesce(v.has_response_in_window, false) as has_response_in_window,
        coalesce(v.has_capture_in_window, false) as has_capture_in_window,
        coalesce(v.has_upload_failed, false) as has_upload_failed,
        coalesce(v.has_upload_event, false) as has_upload_event
    from prompts as p
    left join evidence as v
        on v.prompt_id = p.prompt_id

),

attributed as (

    select
        prompt_id,
        user_id,
        cohort_id,
        tz,
        window_minutes,
        sent_at,
        sent_at_local,
        prompt_date,
        delivered_at,
        delivered_in_grace,
        min_upload_delay_seconds,
        response_on_time,
        captured_in_window_received_late,
        has_response,
        has_response_in_window,
        has_capture_in_window,
        has_upload_failed,
        has_upload_event,
        case
            when not delivered_in_grace then 'delivery_fault'
            when min_upload_delay_seconds < -{{ var('skew_max_min') }} * 60 then 'unattributed'
            when response_on_time then 'on_time'
            when (has_response and captured_in_window_received_late) or (has_upload_failed and not has_response) then 'upload_fault'
            when not has_capture_in_window and not has_response_in_window and not has_upload_event then 'timing_gap'
            else 'unattributed'
        end as label
    from labelled

)

{% set horizon = "(select cast(max(server_upload_time) as date) from " ~ ref('stg_events') ~ ")" %}

select
    *,
    case
        when {{ timestamp_diff('day', 'prompt_date', horizon) }} >= {{ var('lookback_days') }} then 'final'
        else 'provisional'
    end as status
from attributed
{% if is_incremental() %}
-- reprocess only partitions inside the lookback of the landing high-water mark
where {{ timestamp_diff('day', 'prompt_date', horizon) }} <= {{ var('lookback_days') }}
{% endif %}
