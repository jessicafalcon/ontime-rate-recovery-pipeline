-- A conflicting duplicate: one insert_id whose copies tie on all three clocks
-- but carry different event_properties. The dedupe keeps the earliest copy by
-- content, so tying copies must be identical (Phase 2 invariant 1). The DuckDB
-- loader refuses such a landing in Python (loader/load.py); this is the same
-- predicate on the source, so the BigQuery landing (which has no Python
-- loader in the way) fails the build instead of resolving it silently
-- (Phase 9b; closes the BACKLOG row). The payload is compared key by key
-- through the json macro — the six keys generator/models.py::PROPERTY_KEYS
-- allows — because neither dialect can group or cast a whole JSON value
-- portably (BigQuery: JSON is not groupable, not castable to STRING).
select
    insert_id
from {{ source('raw', 'events') }}
group by
    insert_id,
    client_event_time,
    server_received_time,
    server_upload_time
having count(distinct concat(
    coalesce({{ json_extract('event_properties', 'prompt_id') }}, ''), '|',
    coalesce({{ json_extract('event_properties', 'cohort_id') }}, ''), '|',
    coalesce({{ json_extract('event_properties', 'window_minutes') }}, ''), '|',
    coalesce({{ json_extract('event_properties', 'attempt') }}, ''), '|',
    coalesce({{ json_extract('event_properties', 'error_code') }}, ''), '|',
    coalesce({{ json_extract('event_properties', 'response_id') }}, '')
)) > 1
