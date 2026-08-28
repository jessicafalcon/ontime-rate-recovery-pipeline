"""Phase 8b (specs/phase-8b-airflow-dag.md): the DAG is `make pipeline` minus the
scheduler, with no logic and the safe scheduling config (Done-when 1, invariants
1 and 6; Amendment 2).

Offline and Airflow-free: it imports the `orchestration.tasks` manifest (pure
Python) and **parses `orchestration/dags/pipeline_dag.py` with `ast`** — asserting
the actual `DAG(...)` kwarg VALUES (`catchup`, `max_active_runs`, `start_date`)
and operator structure, not substring matches (round 2: a substring scan let
`catchup=True`/`=16`/`"medium"` survive). It never imports airflow, which is
Docker-only. The authoritative RUNTIME structural pin — real task set, the
`dbt_build → writeback` edge, BashOperator-only — is
`tests/integration/test_int_airflow.py::test_dag_edges_and_operators`."""

from __future__ import annotations

import ast
from pathlib import Path

from orchestration import tasks

ROOT = Path(__file__).parent.parent
DAG_FILE = ROOT / "orchestration" / "dags" / "pipeline_dag.py"

# make pipeline (serving.cli.pipeline) runs dbt build → eval → write-back; the DAG
# runs its two WRITING steps (eval is a union-only gate, Amendment 1).
PIPELINE_WRITING_STEPS = ["dbt_build", "writeback"]


def _func_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _tree() -> ast.Module:
    return ast.parse(DAG_FILE.read_text())


def _calls(name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call) and _func_name(n.func) == name
    ]


def _dag_kwargs() -> dict[str, ast.expr]:
    dag_calls = _calls("DAG")
    assert len(dag_calls) == 1, "expected exactly one DAG(...) call"
    return {kw.arg: kw.value for kw in dag_calls[0].keywords if kw.arg}


def test_dag_tasks_are_the_pipeline_writing_steps_in_order() -> None:
    """The manifest's ordered task ids and commands ARE make pipeline's writing
    steps — dbt build → write-back — the build carrying the interval's THROUGH;
    eval is excluded (Amendment 1). PROFILE is pinned to tiny (not self-referential)."""
    assert tasks.PROFILE == "tiny"
    assert [task_id for task_id, _ in tasks.TASKS] == PIPELINE_WRITING_STEPS
    commands = {task_id: cmd for task_id, cmd in tasks.TASKS}
    assert commands["dbt_build"] == (
        f"make dbt-build PROFILE={tasks.PROFILE} THROUGH='{tasks.THROUGH_TEMPLATE}'"
    )
    assert commands["writeback"] == f"make writeback PROFILE={tasks.PROFILE}"
    assert "eval" not in commands


def test_through_token_is_data_interval_end() -> None:
    """The interval reaches THROUGH only as a literal Jinja token Airflow renders
    — data_interval_END (the window's close), single-quoted, not a date we compute."""
    assert tasks.THROUGH_TEMPLATE == "{{ data_interval_end | ds }}"
    dbt_build_cmd = dict(tasks.TASKS)["dbt_build"]
    assert "THROUGH='{{ data_interval_end | ds }}'" in dbt_build_cmd
    assert "now(" not in dbt_build_cmd


def test_dag_config_pins_the_safe_scheduling() -> None:
    """dag_id, catchup=False (Amendment 2 — no auto-catch-up-to-now), and
    max_active_runs=1 (single-writer on data/<p>.duckdb) are asserted by their
    PARSED values, so flipping any literal fails here (round 2 #3/#4)."""
    kw = _dag_kwargs()
    assert isinstance(kw["dag_id"], ast.Constant) and kw["dag_id"].value == "pipeline"
    assert isinstance(kw["catchup"], ast.Constant) and kw["catchup"].value is False
    assert (
        isinstance(kw["max_active_runs"], ast.Constant)
        and kw["max_active_runs"].value == 1
    )
    # start_date is a real datetime(...) constant, not now()/today() (round 2 #22)
    assert "start_date" in kw
    assert isinstance(kw["start_date"], ast.Call)
    assert _func_name(kw["start_date"].func) == "datetime"
    assert "schedule" in kw


def test_dag_uses_only_one_bash_operator_over_tasks() -> None:
    """No logic: exactly one operator is constructed, it is a BashOperator, it is
    built over TASKS with a cwd, and no Python task appears (round 2 #5/#6/#14)."""
    text = DAG_FILE.read_text()
    assert "from orchestration.tasks import TASKS" in text
    operator_calls = [
        n
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call) and (_func_name(n.func) or "").endswith("Operator")
    ]
    assert len(operator_calls) == 1, "exactly one operator should be constructed"
    op = operator_calls[0]
    assert _func_name(op.func) == "BashOperator"
    kw = {k.arg for k in op.keywords}
    assert {"task_id", "bash_command", "cwd"} <= kw, kw  # cwd wired (round 2 #6)
    assert "for task_id, command in TASKS" in text  # built over the manifest
    for forbidden in ("PythonOperator", "python_callable", "@task", "datetime.now"):
        assert forbidden not in text, forbidden


def test_dag_declares_dependencies() -> None:
    """The DAG wires edges — a `>>` chain or an explicit `chain(...)` /
    `cross_downstream(...)` (robust to a refactor; round 2 #20). Deleting all
    edge-wiring fails here; the ordered runtime edge is the container test."""
    tree = _tree()
    has_rshift = any(
        isinstance(n, ast.BinOp) and isinstance(n.op, ast.RShift)
        for n in ast.walk(tree)
    )
    has_chain = any(
        isinstance(n, ast.Call) and _func_name(n.func) in {"chain", "cross_downstream"}
        for n in ast.walk(tree)
    )
    assert has_rshift or has_chain, "the DAG declares no task dependencies"
