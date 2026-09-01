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

ROOT = Path(__file__).resolve().parent.parent  # resolve: match the DAG's REPO (#6)
DAG_FILE = ROOT / "orchestration" / "dags" / "pipeline_dag.py"

PIPELINE_WRITING_STEPS = ["dbt_build", "writeback"]


class _StubOp:
    """A BashOperator stand-in that records its kwargs and `>>` edges. Every
    instance is registered so the test can assert the TOTAL operator count — a
    third operator built anywhere (not only in `steps`) is caught offline (#1).
    (DAG↔task attachment itself — an op attached to a different dag — is the
    container test's job; the stub does not model Airflow's registration.)"""

    instances: list[_StubOp] = []

    def __init__(self, **kw: Any) -> None:
        _StubOp.instances.append(self)
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


def _stub_airflow_modules() -> tuple[types.ModuleType, ...]:
    """The three stub modules a DAG load needs: only `DAG`/`BashOperator` exist, so
    any other operator / TaskFlow import fails the load (a positive pin on "no
    logic")."""
    airflow = types.ModuleType("airflow")
    airflow.DAG = _StubDAG  # type: ignore[attr-defined]
    operators = types.ModuleType("airflow.operators")
    bash = types.ModuleType("airflow.operators.bash")
    bash.BashOperator = _StubOp  # type: ignore[attr-defined]
    operators.bash = bash  # type: ignore[attr-defined]
    airflow.operators = operators  # type: ignore[attr-defined]
    return airflow, operators, bash


def _load_dag() -> tuple[_StubDAG, list[_StubOp]]:
    """Exec pipeline_dag.py with a stubbed airflow; return the DAG stub and its
    operator stubs (in construction order). Only `DAG`/`BashOperator` exist, so a
    PythonOperator / `@task` import would raise here — a pin on "no logic"."""
    airflow, operators, bash = _stub_airflow_modules()
    _StubOp.instances = []  # reset the registry for this load (#1)
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
        f"make dbt-build PROFILE={tasks.PROFILE} TARGET={tasks.TARGET} "
        f"THROUGH='{tasks.THROUGH_TEMPLATE}'"
    )
    assert tasks.TARGET == "duckdb"  # the Docker-local DAG; Composer is Phase 11
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
    assert dag.kw["default_args"] == {"retries": 0}  # determinism lever (#2)


def test_dag_object_tasks_edges_and_operator_kwargs() -> None:
    """The real DAG object: two BashOperator tasks in order, each with the manifest
    command and `cwd` = the repo root (exact value), and the edge dbt_build →
    writeback with DIRECTION (reversing it fails here; round 3 #1/#4)."""
    _, steps = _load_dag()
    # exactly two operators constructed in total — a third built anywhere fails (#1)
    assert len(_StubOp.instances) == 2
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


# --- Phase 12 (specs/phase-12-live-run.md): the DAG parses in both layouts, and
# --- its cloud target is env-driven config (invariants 1–2). ---


