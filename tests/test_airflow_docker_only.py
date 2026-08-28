"""Phase 8b (specs/phase-8b-airflow-dag.md): apache-airflow is Docker-only
(Done-when 6, invariant 7).

The allowlist admits apache-airflow for Phase 8 **via Docker only** — it must
never enter the venv or uv.lock, so `make test`/CI never depend on it and the
offline tests import no airflow. The DAG is exercised only inside the container,
behind OTR_INT."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_apache_airflow_not_in_uv_lock() -> None:
    lock = (ROOT / "uv.lock").read_text()
    assert 'name = "apache-airflow"' not in lock
    assert 'name = "apache-airflow-core"' not in lock


def test_apache_airflow_not_a_project_dependency() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "apache-airflow" not in pyproject
