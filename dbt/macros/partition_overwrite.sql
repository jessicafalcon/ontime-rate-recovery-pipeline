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

{#- The same delete-in-set + insert, as one two-statement BigQuery script; the target is natively
    date-partitioned on partition_col (the models' dialect-guarded partition_by), so the delete prunes to the
    batch's partitions (Phase 9b). -#}
{% macro bigquery__partition_overwrite(target_relation, batch_relation, partition_col, dest_cols) %}
    delete from {{ target_relation }}
    where {{ partition_col }} in (select distinct {{ partition_col }} from {{ batch_relation }});
    insert into {{ target_relation }} ({{ dest_cols }})
    select {{ dest_cols }} from {{ batch_relation }}
{% endmacro %}

{#- The custom incremental strategy (dbt resolves incremental_strategy=
    'partition_overwrite' to this name). It is dbt plumbing, not a dispatch macro
    — it names the partition column from the model's `meta.overwrite_partition_col`
    (a custom key under `meta`, as dbt ≥ 1.10 requires; one neither adapter interprets: dbt-bigquery parses `partition_by`
    as its native partitioning dict and dbt-duckdb rejects a dict there — Phase
    9b, ARCHITECTURE §8) and the columns from dest_columns, then calls the one
    dispatched seam above. The conventions test counts dispatch macros, so this
    adds none. -#}
{% macro get_incremental_partition_overwrite_sql(arg_dict) %}
    {% set dest_cols = arg_dict["dest_columns"] | map(attribute="quoted") | join(", ") %}
    {% set partition_col = config.get("meta", {}).get("overwrite_partition_col") %}
    {% if not partition_col %}
        {{ exceptions.raise_compiler_error("partition_overwrite: the model must set meta.overwrite_partition_col") }}
    {% endif %}
    {% do return(partition_overwrite(
        arg_dict["target_relation"],
        arg_dict["temp_relation"],
        partition_col,
        dest_cols
    )) %}
{% endmacro %}