def test_dag_imports_in_flat_bucket_layout(tmp_path: Path) -> None:
    """Invariant 1: the DAG imports TASKS under the flat Composer `dags/` bucket,
    where only `dags/` is on sys.path and the `orchestration` package does not
    resolve — the dual-path import falls back to the flat `import tasks` (BACKLOG
    row 47). Simulated by copying both files flat, putting that dir on sys.path,
    and blocking `orchestration` in sys.modules (a `None` entry → ImportError,
    the same class ModuleNotFoundError subclasses on a real worker)."""
    flat = tmp_path / "dags"
    flat.mkdir()
    (flat / "tasks.py").write_text((ROOT / "orchestration" / "tasks.py").read_text())
    dag_copy = flat / "pipeline_dag.py"
    dag_copy.write_text(DAG_FILE.read_text())

    airflow, operators, bash = _stub_airflow_modules()
    names = (
        "airflow",
        "airflow.operators",
        "airflow.operators.bash",
        "orchestration",
        "orchestration.tasks",
        "tasks",
    )
    saved = {n: sys.modules.get(n) for n in names}
    sys.path.insert(0, str(flat))
    try:
        sys.modules.update(
            {
                "airflow": airflow,
                "airflow.operators": operators,
                "airflow.operators.bash": bash,
                # the worker has no `orchestration` package: block it so the
                # dual-path import must fall back to the flat `import tasks`
                "orchestration": None,  # type: ignore[assignment]
                "orchestration.tasks": None,  # type: ignore[assignment]
            }
        )
        sys.modules.pop("tasks", None)  # let the flat copy win
        _StubOp.instances = []
        spec = importlib.util.spec_from_file_location("_flat_pipeline_dag", dag_copy)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # must NOT raise (the fallback resolved TASKS)
        assert [op.task_id for op in mod.steps] == PIPELINE_WRITING_STEPS
    finally:
        sys.path.remove(str(flat))
        sys.modules.pop("_flat_pipeline_dag", None)
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


def test_tasks_default_is_local_duckdb() -> None:
    """Invariant 2 (unset default): `build_tasks("duckdb", "")` renders the
    committed local list byte-for-byte — no PROJECT, no CONFIRM — so
    `test-int-airflow` and the offline structure test are unchanged. The module's
    own TASKS (env unset in the test process) IS that list."""
    assert tasks.build_tasks("duckdb", "") == [
        (
            "dbt_build",
            f"make dbt-build PROFILE=tiny TARGET=duckdb "
            f"THROUGH='{tasks.THROUGH_TEMPLATE}'",
        ),
        ("writeback", "make writeback PROFILE=tiny"),
    ]
    assert tasks.TARGET == "duckdb"
    assert tasks.TASKS == tasks.build_tasks("duckdb", "")


def test_tasks_render_cloud_target_from_env() -> None:
    """Invariant 2 (set-cloud): a cloud target renders the two cloud `make`
    commands — build on the warehouse, write-back to spanner — each `make`-only,
    PROJECT single-quoted, CONFIRM on the command line (command-line origin inside
    the BashOperator). No non-`make` token leaks in."""
    rendered = tasks.build_tasks("bigquery", "ontime-rate-recovery")
    assert rendered == [
        (
            "dbt_build",
            "make dbt-build PROFILE=tiny TARGET=bigquery "
            "PROJECT='ontime-rate-recovery' CONFIRM=yes "
            f"THROUGH='{tasks.THROUGH_TEMPLATE}'",
        ),
        (
            "writeback",
            "make writeback PROFILE=tiny TARGET=spanner "
            "PROJECT='ontime-rate-recovery' CONFIRM=yes",
        ),
    ]
    for _task_id, cmd in rendered:
        assert cmd.startswith("make ")


def test_module_target_and_project_come_from_env(monkeypatch: Any) -> None:
    """Invariant 2 (the env wiring): the module-level TARGET/PROJECT are read from
    OTR_DAG_TARGET/OTR_DAG_PROJECT, and TASKS is build_tasks of them — so setting
    the env on the Docker rehearsal / Composer run points the one DAG at the
    cloud. Reloaded back to the unset default in the finally so other tests see
    the committed local list."""
    import importlib as _importlib

    monkeypatch.setenv("OTR_DAG_TARGET", "bigquery")
    monkeypatch.setenv("OTR_DAG_PROJECT", "ontime-rate-recovery")
    try:
        reloaded = _importlib.reload(tasks)
        assert reloaded.TARGET == "bigquery"
        assert reloaded.PROJECT == "ontime-rate-recovery"
        proj = "ontime-rate-recovery"
        assert reloaded.TASKS == reloaded.build_tasks("bigquery", proj)
    finally:
        monkeypatch.delenv("OTR_DAG_TARGET", raising=False)
        monkeypatch.delenv("OTR_DAG_PROJECT", raising=False)
        _importlib.reload(tasks)  # restore the unset default for the rest of the suite
