{#- Naive UTC timestamp → naive wall time in the IANA zone. The inner call pins the input as UTC so the host's session TimeZone never enters (ARCHITECTURE §8). -#}
{% macro to_local_time(ts_utc, tz) %}
    {{ return(adapter.dispatch('to_local_time', 'ontime')(ts_utc, tz)) }}
{% endmacro %}

{% macro duckdb__to_local_time(ts_utc, tz) %}
    (timezone({{ tz }}, timezone('UTC', {{ ts_utc }}))::timestamp)
{% endmacro %}

{#- A TIMESTAMP is an absolute instant; datetime(ts, tz) is its naive wall time in the zone — BigQuery's DATETIME is the naive type DuckDB's timestamp is (Phase 9b). -#}
{% macro bigquery__to_local_time(ts_utc, tz) %}
    datetime({{ ts_utc }}, {{ tz }})
{% endmacro %}
