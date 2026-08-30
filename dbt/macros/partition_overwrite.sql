{#- Delete-and-insert per partition on DuckDB; on BigQuery the adapter's native
    insert_overwrite strategy, selected in the models' config (§2.7, Amendment U). The set-based subquery form has no partition list to mis-quote: it
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

{#- Unreachable BY DESIGN (Phase 9b, Amendment U). dbt-bigquery ships its own
    incremental materialization and validates incremental_strategy against
    'merge' | 'insert_overwrite' | 'microbatch' — a custom get_incremental_<name>_sql
    is never looked up there — so on BigQuery the models select the adapter's
    NATIVE insert_overwrite in config() (dynamic mode: delete the partitions
    present in the batch, then insert — the same semantics as the DuckDB body;
    §2.7, ARCHITECTURE §8). Reaching this body means a model bypassed that
    selection: fail loudly rather than emit SQL nobody runs. -#}
{% macro bigquery__partition_overwrite(target_relation, batch_relation, partition_col, dest_cols) %}
    {{ exceptions.raise_compiler_error("partition_overwrite: on BigQuery the seam is the adapter's native insert_overwrite strategy (Amendment U); this body must not be reached") }}
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
