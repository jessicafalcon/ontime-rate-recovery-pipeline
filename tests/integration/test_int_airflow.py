"""Phase 8b (specs/phase-8b-airflow-dag.md): the Docker-local Airflow DAG equals
`make pipeline` and a catchup backfill equals the union — across process
boundaries (Done-when 1/3/4/5, invariants 1/4/5/6).

Behind OTR_INT (only `make test-int-airflow` exports it; CI never runs this). The
test builds a lean Airflow image (SequentialExecutor + SQLite), drives the DAG
with `airflow dags test <THROUGH>` (a synchronous run; `{{ data_interval_end |
ds }}` renders THROUGH = the arg date), copies the container's DuckDB out, and
compares BOTH tables to the frozen goldens with the SAME host helpers as every
other gate. It also asserts the real DAG object's task set, edges and operator
types (imported with airflow inside the container), and that an intermediate
interval actually lands a partial set — so the proof cannot degrade to "three
full idempotent runs"."""

from __future__ import annotations

import shlex
import subprocess
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from eval import golden
from tests import pins
from tests.test_writeback import send_schedule_hash

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "tiny"
COMPOSE_FILE = ROOT / "orchestration" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]
SVC = "airflow"
CONTAINER_DB = "/opt/otr/data/tiny.duckdb"
# THROUGH = the arg date (data_interval_end | ds); 2026-01-13 lands all 10 files.
UNION_THROUGH = pins.LATE_FILE_TINY  # "2026-01-13"


def _compose(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        COMPOSE + args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
    )


def _exec(
    bash: str, check: bool = True, timeout: int = 900
) -> subprocess.CompletedProcess[str]:
    r = _compose(["exec", "-T", SVC, "bash", "-lc", bash], timeout=timeout)
    if check and r.returncode != 0:
        raise AssertionError(
            f"container exec failed ({r.returncode}): {bash}\n"
            f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-2000:]}"
        )
    return r


@pytest.fixture(scope="module")
def airflow_container() -> Iterator[None]:
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
        down = _compose(["down", "-v"], timeout=180)
        if down.returncode != 0:  # a failed teardown leaks a container (round 2 #17)
            warnings.warn(
                f"container teardown failed ({down.returncode}) — may leak "
                f"otr-airflow-8b: {down.stderr[-500:]}",
                stacklevel=2,
            )


def _reset_db() -> None:
    _exec("rm -f /opt/otr/data/*.duckdb /opt/otr/data/*.wal 2>/dev/null; true")


def _run_dag(through: str) -> None:
    """One synchronous dag run whose data_interval_end (= THROUGH) is `through`."""
    _exec(f"airflow dags test pipeline {through}")


def _pull_db(tmp_path: Path) -> Path:
    dst = tmp_path / "tiny.duckdb"
    cp = _compose(["cp", f"{SVC}:{CONTAINER_DB}", str(dst)], timeout=120)
    assert cp.returncode == 0, cp.stderr
    return dst


def _query(sql: str) -> str:
    """Scalar query against the container's DuckDB via the project venv — opened
    READ-ONLY, with the SQL passed as argv (not interpolated into the shell string;
    round 2 #21). Returns the result's last stdout line."""
    py = (
        "import duckdb,sys;"
        "print(duckdb.connect(sys.argv[1], read_only=True)"
        ".execute(sys.argv[2]).fetchone()[0])"
    )
    out = _exec(
        f"uv run python -c {shlex.quote(py)} {CONTAINER_DB} {shlex.quote(sql)}"
    ).stdout
    return out.strip().splitlines()[-1]


def _assert_scores_match_golden(db: Path) -> None:
    built = golden.export_rows(db, golden.SCORES_SEND_TIME)
    frozen = golden.parse(
        (FIXTURES / golden.SCORES_SEND_TIME.file).read_text(), golden.SCORES_SEND_TIME
    )
    assert golden.diff_rows(built, frozen, golden.SCORES_SEND_TIME.key_width) == []


