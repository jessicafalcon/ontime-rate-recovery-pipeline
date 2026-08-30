{#- Scalar string at top-level key; SQL NULL for a JSON null or a missing key. -#}
{% macro json_extract(col, key) %}
    {{ return(adapter.dispatch('json_extract', 'ontime')(col, key)) }}
{% endmacro %}

{% macro duckdb__json_extract(col, key) %}
    ({{ col }} ->> '{{ key }}')
{% endmacro %}

{#- json_value returns a STRING scalar and SQL NULL for a JSON null or a missing key — the ->> contract (Phase 9b). -#}
{% macro bigquery__json_extract(col, key) %}
    json_value({{ col }}, '$.{{ key }}')
{% endmacro %}
