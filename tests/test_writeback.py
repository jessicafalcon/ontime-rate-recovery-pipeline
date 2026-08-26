"""Phase 8a (specs/phase-8a-write-back.md): the write-back to serving.send_schedule.

A real dbt build of fixtures/tiny into a tmp DuckDB, in-process, then the
write-back. Replace-iff-greater on the row's own (model_version, computed_as_of);
idempotent (a second run writes 0); the nine §2.9 columns with tz from the open
dim_user row and written_at = computed_as_of; the boundary (reads only
scores_send_time + dim_user_current, never raw/side-file). No service, no
network."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from eval import golden
from loader import load as loader
from serving import writeback as wb
from tests import pins

ROOT = Path(__file__).parent.parent
DBT = ROOT / "dbt"

SEND_SCHEDULE_GOLDEN = golden.Golden(
    relation="serving.send_schedule",
    columns=(
        "user_id",
        "cohort_id",
        "send_hour_local",
        "send_minute_local",
        "tz",
        "confidence",
        "model_version",
        "computed_as_of",
        "written_at",
    ),
    key_width=1,
    file="expected/send_schedule.csv",
)


def build_tiny(db: Path) -> None:
    """loader.load + a full `dbt run` of fixtures/tiny into `db` (models only —
    every model, so scores_send_time and dim_user_current exist)."""
    os.environ.setdefault("DO_NOT_TRACK", "1")
    from dbt.cli.main import dbtRunner

    loader.load("tiny", db)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OTR_DUCKDB_PATH", str(db))
        res = dbtRunner().invoke(
            [
                "run",
                "--project-dir",
                str(DBT),
                "--profiles-dir",
                str(DBT),
                "--target",
                "duckdb",
                "--quiet",
                "--target-path",
                str(db.parent / "t"),
            ]
        )
    assert res.success, "dbt run failed"


def send_schedule_hash(db: Path) -> str:
    rows = golden.export_rows(db, SEND_SCHEDULE_GOLDEN)
    return hashlib.sha256(
        golden.render(rows, SEND_SCHEDULE_GOLDEN).encode()
    ).hexdigest()


def rows_of(db: Path):
    con = duckdb.connect(str(db))
    try:
        return con.execute(
            "select user_id, cohort_id, send_hour_local, send_minute_local, tz, "
            "confidence, model_version, computed_as_of, written_at "
            "from serving.send_schedule order by user_id"
        ).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A built tiny db with the write-back applied once."""
    db = tmp_path_factory.mktemp("wb") / "tiny.duckdb"
    build_tiny(db)
    cand, written = wb.write_back("tiny", db)
    assert (cand, written) == (
        pins.SEND_SCHEDULE_ROWS_TINY,
        pins.SEND_SCHEDULE_ROWS_TINY,
    )
    return db


def test_writeback_populates_and_matches_pin(built: Path) -> None:
    con = duckdb.connect(str(built))
    try:
        n = con.execute("select count(*) from serving.send_schedule").fetchone()[0]
    finally:
        con.close()
    assert n == pins.SEND_SCHEDULE_ROWS_TINY
    assert send_schedule_hash(built) == pins.SEND_SCHEDULE_SHA256_TINY


def test_writeback_twice_is_a_noop(built: Path) -> None:
    before = send_schedule_hash(built)
    cand, written = wb.write_back("tiny", built)
    assert (cand, written) == (
        pins.SEND_SCHEDULE_ROWS_TINY,
        0,
    )  # nothing strictly greater
    assert send_schedule_hash(built) == before


def test_send_schedule_has_the_nine_columns(built: Path) -> None:
    con = duckdb.connect(str(built))
    try:
        cols = [
            r[0]
            for r in con.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'serving' and table_name = 'send_schedule' "
                "order by ordinal_position"
            ).fetchall()
        ]
    finally:
        con.close()
    assert tuple(cols) == SEND_SCHEDULE_GOLDEN.columns  # §2.9, in order


def test_written_at_equals_computed_as_of(built: Path) -> None:
    assert all(r[7] == r[8] for r in rows_of(built))  # written_at == computed_as_of


def test_tz_is_the_open_dim_user_row(built: Path) -> None:
    con = duckdb.connect(str(built))
    try:
        open_tz = dict(
            con.execute(
                "select user_id, tz from raw.dim_user where valid_to is null"
            ).fetchall()
        )
        served = con.execute("select user_id, tz from serving.send_schedule").fetchall()
    finally:
        con.close()
    assert served, "send_schedule empty"
    assert all(tz == open_tz[uid] for uid, tz in served)
    # a tz-change user carries its CURRENT zone, not an event-time zone
    changed = [u for u in open_tz if u in ("u-000008", "u-000010")]
    assert changed and all(dict(served)[u] == open_tz[u] for u in changed)


