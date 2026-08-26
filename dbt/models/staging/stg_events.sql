-- One row per insert_id (the export carries duplicates), typed, with the
-- user's tz valid at client_event_time and the event's local wall time.
-- Dedupe keeps the earliest (server_upload_time, server_received_time,
-- client_event_time) copy — content-derived, never file or load order
-- (invariant 1). Copies tying on all three clocks are identical by contract:
-- the loader refuses a landing where they differ in event_properties.
--
-- Incremental (Phase 7) on event_date (the local date; an app_opened has no
-- prompt_id). The dedupe qualify runs over the WHOLE raw source before the
-- lookback filter, so both copies of a duplicate — which share client_event_time,
-- hence event_date — are always seen together and never split across landings.
-- Reprocess partitions inside the lookback of the data-derived horizon
-- (max(server_upload_time)); a closed partition is never rewritten.

{{ config(
    materialized='incremental',
    incremental_strategy='partition_overwrite',
    partition_by='event_date',
    unique_key='insert_id',
) }}

with raw_events as (

    select
        insert_id,
        event_type,
        user_id,
        device_id,
        client_event_time,
        server_received_time,
        server_upload_time,
        event_properties
    from {{ source('raw', 'events') }}
    qualify row_number() over (
        partition by insert_id
        order by
            server_upload_time,
            server_received_time,
            client_event_time
    ) = 1

),

dim_user as (

    select
        user_id,
        tz,
        cohort_id,
        valid_from,
        valid_to
    from {{ source('raw', 'dim_user') }}

),

joined as (

    select
        e.insert_id,
        e.event_type,
        e.user_id,
        e.device_id,
        e.client_event_time,
        e.server_received_time,
        e.server_upload_time,
        d.tz,
        d.cohort_id,
        e.event_properties
    from raw_events as e
    left join dim_user as d
        on d.user_id = e.user_id
        and d.valid_from <= e.client_event_time
        and (d.valid_to is null or e.client_event_time < d.valid_to)

),

staged as (

    select
        insert_id,
        event_type,
        user_id,
        device_id,
        client_event_time,
        server_received_time,
        server_upload_time,
        tz,
        cohort_id,
        {{ to_local_time('client_event_time', 'tz') }} as client_event_time_local,
        cast({{ to_local_time('client_event_time', 'tz') }} as date) as event_date,
        {{ timestamp_diff('second', 'client_event_time', 'server_received_time') }}
            as upload_delay_seconds,
        {{ json_extract('event_properties', 'prompt_id') }} as prompt_id,
        {{ json_extract('event_properties', 'cohort_id') }} as prompt_cohort_id,
        cast({{ json_extract('event_properties', 'window_minutes') }} as integer)
            as window_minutes,
        cast({{ json_extract('event_properties', 'attempt') }} as integer) as attempt,
        {{ json_extract('event_properties', 'error_code') }} as error_code,
        {{ json_extract('event_properties', 'response_id') }} as response_id
    from joined

)

select * from staged
{% if is_incremental() %}
-- reprocess only partitions inside the lookback of the landing high-water mark
{% set horizon = "(select cast(max(server_upload_time) as date) from " ~ source('raw', 'events') ~ ")" %}
where {{ timestamp_diff('day', 'event_date', horizon) }} <= {{ var('lookback_days') }}
{% endif %}
