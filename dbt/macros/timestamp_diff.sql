{#- end − start in whole units ('second', 'minute', 'hour', 'day'); the upload delay is timestamp_diff('second', client_event_time, server_received_time). -#}
{% macro timestamp_diff(unit, start_ts, end_ts) %}
    {{ return(adapter.dispatch('timestamp_diff', 'ontime')(unit, start_ts, end_ts)) }}
{% endmacro %}

{% macro duckdb__timestamp_diff(unit, start_ts, end_ts) %}
    date_diff('{{ unit }}', {{ start_ts }}, {{ end_ts }})
{% endmacro %}

{% macro bigquery__timestamp_diff(unit, start_ts, end_ts) %}
    {{ exceptions.raise_compiler_error("timestamp_diff: the BigQuery body lands in Phase 9") }}
{% endmacro %}
