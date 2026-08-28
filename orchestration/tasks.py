"""The pipeline's ordered task commands — the one definition the Airflow DAG and
the offline structure test share.

No Airflow import: this is a static manifest of `make` targets (a task is a
`make` target — CLAUDE.md § Engineering contracts), so importing it needs no
scheduler and `tests/test_dag_structure.py` runs under `make test` without
apache-airflow (which is Docker-only, never in the venv / uv.lock). The DAG in
`dags/pipeline_dag.py` builds one BashOperator per entry, in order.

The interval → THROUGH mapping is a **literal Jinja token** Airflow renders at
run time (`{{ data_interval_end | ds }}`); we compute nothing, so "Airflow
contains no logic" holds. These three steps ARE `make pipeline`'s
(serving.cli.pipeline: dbt build → eval → write-back); the build carries the
interval's THROUGH so a per-interval run lands only files ≤ that date."""

from __future__ import annotations

PROFILE = "tiny"

# Rendered to YYYY-MM-DD by Airflow, never by us. A per-interval build lands only
# the files uploaded on or before this date (Phase 8b); the DAG's @daily schedule
# gives consecutive intervals a 1-day gap, well within lookback_days, so a
# catchup backfill converges to the union.
THROUGH_TEMPLATE = "{{ data_interval_end | ds }}"

# (task_id, make command) in dependency order.
TASKS: list[tuple[str, str]] = [
    ("dbt_build", f"make dbt-build PROFILE={PROFILE} THROUGH={THROUGH_TEMPLATE}"),
    ("eval", f"make eval PROFILE={PROFILE}"),
    ("writeback", f"make writeback PROFILE={PROFILE}"),
]
