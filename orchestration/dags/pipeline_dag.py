"""The local pipeline as an Airflow DAG (Docker-local, Phase 8b).

No logic: each task is a `make` target from `orchestration.tasks`, ordered
`dbt_build >> writeback` — `make pipeline`'s WRITING steps, minus the scheduler
(`eval` is a union-only gate, Amendment 1). The interval is rendered to a THROUGH
date by Airflow templating (the command carries the literal token), so a
per-interval run builds only its landing and an explicit backfill converges to the
union (spec Done-when 2).

`catchup=False` (Amendment 2): a past `start_date` with auto-catchup would
backfill every day since — the catchup-to-now the spec rejects. A backfill is
invoked explicitly (`airflow dags test <date>` / `airflow dags backfill -s -e`),
which ignores `catchup`. `max_active_runs=1` serialises any concurrent runs so
only ONE process writes `data/<p>.duckdb` at a time — DuckDB is single-writer, and
each BashOperator is a separate subprocess that opens and closes the file in turn.
`retries=0` and templated dates keep the run deterministic; run ids, task timings
and logs are non-deterministic by nature and nothing asserts them."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator

from orchestration.tasks import TASKS

# The repo root (dags → orchestration → repo); `make` runs here inside the image.
REPO = Path(__file__).resolve().parents[2]

with DAG(
    dag_id="pipeline",
    schedule="@daily",
    start_date=datetime(2026, 1, 6),
    catchup=False,  # never auto-catch-up-to-now; backfill is explicit (Amendment 2)
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
