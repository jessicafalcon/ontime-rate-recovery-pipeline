{#- NULL on a zero or null denominator, never an error (Phase 4 rates). -#}
{% macro safe_divide(numerator, denominator) %}
    {{ return(adapter.dispatch('safe_divide', 'ontime')(numerator, denominator)) }}
{% endmacro %}

{% macro duckdb__safe_divide(numerator, denominator) %}
    (case when {{ denominator }} is null or {{ denominator }} = 0 then null else {{ numerator }} / {{ denominator }} end)
{% endmacro %}

{% macro bigquery__safe_divide(numerator, denominator) %}
    {{ exceptions.raise_compiler_error("safe_divide: the BigQuery body lands in Phase 9") }}
{% endmacro %}
