{#- Scalar string at top-level key; SQL NULL for a JSON null or a missing key. -#}
{% macro json_extract(col, key) %}
    {{ return(adapter.dispatch('json_extract', 'ontime')(col, key)) }}
{% endmacro %}

{% macro duckdb__json_extract(col, key) %}
    ({{ col }} ->> '{{ key }}')
{% endmacro %}

{% macro bigquery__json_extract(col, key) %}
    {{ exceptions.raise_compiler_error("json_extract: the BigQuery body lands in Phase 9") }}
{% endmacro %}
