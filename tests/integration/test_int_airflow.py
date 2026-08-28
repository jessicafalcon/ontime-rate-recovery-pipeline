"""Phase 8b (specs/phase-8b-airflow-dag.md): the Docker-local Airflow DAG equals
`make pipeline` and a catchup backfill equals the union — across process
boundaries (Done-when 1/3/4/5, invariants 4/5/6).

Behind OTR_INT (only `make test-int-airflow` exports it; CI never runs this). The
test builds a lean Airflow image (SequentialExecutor + SQLite), drives the DAG
with `airflow dags test <THROUGH>` (a synchronous run; `{{ data_interval_end |
ds }}` renders THROUGH = the arg date), copies the container's DuckDB out, and
hashes `send_schedule` with the SAME host helper as every other gate — so a match
against SEND_SCHEDULE_SHA256_TINY is the cross-process proof the scheduler-ordered
chain reproduces `make pipeline`'s table. Each `airflow dags test` runs the
BashOperators as separate subprocesses, so a green catchup demonstrates the
single-writer hand-off on data/<p>.duckdb (max_active_runs=1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests import pins
from tests.test_writeback import send_schedule_hash

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "orchestration" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]
SVC = "airflow"
CONTAINER_DB = "/opt/otr/data/tiny.duckdb"
# THROUGH = the arg date (data_interval_end | ds); 2026-01-13 lands all 10 files.
UNION_THROUGH = pins.LATE_FILE_TINY  # "2026-01-13"


def _compose(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        COMPOSE + args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
    )


def _exec(
    bash: str, check: bool = True, timeout: int = 900
) -> subprocess.CompletedProcess:
    r = _compose(["exec", "-T", SVC, "bash", "-lc", bash], timeout=timeout)
    if check and r.returncode != 0:
        raise AssertionError(
            f"container exec failed ({r.returncode}): {bash}\n"
            f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-2000:]}"
        )
    return r


@pytest.fixture(scope="module")
def airflow_container() -> None:
    build = _compose(["build"], timeout=1800)
    assert build.returncode == 0, build.stderr[-4000:]
    up = _compose(["up", "-d"], timeout=300)
    assert up.returncode == 0, up.stderr[-2000:]
    try:
        # Deterministic readiness: block on migrate (idempotent), then confirm the
        # DAG parses with no import error.
        _exec("airflow db migrate", timeout=300)
        errs = _exec("airflow dags list-import-errors")
        assert "pipeline_dag" not in errs.stdout, errs.stdout
        yield
    finally:
        _compose(["down", "-v"], timeout=180)


def _reset_db() -> None:
    _exec("rm -f /opt/otr/data/*.duckdb /opt/otr/data/*.wal 2>/dev/null; true")


def _run_dag(through: str) -> None:
    """One synchronous dag run whose data_interval_end (= THROUGH) is `through`."""
    _exec(f"airflow dags test pipeline {through}")


def _container_send_schedule_hash(tmp_path: Path) -> str:
    dst = tmp_path / "tiny.duckdb"
    cp = _compose(["cp", f"{SVC}:{CONTAINER_DB}", str(dst)], timeout=120)
    assert cp.returncode == 0, cp.stderr
    return send_schedule_hash(dst)


def test_dag_run_matches_make_pipeline(airflow_container: None, tmp_path: Path) -> None:
    """A single union DAG run (THROUGH=2026-01-13) produces send_schedule
    byte-identical to make pipeline's (the pinned hash)."""
    _reset_db()
    _run_dag(UNION_THROUGH)
    assert _container_send_schedule_hash(tmp_path) == pins.SEND_SCHEDULE_SHA256_TINY


def test_catchup_backfill_equals_union(airflow_container: None, tmp_path: Path) -> None:
    """Three interval runs (THROUGH 2026-01-07, 2026-01-12, 2026-01-13) into the
    container's incremental DB land the union send_schedule — the DAG-level
    analogue of tests/test_backfill.py, across process boundaries."""
    _reset_db()
    for through in pins.BACKFILL_THROUGHS_TINY:
        _run_dag(through)
    assert _container_send_schedule_hash(tmp_path) == pins.SEND_SCHEDULE_SHA256_TINY


def test_catchup_runs_green(airflow_container: None) -> None:
    """The three-interval catchup completes all tasks green (each BashOperator a
    separate subprocess on data/<p>.duckdb) — the single-writer hand-off. The
    final send_schedule has one row per scored user."""
    _reset_db()
    for through in pins.BACKFILL_THROUGHS_TINY:
        _run_dag(through)  # _exec check=True → a failed task raises here
    out = _exec(
        'uv run python -c "import duckdb;'
        f"print(duckdb.connect('{CONTAINER_DB}').execute("
        "'select count(*) from serving.send_schedule').fetchone()[0])\""
    ).stdout
    assert out.strip().splitlines()[-1] == str(pins.SEND_SCHEDULE_ROWS_TINY)