def test_should_replace_is_strict() -> None:
    def cand(version: str, when: datetime) -> wb.Candidate:
        return wb.Candidate("u", "c", 8, 0, "UTC", 0.5, version, when)

    t0 = datetime(2026, 1, 12, 0, 47)
    t1 = datetime(2026, 1, 13, 0, 0)
    assert wb.should_replace(cand("v1", t0), None) is True  # absent → insert
    assert wb.should_replace(cand("v1", t1), ("v1", t0)) is True  # later as_of
    assert wb.should_replace(cand("v1", t0), ("v1", t0)) is False  # tie → no-op
    assert wb.should_replace(cand("v1", t0), ("v1", t1)) is False  # older → keep newer
    assert wb.should_replace(cand("v2", t0), ("v1", t1)) is True  # greater version


@pytest.fixture
def fresh(tmp_path: Path) -> Path:
    """An own-build db with the write-back applied — for tests that mutate rows."""
    db = tmp_path / "tiny.duckdb"
    build_tiny(db)
    wb.write_back("tiny", db)
    return db


def test_replace_only_on_strictly_greater(fresh: Path) -> None:
    """Seed a user with an older and another with a newer pair; the write-back
    replaces only the older."""
    con = duckdb.connect(str(fresh))
    try:
        con.execute(
            "delete from serving.send_schedule where user_id in ('u-000001','u-000002')"
        )
        # u-000001: stale (older computed_as_of) → will be replaced
        con.execute(
            "insert into serving.send_schedule values "
            "('u-000001','c',0,0,'UTC',0.0,'v1',"
            "timestamp '2020-01-01 00:00:00',timestamp '2020-01-01 00:00:00')"
        )
        # u-000002: fresher (year 2999) → must NOT be replaced
        con.execute(
            "insert into serving.send_schedule values "
            "('u-000002','c',0,0,'UTC',0.0,'v1',"
            "timestamp '2999-01-01 00:00:00',timestamp '2999-01-01 00:00:00')"
        )
    finally:
        con.close()
    wb.write_back("tiny", fresh)
    con = duckdb.connect(str(fresh))
    try:
        got = dict(
            con.execute(
                "select user_id, send_hour_local from serving.send_schedule "
                "where user_id in ('u-000001','u-000002')"
            ).fetchall()
        )
        real = dict(
            con.execute(
                "select user_id, send_hour_local from main_scores.scores_send_time "
                "where user_id in ('u-000001','u-000002')"
            ).fetchall()
        )
    finally:
        con.close()
    assert got["u-000001"] == real["u-000001"]  # stale row replaced by the score
    assert got["u-000002"] == 0  # fresher row kept (not the score's hour)


def test_new_user_inserts(fresh: Path) -> None:
    con = duckdb.connect(str(fresh))
    try:
        con.execute("delete from serving.send_schedule where user_id = 'u-000003'")
    finally:
        con.close()
    wb.write_back("tiny", fresh)
    con = duckdb.connect(str(fresh))
    try:
        n = con.execute(
            "select count(*) from serving.send_schedule where user_id = 'u-000003'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 1  # absent → inserted


def test_writeback_under_tokyo_is_identical(tmp_path: Path) -> None:
    """A build + write-back under TZ=Asia/Tokyo yields the same send_schedule —
    loader.connect forces UTC and written_at is data-derived (no host clock)."""
    db = tmp_path / "tk.duckdb"
    script = (
        "import sys; from pathlib import Path;"
        "sys.path.insert(0, sys.argv[3]);"
        "from tests.test_writeback import build_tiny, send_schedule_hash;"
        "from serving import writeback as wb;"
        "db=Path(sys.argv[1]); build_tiny(db); wb.write_back('tiny', db);"
        "print(send_schedule_hash(db))"
    )
    env = {**os.environ, "TZ": "Asia/Tokyo", "DO_NOT_TRACK": "1"}
    out = subprocess.run(
        [sys.executable, "-c", script, str(db), str(DBT), str(ROOT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == pins.SEND_SCHEDULE_SHA256_TINY


def test_writeback_reads_only_scores_and_dim_current() -> None:
    """The write-back's read set is scores_send_time + dim_user_current — never
    the raw schema (§3.1). The truth-isolation test covers `truth`; this pins raw."""
    import inspect

    assert wb.SCORES == "main_scores.scores_send_time"
    assert wb.DIM_CURRENT == "main_marts.dim_user_current"
    reads = inspect.getsource(wb.read_candidates) + inspect.getsource(wb.read_existing)
    assert "raw" not in reads  # the read queries never touch the raw schema


def test_cli_refuses_bad_profile() -> None:
    from serving import cli

    for bad in ("../x", "", 'a"; rm'):
        with pytest.raises(SystemExit) as e:
            cli.writeback(bad)
        assert e.value.code == 2
