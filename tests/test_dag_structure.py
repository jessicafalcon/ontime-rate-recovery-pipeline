"""Phase 8b (specs/phase-8b-airflow-dag.md): the DAG is `make pipeline` minus the
scheduler, with no logic (Done-when 1, invariants 1 and 6).

Offline and Airflow-free: it imports the `orchestration.tasks` manifest (pure
Python) and text-scans `orchestration/dags/pipeline_dag.py` — it never imports
airflow, because apache-airflow is Docker-only and absent from the venv. The
container proof that the DAG RUN equals `make pipeline` is
`tests/integration/test_int_airflow.py`."""

from __future__ import annotations

from pathlib import Path

from orchestration import tasks

ROOT = Path(__file__).parent.parent
DAG_FILE = ROOT / "orchestration" / "dags" / "pipeline_dag.py"

# make pipeline (serving.cli.pipeline) runs dbt build → eval → write-back; the DAG
# runs its two WRITING steps (eval is a union-only gate, Amendment 1).
PIPELINE_WRITING_STEPS = ["dbt_build", "writeback"]


def test_dag_tasks_are_the_pipeline_writing_steps_in_order() -> None:
    """The manifest's ordered task ids and commands ARE make pipeline's writing
    steps — dbt build → write-back — the build carrying the interval's THROUGH;
    eval is excluded (it reads truth and asserts full-data pins — Amendment 1)."""
    assert [task_id for task_id, _ in tasks.TASKS] == PIPELINE_WRITING_STEPS
    commands = {task_id: cmd for task_id, cmd in tasks.TASKS}
    assert commands["dbt_build"] == (
        f"make dbt-build PROFILE={tasks.PROFILE} THROUGH={tasks.THROUGH_TEMPLATE}"
    )
    assert commands["writeback"] == f"make writeback PROFILE={tasks.PROFILE}"
    assert "eval" not in commands


def test_through_token_is_data_interval_end() -> None:
    """The interval reaches THROUGH only as a literal Jinja token Airflow renders
    — data_interval_END (the window's close), not a date we compute."""
    assert tasks.THROUGH_TEMPLATE == "{{ data_interval_end | ds }}"
    dbt_build_cmd = dict(tasks.TASKS)["dbt_build"]
    assert "{{ data_interval_end | ds }}" in dbt_build_cmd
    assert "now(" not in dbt_build_cmd


def test_dag_uses_only_bash_operators_and_no_python_callable() -> None:
    """No logic: every operator is a BashOperator, no Python task runs — so the
    only computation is Airflow's own templating."""
    text = DAG_FILE.read_text()
    assert "from orchestration.tasks import TASKS" in text
    assert "BashOperator" in text
    for forbidden in ("PythonOperator", "python_callable", "@task", "datetime.now"):
        assert forbidden not in text, forbidden


def test_dag_serialises_writes() -> None:
    """max_active_runs=1 — catchup runs one interval at a time, so only one
    process writes data/<p>.duckdb (DuckDB is single-writer)."""
    text = DAG_FILE.read_text()
    assert "max_active_runs=1" in text
    assert "catchup=True" in text
