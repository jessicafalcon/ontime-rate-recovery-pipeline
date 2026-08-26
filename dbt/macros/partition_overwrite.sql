{#- Delete-and-insert per partition on DuckDB, insert_overwrite on BigQuery
    (§2.7). The set-based subquery form has no partition list to mis-quote: it
    replaces exactly the partitions present in the incoming batch. `dest_cols` is
    the model's column list (from dbt's dest_columns), so the insert is
    column-explicit, not `select *`. First caller: the incremental event-level
    models via the custom strategy below (Phase 7, closes the BACKLOG row). -#}
{% macro partition_overwrite(target_relation, batch_relation, partition_col, dest_cols) %}
    {{ return(adapter.dispatch('partition_overwrite', 'ontime')(target_relation, batch_relation, partition_col, dest_cols)) }}
{% endmacro %}

{% macro duckdb__partition_overwrite(target_relation, batch_relation, partition_col, dest_cols) %}
    delete from {{ target_relation }}
    where {{ partition_col }} in (select distinct {{ partition_col }} from {{ batch_relation }});
    insert into {{ target_relation }} ({{ dest_cols }})
    select {{ dest_cols }} from {{ batch_relation }}
{% endmacro %}

{% macro bigquery__partition_overwrite(target_relation, batch_relation, partition_col, dest_cols) %}
    {{ exceptions.raise_compiler_error("partition_overwrite: the BigQuery body lands in Phase 9") }}
{% endmacro %}

{#- The custom incremental strategy (dbt resolves incremental_strategy=
    'partition_overwrite' to this name). It is dbt plumbing, not a dispatch macro
    — it names the partition column from the model's `partition_by` config and
    the columns from dest_columns, then calls the one dispatched seam above. The
    conventions test counts dispatch macros, so this adds none. -#}
{% macro get_incremental_partition_overwrite_sql(arg_dict) %}
    {% set dest_cols = arg_dict["dest_columns"] | map(attribute="quoted") | join(", ") %}
    {% do return(partition_overwrite(
        arg_dict["target_relation"],
        arg_dict["temp_relation"],
        config.require("partition_by"),
        dest_cols
    )) %}
{% endmacro %}
