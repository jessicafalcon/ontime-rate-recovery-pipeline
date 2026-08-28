"""The local pipeline as an Airflow DAG (Docker-local, Phase 8b).

No logic: each task is a `make` target from `orchestration.tasks`, ordered
`dbt_build >> eval >> writeback` — the same steps `make pipeline` runs, minus the
scheduler. The interval is rendered to a THROUGH date by Airflow templating (the
command carries the literal token), so a catchup run builds each landing
incrementally and a backfill converges to the union (spec Done-when 2).

`max_active_runs=1` serialises catchup so only ONE process writes
`data/<p>.duckdb` at a time — DuckDB is single-writer, and each BashOperator is a
separate subprocess that opens and closes the file in turn. `retries=0` and
templated dates keep the run deterministic; run ids, task timings and logs are
non-deterministic by nature and nothing asserts them."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator

from orchestration.tasks import TASKS

# The repo root (dags → orchestration → repo); `make` runs here inside the mount.
REPO = Path(__file__).resolve().parents[2]

with DAG(
    dag_id="pipeline",
    schedule="@daily",
    start_date=datetime(2026, 1, 6),
    catchup=True,
    max_active_runs=1,  # one writer on data/<p>.duckdb at a time (DuckDB single-writer)
    default_args={"retries": 0},
    tags=["ontime", "phase-8b"],
) as dag:
    steps = [
        BashOperator(task_id=task_id, bash_command=command, cwd=str(REPO))
        for task_id, command in TASKS
    ]
    for upstream, downstream in zip(steps, steps[1:], strict=False):
        upstream >> downstream
