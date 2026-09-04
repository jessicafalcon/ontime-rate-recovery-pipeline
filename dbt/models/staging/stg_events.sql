-- One row per insert_id (the export carries duplicates), typed, with the
-- user's tz valid at client_event_time and the event's local wall time.
-- Dedupe keeps the earliest (server_upload_time, server_received_time,
-- client_event_time) copy — content-derived, never file or load order
-- (invariant 1). Copies tying on all three clocks are identical by contract:
-- the landing step refuses a landing where they differ in event_properties.
--
-- Incremental (Phase 7) on event_date (the local date; an app_opened has no
-- prompt_id). The dedupe qualify runs over the raw source before the lookback
-- filter; on BigQuery the source read is first pruned to a SUPERSET upload-time
-- window (fix/append-landing, below) that still co-locates both copies of any
-- duplicate (they are <= 1 h apart, a generator invariant), so a duplicate is
-- never split and the earliest-copy rule is unchanged. Reprocess partitions
-- inside the lookback of the data-derived horizon (max(server_upload_time)); a
-- closed partition is never rewritten.

{{ config(
    materialized='incremental',
    incremental_strategy=('insert_overwrite' if target.type == 'bigquery' else 'partition_overwrite'),
    meta={'overwrite_partition_col': 'event_date'},
    partition_by=({'field': 'event_date', 'data_type': 'date'} if target.type == 'bigquery' else none),
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
    {% if is_incremental() and target.type == 'bigquery' %}
    -- Source-scan prune (fix/append-landing): on BigQuery raw.events is
    -- DAY-partitioned on server_upload_time, so bounding the read to a superset
    -- upload-time window lets an incremental re-run prune source partitions
    -- instead of re-scanning all of raw (the measured item-6 cost). Native
    -- BigQuery, guarded to this adapter — DuckDB has no partitions, so its SQL is
    -- untouched and every DuckDB golden is byte-identical for free (no dispatch
    -- macro: this never runs on DuckDB). The window is a SUPERSET, wide enough to
    -- (a) include every row whose event_date (client-local) is in the lookback
    -- reprocess window despite the client<->server clock offset and (b) co-locate
    -- both copies of any duplicate insert_id (<= 1 h apart, generator invariant),
    -- so the earliest-copy dedupe below is unchanged. The margin is
    -- var('source_prune_margin_days'), a declared floor pinned by
    -- test_source_prune_margin_covers_every_profile to be >=
    -- ceil(late_arrival_max_hours/24) + tz_days + dup_days for every profile.
    -- Correctness is proven both offline (this predicate renders only under the
    -- guard, the duplicate span is bounded < 1 h for all seeds, the margin is a
    -- per-profile-pinned floor) and LIVE: test-int-bigquery's incremental parity
    -- phase (fix/prune-live-proof) lands a late tail and runs a PLAIN build, so
    -- this predicate renders and executes on BigQuery, and the built tables are
    -- byte-identical to the full-scan goldens (tiny's 9-day span sits inside the
    -- 10-day window, so the prune excludes no partition here — the byte reduction
    -- is a >10-day-span / large-profile effect).
    where server_upload_time >= (
        select timestamp_sub(
            cast(max(server_upload_time) as timestamp),
            interval {{ var('lookback_days') + var('source_prune_margin_days') }} day
        )
        from {{ source('raw', 'events') }}
    )
    {% endif %}
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
