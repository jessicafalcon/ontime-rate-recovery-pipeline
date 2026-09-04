"""fix/composer-cosmos (invariant 5): astronomer-cosmos and the k8s provider are
Cloud-Composer `pypi_packages` ONLY — never in uv.lock, never the repo venv. The
offline suite stubs them; a lock entry would mean they leaked into the project."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSER_ONLY = ("astronomer-cosmos", "apache-airflow-providers-cncf-kubernetes")


def test_composer_only_packages_absent_from_lock() -> None:
    lock = (ROOT / "uv.lock").read_text().lower()
    for pkg in COMPOSER_ONLY:
        assert f'name = "{pkg}"' not in lock, f"{pkg} leaked into uv.lock"


def test_apache_airflow_stays_docker_only() -> None:
    """apache-airflow itself is Docker/Composer-only (the standing Phase 8b rule)
    — re-asserted here so a Cosmos-driven change cannot pull it into the lock."""
    lock = (ROOT / "uv.lock").read_text().lower()
    assert 'name = "apache-airflow"' not in lock