def test_dag_edges_and_operators(airflow_container: None) -> None:
    """Invariant 1 at runtime: the real DAG object has exactly {dbt_build,
    writeback}, the edge dbt_build → writeback, and only BashOperators — imported
    with airflow inside the container (the offline test can only text-scan)."""
    snippet = (
        "from airflow.models.dagbag import DagBag;"
        "d=DagBag('/opt/otr/orchestration/dags',include_examples=False).get_dag('pipeline');"
        "tids=set(t.task_id for t in d.tasks);"
        "wb=d.get_task('writeback');db=d.get_task('dbt_build');"
        "ok=(tids=={'dbt_build','writeback'} and wb.upstream_task_ids=={'dbt_build'} "
        "and db.downstream_task_ids=={'writeback'} "
        "and all(type(t).__name__=='BashOperator' for t in d.tasks));"
        "print('EDGES_OK' if ok else 'EDGES_BAD:'+repr(sorted(tids))"
        "+'/'+repr(sorted(wb.upstream_task_ids)))"
    )
    out = _exec(f'python -c "{snippet}"').stdout
    assert "EDGES_OK" in out, out


def test_image_has_no_secrets(airflow_container: None) -> None:
    """Round 2 #1/#2: `COPY . /opt/otr` must bake no secret file and no terraform
    cache — `.dockerignore` (with `**/`-anchored patterns) excludes them. Assert
    the BUILT image carries none (the pin the .dockerignore lacked)."""
    r = _exec(
        r"find /opt/otr \( -name '.env' -o -name '*.tfvars' -o -name '*.tfstate*' "
        r"-o -name '*-key.json' -o -name 'service-account*.json' -o -name '.terraform' "
        r"\) -print 2>/dev/null; true"
    )
    assert r.stdout.strip() == "", (
        f"secrets/terraform baked into the image:\n{r.stdout}"
    )


def test_dag_run_matches_make_pipeline(airflow_container: None, tmp_path: Path) -> None:
    """A single union DAG run (THROUGH=2026-01-13) produces BOTH scores_send_time
    and send_schedule byte-identical to make pipeline's (the frozen golden and the
    pinned hash)."""
    _reset_db()
    _run_dag(UNION_THROUGH)
    db = _pull_db(tmp_path)
    _assert_scores_match_golden(
        db
    )  # Done-when 3: scores_send_time too, not only send_schedule
    assert send_schedule_hash(db) == pins.SEND_SCHEDULE_SHA256_TINY


def test_catchup_backfill_equals_union(airflow_container: None, tmp_path: Path) -> None:
    """Three interval runs (THROUGH 2026-01-07, 2026-01-12, 2026-01-13) into the
    container's incremental DB land the union — the DAG-level analogue of
    tests/test_backfill.py, across process boundaries. The first interval is
    asserted PARTIAL, so a token rendering empty (or a dropped --through) fails
    here instead of degrading to 'three full idempotent runs'."""
    _reset_db()
    first, *rest = pins.BACKFILL_THROUGHS_TINY  # 2026-01-07
    _run_dag(first)
    # THROUGH reached the loader: the first landing is a strict subset.
    assert (
        _query("select max(cast(server_upload_time as date))::varchar from raw.events")
        <= first
    )
    assert int(_query("select count(*) from raw.events")) < pins.RAW_EVENT_ROWS
    for through in rest:
        _run_dag(through)
    db = _pull_db(tmp_path)
    _assert_scores_match_golden(db)
    assert send_schedule_hash(db) == pins.SEND_SCHEDULE_SHA256_TINY


def test_catchup_runs_green(airflow_container: None) -> None:
    """The three-interval catchup completes all tasks green (each BashOperator a
    separate subprocess on data/<p>.duckdb) — the single-writer hand-off. The
    final send_schedule has one row per scored user."""
    _reset_db()
    for through in pins.BACKFILL_THROUGHS_TINY:
        _run_dag(through)  # _exec check=True → a failed task raises here
    assert int(_query("select count(*) from serving.send_schedule")) == (
        pins.SEND_SCHEDULE_ROWS_TINY
    )
