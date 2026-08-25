{#- Naive UTC timestamp → naive wall time in the IANA zone. The inner call pins the input as UTC so the host's session TimeZone never enters (ARCHITECTURE §8). -#}
{% macro to_local_time(ts_utc, tz) %}
    {{ return(adapter.dispatch('to_local_time', 'ontime')(ts_utc, tz)) }}
{% endmacro %}

{% macro duckdb__to_local_time(ts_utc, tz) %}
    (timezone({{ tz }}, timezone('UTC', {{ ts_utc }}))::timestamp)
{% endmacro %}

{% macro bigquery__to_local_time(ts_utc, tz) %}
    {{ exceptions.raise_compiler_error("to_local_time: the BigQuery body lands in Phase 9") }}
{% endmacro %}
