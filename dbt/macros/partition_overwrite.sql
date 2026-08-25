{#- Delete-and-insert per partition on DuckDB, insert_overwrite on BigQuery (§2.7). Body exists for Phase 7; no caller yet. -#}
{% macro partition_overwrite(target_relation, partition_col, partitions) %}
    {{ return(adapter.dispatch('partition_overwrite', 'ontime')(target_relation, partition_col, partitions)) }}
{% endmacro %}

{% macro duckdb__partition_overwrite(target_relation, partition_col, partitions) %}
    delete from {{ target_relation }} where {{ partition_col }} in ({{ partitions | join(', ') }})
{% endmacro %}

{% macro bigquery__partition_overwrite(target_relation, partition_col, partitions) %}
    {{ exceptions.raise_compiler_error("partition_overwrite: the BigQuery body lands in Phase 9") }}
{% endmacro %}
