{#- Where a model lands (Phase 9b, Amendment I of 9a). On the bigquery target every
    model resolves to target.schema — the `ontime` dataset Terraform created; the
    per-folder `+schema` would otherwise make dbt create `ontime_staging …
    ontime_scores`, five datasets outside Terraform (the SA cannot; an operator's
    ADC could). Every other target keeps dbt's default, restated here verbatim
    (`<target.schema>_<custom>` — `main_staging` … on DuckDB, the names every
    local reader hard-codes; NOT generate_schema_name_for_env, which collapses
    every non-prod target). Keyed
    on target.type (the dialect), never target.name. A dbt hook override, not a
    dispatch macro: the five-macro count is unchanged. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if target.type == 'bigquery' -%}
        {{ target.schema }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
