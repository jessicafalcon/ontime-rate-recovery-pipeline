"""Phase 8b (specs/phase-8b-airflow-dag.md): the DAG is `make pipeline` minus the
scheduler, with no logic and the safe scheduling config (Done-when 1, invariants
1 and 6; Amendments 1–2).

**Review-cap re-implementation (rounds 1–3).** Substring/AST scans were each one
notch weaker than the invariant they named (presence vs value; `>>` exists vs
direction; paused-by-env vs pinned). This loads `pipeline_dag.py` offline with a
**stubbed airflow** and asserts the REAL DAG object: config VALUES, task order,
edge DIRECTION, operator kwargs — exactly. No real airflow (Docker-only): the
stub provides just `DAG` and `BashOperator`, so any other operator / TaskFlow
import fails the load (a positive pin on "no logic"). The container
`test_int_airflow.py::test_dag_edges_and_operators` corroborates against a real
Airflow at runtime."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestration import tasks

ROOT = Path(__file__).parent.parent
DAG_FILE = ROOT / "orchestration" / "dags" / "pipeline_dag.py"

PIPELINE_WRITING_STEPS = ["dbt_build", "writeback"]


class _StubOp:
    """A BashOperator stand-in that records its kwargs and `>>` edges."""

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.task_id = kw.get("task_id")
        self.downstream: list[_StubOp] = []
        self.upstream: list[_StubOp] = []

    def __rshift__(self, other: _StubOp) -> _StubOp:
        self.downstream.append(other)
        other.upstream.append(self)
        return other  # so `a >> b >> c` chains


class _StubDAG:
    def __init__(self, **kw: Any) -> None:
        self.kw = kw

    def __enter__(self) -> _StubDAG:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _load_dag() -> tuple[_StubDAG, list[_StubOp]]:
    """Exec pipeline_dag.py with a stubbed airflow; return the DAG stub and its
    operator stubs (in construction order). Only `DAG`/`BashOperator` exist, so a
    PythonOperator / `@task` import would raise here — a pin on "no logic"."""
    airflow = types.ModuleType("airflow")
    airflow.DAG = _StubDAG  # type: ignore[attr-defined]
    operators = types.ModuleType("airflow.operators")
    bash = types.ModuleType("airflow.operators.bash")
    bash.BashOperator = _StubOp  # type: ignore[attr-defined]
    operators.bash = bash  # type: ignore[attr-defined]
    airflow.operators = operators  # type: ignore[attr-defined]
    names = ("airflow", "airflow.operators", "airflow.operators.bash")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update(
        {
            "airflow": airflow,
            "airflow.operators": operators,
            "airflow.operators.bash": bash,
        }
    )
    try:
        spec = importlib.util.spec_from_file_location(
            "_pipeline_dag_undertest", DAG_FILE
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.dag, list(mod.steps)
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


def test_dag_tasks_are_the_pipeline_writing_steps_in_order() -> None:
    """The manifest's ordered task ids and commands ARE make pipeline's writing
    steps — dbt build → write-back — eval excluded (Amendment 1); PROFILE is pinned
    to tiny (not self-referential)."""
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
    — data_interval_END, single-quoted, not a date we compute."""
    assert tasks.THROUGH_TEMPLATE == "{{ data_interval_end | ds }}"
    dbt_build_cmd = dict(tasks.TASKS)["dbt_build"]
    assert "THROUGH='{{ data_interval_end | ds }}'" in dbt_build_cmd
    assert "now(" not in dbt_build_cmd


def test_dag_object_config_is_the_safe_scheduling() -> None:
    """The real DAG object's config VALUES: dag_id, `@daily` (so intervals are
    spaced ≤ lookback_days — invariant 5), `catchup=False` + `is_paused_upon_creation`
    (Amendment 2's two safety legs), `max_active_runs=1` (single-writer),
    `start_date` fixed (no clock). Flipping any value fails here (rounds 2–3 #2/#3)."""
    dag, _ = _load_dag()
    assert dag.kw["dag_id"] == "pipeline"
    assert dag.kw["schedule"] == "@daily"
    assert dag.kw["catchup"] is False
    assert dag.kw["is_paused_upon_creation"] is True
    assert dag.kw["max_active_runs"] == 1
    assert dag.kw["start_date"] == datetime(2026, 1, 6)


def test_dag_object_tasks_edges_and_operator_kwargs() -> None:
    """The real DAG object: two BashOperator tasks in order, each with the manifest
    command and `cwd` = the repo root (exact value), and the edge dbt_build →
    writeback with DIRECTION (reversing it fails here; round 3 #1/#4)."""
    _, steps = _load_dag()
    assert [op.task_id for op in steps] == PIPELINE_WRITING_STEPS
    for op in steps:
        assert isinstance(op, _StubOp)
        assert op.kw["cwd"] == str(ROOT)
    assert {op.task_id: op.kw["bash_command"] for op in steps} == dict(tasks.TASKS)
    by_id = {op.task_id: op for op in steps}
    assert [d.task_id for d in by_id["dbt_build"].downstream] == ["writeback"]
    assert [u.task_id for u in by_id["writeback"].upstream] == ["dbt_build"]
    assert by_id["dbt_build"].upstream == []  # dbt_build is the source
    assert by_id["writeback"].downstream == []  # writeback is the sink
