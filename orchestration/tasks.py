"""The pipeline's ordered task commands — the one definition the Airflow DAG and
the offline structure test share.

No Airflow import: this is a static manifest of `make` targets (a task is a
`make` target — CLAUDE.md § Engineering contracts), so importing it needs no
scheduler and `tests/test_dag_structure.py` runs under `make test` without
apache-airflow (which is Docker-only, never in the venv / uv.lock). The DAG in
`dags/pipeline_dag.py` builds one BashOperator per entry, in order.

The interval → THROUGH mapping is a **literal Jinja token** Airflow renders at
run time (`{{ data_interval_end | ds }}`); we compute nothing, so "Airflow
contains no logic" holds. These two steps ARE `make pipeline`'s WRITING steps
(serving.cli.pipeline runs dbt build → eval → write-back; `eval` asserts the
full-data pins and reads the side-file, so it is a union-only validation gate in
`make pipeline`/CI, never a per-interval task — Amendment 1). The build carries
the interval's THROUGH so a per-interval run lands only files ≤ that date; eval
writes no table, so the DAG's two outputs stay byte-identical to `make
pipeline`'s."""

from __future__ import annotations

import os

PROFILE = "tiny"

# Rendered to YYYY-MM-DD by Airflow, never by us. A per-interval build lands only
# the files uploaded on or before this date (Phase 8b); the DAG's @daily schedule
# gives consecutive intervals a 1-day gap, well within lookback_days, so an
# explicit backfill converges to the union (catchup=False).
THROUGH_TEMPLATE = "{{ data_interval_end | ds }}"


def build_tasks(target: str, project: str) -> list[tuple[str, str]]:
    """The ordered (task_id, make command) list — make pipeline's WRITING steps.

    `target == "duckdb"` (the default) renders the local build + local write-back,
    byte-identical to the Docker-local DAG (Phase 8b). Any other target is a cloud
    target (Phase 12): the build lands on that warehouse and the write-back writes
    the Spanner serving table, each carrying `PROJECT` (single-quoted) and
    `CONFIRM=yes` — which is command-line origin inside the BashOperator, so the
    `$(origin CONFIRM)` gate accepts it. Selecting a target is config, not logic:
    every rendered command is a `make` target (CLAUDE.md "Airflow contains no
    logic"). `THROUGH` is single-quoted so the rendered date is one shell token.
    """
    if target == "duckdb":
        return [
            (
                "dbt_build",
                f"make dbt-build PROFILE={PROFILE} TARGET={target} "
                f"THROUGH='{THROUGH_TEMPLATE}'",
            ),
            ("writeback", f"make writeback PROFILE={PROFILE}"),
        ]
    return [
        (
            "dbt_build",
            f"make dbt-build PROFILE={PROFILE} TARGET={target} "
            f"PROJECT='{project}' CONFIRM=yes THROUGH='{THROUGH_TEMPLATE}'",
        ),
        (
            "writeback",
            f"make writeback PROFILE={PROFILE} TARGET=spanner "
            f"PROJECT='{project}' CONFIRM=yes",
        ),
    ]


# The warehouse the build lands in and builds against (Phase 9b): the build's
# landing is the target's own (duckdb → the DuckDB file; bigquery → GCS →
# BigQuery), so the DAG names it. Env-driven config (Phase 12): unset → duckdb,
# the Docker-local DAG unchanged; the live rehearsal and the Composer run set
# OTR_DAG_TARGET=bigquery + OTR_DAG_PROJECT=<id> so one DAG run builds on BigQuery
# and writes Spanner. Read at parse time; it selects a target, never a path.
TARGET = os.environ.get("OTR_DAG_TARGET", "duckdb")
PROJECT = os.environ.get("OTR_DAG_PROJECT", "")

TASKS: list[tuple[str, str]] = build_tasks(TARGET, PROJECT)
