-- A conflicting duplicate: one insert_id whose copies tie on all three clocks
-- but carry different event_properties. The dedupe keeps the earliest copy by
-- content, so tying copies must be identical (Phase 2 invariant 1). The DuckDB
-- landing refuses such a landing in Python (landing/load.py); this is the same
-- predicate on the source, so the BigQuery landing (which has no Python
-- landing step in the way) fails the build instead of resolving it silently
-- (Phase 9b; closes the BACKLOG row). The payload is compared key by key
-- through the json macro — the six keys generator/models.py::PROPERTY_KEYS
-- allows — because neither dialect can group or cast a whole JSON value
-- portably (BigQuery: JSON is not groupable, not castable to STRING). A null
-- is marked explicitly ('<null>'), so "" and null differ on both engines
-- (DuckDB's concat would otherwise skip the null — review round 1 #3).
-- Residual, by contract: a JSON null and a MISSING key both read as null
-- (PROPERTY_KEYS fixes the key set per event_type, so the two cannot coexist
-- for one event_type), and a '|' inside a value could alias (values are
-- counters and error codes) — BACKLOG, trigger "an optional key or a
-- free-text value enters the contract".
{% set keys = ['prompt_id', 'cohort_id', 'window_minutes', 'attempt', 'error_code', 'response_id'] %}
select
    insert_id
from {{ source('raw', 'events') }}
group by
    insert_id,
    client_event_time,
    server_received_time,
    server_upload_time
having count(distinct concat(
    {%- for key in keys %}
    case
        when {{ json_extract('event_properties', key) }} is null then '<null>'
        else {{ json_extract('event_properties', key) }}
    end{{ ", '|'," if not loop.last }}
    {%- endfor %}
)) > 1
