{#- NULL on a zero or null denominator, never an error (Phase 4 rates). -#}
{% macro safe_divide(numerator, denominator) %}
    {{ return(adapter.dispatch('safe_divide', 'ontime')(numerator, denominator)) }}
{% endmacro %}

{% macro duckdb__safe_divide(numerator, denominator) %}
    (case when {{ denominator }} is null or {{ denominator }} = 0 then null else {{ numerator }} / {{ denominator }} end)
{% endmacro %}

{#- Native safe_divide is NULL on a zero or null denominator; the cast keeps integer/integer from truncating (DuckDB's `/` is a float divide) (Phase 9b). -#}
{% macro bigquery__safe_divide(numerator, denominator) %}
    safe_divide(cast({{ numerator }} as float64), {{ denominator }})
{% endmacro %}
