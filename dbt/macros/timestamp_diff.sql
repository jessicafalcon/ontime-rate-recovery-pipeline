{#- end − start in whole units ('second', 'minute', 'hour', 'day'); the upload delay is timestamp_diff('second', client_event_time, server_received_time). -#}
{% macro timestamp_diff(unit, start_ts, end_ts) %}
    {{ return(adapter.dispatch('timestamp_diff', 'ontime')(unit, start_ts, end_ts)) }}
{% endmacro %}

{% macro duckdb__timestamp_diff(unit, start_ts, end_ts) %}
    date_diff('{{ unit }}', {{ start_ts }}, {{ end_ts }})
{% endmacro %}

{#- BigQuery is end-first and TIMESTAMP-only; callers also pass DATE / DATETIME (prompt_date, the retention
    midnights), so both sides are cast — a DATETIME casts as UTC, and only ever differences against another such cast (Phase 9b). -#}
{% macro bigquery__timestamp_diff(unit, start_ts, end_ts) %}
    timestamp_diff(cast({{ end_ts }} as timestamp), cast({{ start_ts }} as timestamp), {{ unit }})
{% endmacro %}
