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
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime
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
    the raw schema (§3.1). The truth-isolation test covers `truth`; this pins
    the two read STATEMENTS themselves (round 1 #21: the SQL lives in
    candidates_sql / EXISTING_SQL, so those strings are what is asserted)."""
    assert wb.SCORES == "main_scores.scores_send_time"
    assert wb.DIM_CURRENT == "main_marts.dim_user_current"
    read = wb.candidates_sql()
    relations = re.findall(r"(?:from|join)\s+([a-z_.]+)", read)
    assert relations == [wb.SCORES, wb.DIM_CURRENT]  # exactly the two, no third
    assert re.findall(r"from\s+([a-z_.]+)", wb.EXISTING_SQL) == [wb.SEND_SCHEDULE]
    for sql in (read, wb.EXISTING_SQL):
        assert "raw" not in sql  # the read queries never touch the raw schema


def test_cli_refuses_bad_profile() -> None:
    from serving import cli

    for bad in ("../x", "", 'a"; rm'):
        with pytest.raises(SystemExit) as e:
            cli.writeback(bad)
        assert e.value.code == 2


def test_writeback_refuses_without_a_db() -> None:
    """No data/<p>.duckdb → the guard refuses (SystemExit 2), not a confusing
    downstream failure (round 1, finding 1)."""
    from serving import cli

    assert not loader.db_path("nodbabsent").is_file()  # precondition
    with pytest.raises(SystemExit) as e:
        cli.writeback("nodbabsent")
    assert e.value.code == 2


# ------------------- Phase 10: numeric version order, the TARGET=spanner path


def _cand(version: str, when: datetime, uid: str = "u", hour: int = 8) -> wb.Candidate:
    return wb.Candidate(uid, "c", hour, 0, "UTC", 0.5, version, when)


def test_version_orders_numerically_v10_beats_v2() -> None:
    """The Done-when's for-all: an older model_version never overwrites a newer
    one — under NUMERIC order ('v10' < 'v2' lexically was the BACKLOG bug)."""
    t0 = datetime(2026, 1, 12, 0, 47)
    t1 = datetime(2026, 1, 13, 0, 0)
    assert wb.version_key("v10") > wb.version_key("v2")
    assert wb.should_replace(_cand("v10", t0), ("v2", t1)) is True  # newer version
    assert wb.should_replace(_cand("v2", t1), ("v10", t0)) is False  # older → keep
    assert wb.should_replace(_cand("v2", t0), ("v2", t0)) is False  # tie → no-op


def test_malformed_version_refuses() -> None:
    """A version outside v<int> raises — never a lexical fallback."""
    for bad in ("", "1", "v", "v1.2", "v-1", "x1", "V1"):
        with pytest.raises(ValueError):
            wb.version_key(bad)
    t = datetime(2026, 1, 12)
    with pytest.raises(ValueError):
        wb.should_replace(_cand("x1", t), ("v1", t))


def test_malformed_version_refuses_on_the_insert_path_too() -> None:
    """Round 1 #1 (BLOCKER): an absent row must not short-circuit the parse —
    a malformed candidate is refused BEFORE it can be stored (a stored one
    would raise on every later run's comparison: a wedged serving table).
    Unit: should_replace with existing=None; end-to-end: the Spanner path
    over an empty store writes nothing."""
    t = datetime(2026, 1, 12)
    with pytest.raises(ValueError):
        wb.should_replace(_cand("x1", t), None)
    with pytest.raises(ValueError):
        wb.winners_of([_cand("v1", t, "a"), _cand("x1", t, "b")], {})
    from serving import spanner as sp

    store = FakeSpanner("my-proj")
    bad = [_bq_row("u-000001", 8, "x1", t)]
    with pytest.raises(ValueError):
        sp.write_back(
            "my-proj",
            bq_clients=lambda p: FakeQuery(p, bad),
            spanner_clients=lambda p: store,
        )
    assert store.rows() == {} and store.upserts == 0  # refused before any write
    assert not store.in_transaction()  # the abort rolled back, nothing left open


def test_columns_are_the_golden_nine_and_row_of_maps_by_name() -> None:
    """Round 1 #2: ONE column tuple for every writer, pinned to the golden's
    nine; row_of looks each value up by field NAME, so swapping two same-typed
    fields' positions (cohort_id ↔ tz) is caught here, not by a type."""
    from serving import spanner as sp

    assert wb.COLUMNS == SEND_SCHEDULE_GOLDEN.columns
    assert sp.COLUMNS is wb.COLUMNS
    assert wb.COLUMNS == tuple(f.name for f in fields(wb.Candidate)) + ("written_at",)
    c = wb.Candidate(
        user_id="U",
        cohort_id="COHORT",
        send_hour_local=7,
        send_minute_local=41,
        tz="Asia/Tokyo",
        confidence=0.25,
        model_version="v3",
        computed_as_of=datetime(2026, 1, 12, 0, 47),
    )
    for row in (wb.row_of(c), sp.row_of(c)):
        by_name = dict(zip(wb.COLUMNS, row, strict=True))
        for f in fields(c):
            if f.name != "computed_as_of":
                assert by_name[f.name] == getattr(c, f.name), f.name
        assert by_name["written_at"] == by_name["computed_as_of"]
    assert wb.row_of(c)[wb.COLUMNS.index("computed_as_of")] == c.computed_as_of
    aware = sp.row_of(c)[wb.COLUMNS.index("computed_as_of")]
    assert aware == c.computed_as_of.replace(tzinfo=UTC)  # a UTC instant on Spanner


def test_reader_relations_per_target() -> None:
    """One read, two named configurations: duckdb reads the main_* relations,
    spanner reads `<project>.ontime.*` off BigQuery — and neither read names
    raw (§3.1; truth is the isolation test's grep)."""
    from serving import spanner as sp

    local = wb.candidates_sql()
    assert wb.SCORES in local and wb.DIM_CURRENT in local
    scores, dims = sp.relations("my-proj")
    assert scores == "`my-proj.ontime.scores_send_time`"
    assert dims == "`my-proj.ontime.dim_user_current`"
    cloud = wb.candidates_sql(scores, dims)
    assert "main_scores" not in cloud and "main_marts" not in cloud
    for sql in (local, cloud, sp.EXISTING_SQL):
        assert "raw" not in sql


# The offline fakes EXECUTE the SQL they are handed on in-process DuckDB
# (round 1 #3): a read of the wrong relation, a swapped join, or a wrong
# column list fails here the way it would live, instead of being ignored.


def _bq_sql(sql: str) -> str:
    """BigQuery's `a.b.c` quoting → DuckDB's "a"."b"."c" — the only translation."""
    return re.sub(
        r"`([^`]+)`", lambda m: ".".join(f'"{x}"' for x in m.group(1).split(".")), sql
    )


class FakeQuery:
    """BigQuery `ontime`, modelled: scores_send_time (the served columns) and
    dim_user_current as tables in an attached `<project>` catalog. Timestamps
    come back NAIVE (DuckDB `timestamp`) — one side of the naive/aware mix
    the write path must normalize (round 1 #4)."""

    def __init__(self, project: str, rows: list[tuple]) -> None:
        self.sqls: list[str] = []
        self.con = duckdb.connect()
        self.con.execute("set TimeZone = 'UTC'")
        self.con.execute(f"attach ':memory:' as \"{project}\"")
        self.con.execute(f'create schema "{project}".ontime')
        self.con.execute(
            f'create table "{project}".ontime.scores_send_time ('
            "user_id varchar, cohort_id varchar, send_hour_local integer, "
            "send_minute_local integer, confidence double, model_version varchar, "
            "computed_as_of timestamp)"
        )
        self.con.execute(
            f'create table "{project}".ontime.dim_user_current '
            "(user_id varchar, tz varchar)"
        )
        for r in rows:
            c = wb.Candidate(*r)
            self.con.execute(
                f'insert into "{project}".ontime.scores_send_time '
                "values (?,?,?,?,?,?,?)",
                [
                    c.user_id,
                    c.cohort_id,
                    c.send_hour_local,
                    c.send_minute_local,
                    c.confidence,
                    c.model_version,
                    c.computed_as_of.replace(tzinfo=None),
                ],
            )
            self.con.execute(
                f'insert into "{project}".ontime.dim_user_current values (?, ?)',
                [c.user_id, c.tz],
            )

    def query(self, sql: str) -> list[dict[str, object]]:
        """Rows keyed by column name, like google-cloud-bigquery's Row."""
        self.sqls.append(sql)
        return wb.rows_by_name(self.con.execute(_bq_sql(sql)))


class FakeSpanner:
    """The Spanner database, modelled: `send_schedule` with the nine columns
    (timestamps `timestamptz` → AWARE on read, the other side of #4), a
    snapshot `read`, and `transact` running the function inside a real
    transaction — ONCE aborted and rolled back, then re-run and committed,
    the retry `run_in_transaction` performs, so the write path's function
    must be re-runnable (round 1 #7)."""

    def __init__(self, project: str) -> None:
        self.project = project
        self.upserts = 0
        self.snapshot_reads = 0
        self.txn_reads = 0
        self.attempts = 0
        self.con = duckdb.connect()
        self.con.execute("set TimeZone = 'UTC'")
        self.con.execute(
            "create table send_schedule ("
            "user_id varchar primary key, cohort_id varchar, "
            "send_hour_local bigint, send_minute_local bigint, tz varchar, "
            "confidence double, model_version varchar, computed_as_of timestamptz, "
            "written_at timestamptz)"
        )

    # -- the Txn protocol (only valid inside transact)
    def read(self, sql: str) -> list[dict[str, object]]:
        if self._in_txn:
            self.txn_reads += 1
        else:
            self.snapshot_reads += 1
        return wb.rows_by_name(self.con.execute(sql))

    def upsert(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        assert self._in_txn, "upsert outside the transaction"
        assert table == "send_schedule" and columns == wb.COLUMNS
        self.upserts += 1
        for r in rows:
            self.con.execute("delete from send_schedule where user_id = ?", [r[0]])
            self.con.execute(
                f"insert into send_schedule ({', '.join(columns)}) "
                f"values ({', '.join('?' * len(columns))})",
                list(r),
            )

    _in_txn = False

    def _attempt(self, fn: Callable[[object], object]) -> object:
        """One attempt inside a real transaction; an exception rolls it back
        and propagates — run_in_transaction's shape (round 2 #13)."""
        self.con.execute("begin transaction")
        self._in_txn = True
        self.attempts += 1
        try:
            out = fn(self)
        except BaseException:
            self.con.execute("rollback")
            raise
        finally:
            self._in_txn = False
        return out

    def transact(self, fn: Callable[[object], object]) -> object:
        self._attempt(fn)  # attempt 1: aborted by the store
        self.con.execute("rollback")
        out = self._attempt(fn)  # attempt 2: committed
        self.con.execute("commit")
        return out

    def in_transaction(self) -> bool:
        """DuckDB: a `begin` with no commit/rollback leaves one open."""
        try:
            self.con.execute("begin transaction")
        except duckdb.TransactionException:
            return True
        self.con.execute("rollback")
        return False

    def rows(self) -> dict[str, tuple]:
        got = self.con.execute(
            f"select {', '.join(wb.COLUMNS)} from send_schedule order by user_id"
        ).fetchall()
        return {r[0]: r for r in got}

    def seed(self, *rows: tuple) -> None:
        for r in rows:
            self.con.execute(
                f"insert into send_schedule values ({', '.join('?' * len(r))})", list(r)
            )


def _bq_row(uid: str, hour: int, version: str, when: datetime) -> tuple:
    """A candidates_sql result row off BigQuery."""
    return (uid, "c", hour, 0, "UTC", 0.5, version, when)


V = wb.COLUMNS.index("model_version")
AS_OF = wb.COLUMNS.index("computed_as_of")
WRITTEN = wb.COLUMNS.index("written_at")


def test_fakes_execute_the_read_contract() -> None:
    """The fakes model the read: the canonical SQL returns the rows; a swapped
    candidates_sql(dims, scores), an EXISTING_SQL at another table, or a
    column not on the relation fail loudly (they do not return the rows)."""
    from serving import spanner as sp

    q = FakeQuery("my-proj", [_bq_row("u-000001", 8, "v1", datetime(2026, 1, 13))])
    scores, dims = sp.relations("my-proj")
    assert len(q.query(wb.candidates_sql(scores, dims))) == 1
    with pytest.raises(duckdb.Error):
        q.query(wb.candidates_sql(dims, scores))
    with pytest.raises(duckdb.Error):
        q.query(wb.candidates_sql(scores, "`my-proj.raw.dim_user`"))
    store = FakeSpanner("my-proj")
    assert store.transact(lambda t: t.read(sp.EXISTING_SQL)) == []
    with pytest.raises(duckdb.Error):
        store.transact(
            lambda t: t.read(sp.EXISTING_SQL.replace("send_schedule", "dim_user"))
        )


def test_spanner_writeback_second_run_writes_zero() -> None:
    """Invariant 1 on the Spanner path: two runs over the same scores — the
    second writes 0 and the store is byte-identical (written_at =
    computed_as_of, data-derived, so nothing can move)."""
    from serving import spanner as sp

    rows = [
        _bq_row("u-000001", 8, "v1", datetime(2026, 1, 13)),
        _bq_row("u-000002", 21, "v1", datetime(2026, 1, 12, 23, 30)),
    ]
    store = FakeSpanner("my-proj")
    n, written = sp.write_back(
        "my-proj",
        bq_clients=lambda p: FakeQuery(p, rows),
        spanner_clients=lambda p: store,
    )
    assert (n, written) == (2, 2)
    assert len(store.rows()) == 2
    assert all(r[AS_OF] == r[WRITTEN] for r in store.rows().values())
    assert all(r[AS_OF].tzinfo is not None for r in store.rows().values())
    before = store.rows()
    upserts_after_run_1 = store.upserts
    n, written = sp.write_back(
        "my-proj",
        bq_clients=lambda p: FakeQuery(p, rows),
        spanner_clients=lambda p: store,
    )
    assert (n, written) == (2, 0)  # nothing strictly greater
    assert store.rows() == before
    assert store.upserts == upserts_after_run_1  # the mutation ledger: no call at all


def test_spanner_replace_only_on_strictly_greater() -> None:
    """The full cloud path honours the guard: a v2 candidate never overwrites a
    stored v10 row; a v10 candidate replaces a v2 row (numeric order). The
    store's timestamps are AWARE and the warehouse's NAIVE — the comparison
    only works through the write path's UTC normalization (round 1 #4)."""
    from serving import spanner as sp

    t0 = datetime(2026, 1, 12, tzinfo=UTC)
    store = FakeSpanner("my-proj")
    store.seed(
        ("u-000001", "c", 9, 0, "UTC", 0.5, "v10", t0, t0),
        ("u-000002", "c", 9, 0, "UTC", 0.5, "v2", t0, t0),
    )
    rows = [
        _bq_row(
            "u-000001", 8, "v2", datetime(2026, 1, 13)
        ),  # older version, later as_of
        _bq_row(
            "u-000002", 8, "v10", datetime(2026, 1, 12)
        ),  # newer version, same as_of
    ]
    q = FakeQuery("my-proj", rows)
    assert all(
        r["computed_as_of"].tzinfo is None
        for r in q.query(wb.candidates_sql(*sp.relations("my-proj")))
    )
    n, written = sp.write_back(
        "my-proj", bq_clients=lambda p: q, spanner_clients=lambda p: store
    )
    assert (n, written) == (2, 1)
    assert store.rows()["u-000001"][V] == "v10"  # v2 never overwrote v10
    assert store.rows()["u-000002"][V] == "v10"  # v10 replaced v2


def test_spanner_guard_and_write_are_one_retried_transaction() -> None:
    """Round 1 #7: the stored-pair read and the upsert happen INSIDE the one
    read-write transaction (never a snapshot read before it), and the function
    survives Spanner's abort-and-retry: the fake aborts attempt 1, re-runs, and
    the committed state is exactly one write's worth."""
    from serving import spanner as sp

    rows = [_bq_row("u-000001", 8, "v1", datetime(2026, 1, 13))]
    store = FakeSpanner("my-proj")
    n, written = sp.write_back(
        "my-proj",
        bq_clients=lambda p: FakeQuery(p, rows),
        spanner_clients=lambda p: store,
    )
    assert (n, written) == (1, 1)
    assert store.attempts == 2 and store.txn_reads == 2  # read on EVERY attempt
    assert store.snapshot_reads == 0  # never a read outside the transaction
    assert store.upserts == 2 and len(store.rows()) == 1  # attempt 1 rolled back


def test_cloud_writeback_refuses_before_any_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every refusal — missing/env CONFIRM, a bad PROJECT, an unknown TARGET,
    a malformed PROFILE (optional on this target, never unvalidated) —
    happens before either client factory is even resolved (spec invariant 6)."""
    from serving import cli
    from serving import spanner as sp

    def boom() -> None:
        raise AssertionError("client factory resolved before the gate")

    monkeypatch.setattr(sp, "default_query_clients", boom)
    monkeypatch.setattr(sp, "default_spanner_clients", boom)
    cases = [
        ("tiny", "spanner", "ontime-rate-recovery", "", ""),  # no CONFIRM
        ("", "spanner", "ontime-rate-recovery", "", ""),  # no PROFILE, no CONFIRM
        ("tiny", "spanner", "ontime-rate-recovery", "yes", "environment"),  # env origin
        ("tiny", "spanner", "../x", "yes", "command line"),  # bad project
        ("tiny", "spanner", "", "yes", "command line"),  # empty project
        ("tiny", "postgres", "", "", ""),  # unknown target
        ("../x", "spanner", "", "", ""),  # bad profile
        ('a"; rm', "spanner", "ontime-rate-recovery", "yes", "command line"),
    ]
    for args in cases:
        with pytest.raises(SystemExit) as e:
            cli.writeback(*args)
        assert e.value.code == 2, args


def test_cloud_writeback_ok_line_names_the_warehouse_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Round 1 #20: on TARGET=spanner the read is BigQuery `ontime`, not a
    PROFILE's build — the OK line says so, with or without a PROFILE."""
    from serving import cli
    from serving import spanner as sp

    monkeypatch.setattr(sp, "write_back", lambda project: (20, 0))
    for profile in ("tiny", ""):
        rc = cli.writeback(
            profile, "spanner", "ontime-rate-recovery", "yes", "command line"
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert (
            "writeback OK: ontime-rate-recovery.ontime → spanner, 20 users, 0 written"
            in out
        )
        assert "tiny" not in out


def test_candidates_are_read_by_column_name() -> None:
    """Round 2 #9: the READ maps by name like the write — a select list in any
    order, or a row dict in any key order, lands every value in its field; a
    missing or extra column refuses. candidates_sql names each Candidate field
    exactly once, tz from the dims side, the rest from the scores side."""
    from serving import spanner as sp

    row = {
        "tz": "Asia/Tokyo",
        "cohort_id": "COHORT",
        "computed_as_of": datetime(2026, 1, 12, 0, 47),
        "confidence": 0.25,
        "user_id": "U",
        "send_minute_local": 41,
        "model_version": "v3",
        "send_hour_local": 7,
    }
    c = wb.candidate_of(row)
    for name, value in row.items():
        assert getattr(c, name) == value, name
    with pytest.raises(ValueError):
        wb.candidate_of({k: v for k, v in row.items() if k != "tz"})
    with pytest.raises(ValueError):
        wb.candidate_of({**row, "written_at": row["computed_as_of"]})
    # round 5 O4: a wrong-typed cell refuses, never coerces (the rule
    # existing_of follows) — str for an int, int for a float, str for a datetime
    for bad in (
        {"send_hour_local": "7"},
        {"confidence": 1},
        {"computed_as_of": "2026-01-12"},
        {"user_id": 7},
        {"send_minute_local": True},
    ):
        with pytest.raises(ValueError, match="want"):
            wb.candidate_of({**row, **bad})
    sql = wb.candidates_sql()
    select = sql[len("select ") : sql.index(" from ")]
    items = [x.strip() for x in select.split(",")]
    assert items == [f"{'d' if f == 'tz' else 's'}.{f}" for f in wb.CANDIDATE_FIELDS]
    # the cloud read goes through the same mapper: a fake returning keys in a
    # different order than the select list still lands by name
    rows = [_bq_row("u-000001", 8, "v1", datetime(2026, 1, 13))]

    class Shuffled(FakeQuery):
        def query(self, sql: str) -> list[dict[str, object]]:
            return [dict(reversed(list(r.items()))) for r in super().query(sql)]

    store = FakeSpanner("my-proj")
    sp.write_back(
        "my-proj",
        bq_clients=lambda p: Shuffled(p, rows),
        spanner_clients=lambda p: store,
    )
    got = dict(zip(wb.COLUMNS, store.rows()["u-000001"], strict=True))
    assert (got["cohort_id"], got["tz"], got["send_hour_local"]) == ("c", "UTC", 8)


def test_duckdb_writeback_is_one_transaction(fresh: Path) -> None:
    """Round 2 #8 (Amendment A's DuckDB half): a failure between the delete and
    the insert rolls the run back — the table is exactly what the run started
    from, never a half-replaced one."""
    before = send_schedule_hash(fresh)
    con = duckdb.connect(str(fresh))
    try:  # make one row stale so there IS a winner to replace
        con.execute(
            "update serving.send_schedule set computed_as_of = timestamp '2020-01-01', "
            "written_at = timestamp '2020-01-01' where user_id = 'u-000001'"
        )
        stale = send_schedule_hash(fresh)
    finally:
        con.close()
    real_apply = wb.apply_writeback

    def delete_then_die(con: duckdb.DuckDBPyConnection, winners: list) -> None:
        assert winners, "no winner to replace"
        con.execute("delete from serving.send_schedule where user_id = 'u-000001'")
        raise RuntimeError("simulated failure between delete and insert")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wb, "apply_writeback", delete_then_die)
        with pytest.raises(RuntimeError, match="simulated"):
            wb.write_back("tiny", fresh)
    assert (
        send_schedule_hash(fresh) == stale
    )  # rolled back: the stale row is still there
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wb, "apply_writeback", real_apply)
        assert wb.write_back("tiny", fresh)[1] == 1  # and the next run repairs it
    assert send_schedule_hash(fresh) == before


def test_duckdb_target_is_single_writer(fresh: Path) -> None:
    """The stand-in's cross-process serialization is DuckDB's file lock: while
    this process holds the database, a second process cannot open it at all —
    so two write-backs cannot interleave on the DuckDB target (stated, and
    pinned, per round 2 #8)."""
    con = duckdb.connect(str(fresh))
    try:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import duckdb,sys; duckdb.connect(sys.argv[1]); print('opened')",
                str(fresh),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        con.close()
    assert probe.returncode != 0 and "opened" not in probe.stdout, probe.stdout
    assert "lock" in probe.stderr.lower() or "IOException" in probe.stderr, probe.stderr


def test_duckdb_writeback_rolls_back_before_close(
    fresh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 3 #5: the explicit `con.rollback()` on the failure path is what
    restores the table — observed on the SAME, still-open connection before
    `close` (which would also roll back, and hid a deleted line)."""
    real_connect = wb.loader.connect
    calls: list[str] = []
    held: dict[str, duckdb.DuckDBPyConnection] = {}

    class Proxy:
        def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
            self._con = con

        def __getattr__(self, name: str) -> object:
            return getattr(self._con, name)

        def rollback(self) -> None:
            calls.append("rollback")
            self._con.rollback()

        def close(self) -> None:
            calls.append("close")  # kept open: the assertion looks inside

    def connect(path: Path) -> Proxy:
        held["con"] = real_connect(path)
        return Proxy(held["con"])

    def delete_then_die(con: duckdb.DuckDBPyConnection, winners: list) -> None:
        con.execute("delete from serving.send_schedule where user_id = 'u-000001'")
        raise RuntimeError("simulated failure after the delete")

    monkeypatch.setattr(wb.loader, "connect", connect)
    monkeypatch.setattr(wb, "apply_writeback", delete_then_die)
    with pytest.raises(RuntimeError, match="simulated"):
        wb.write_back("tiny", fresh)
    con = held["con"]
    try:
        assert calls == ["rollback", "close"]
        (n,) = con.execute(
            "select count(*) from serving.send_schedule where user_id = 'u-000001'"
        ).fetchone()
        assert n == 1  # rolled back on the open connection — not by close
    finally:
        con.close()


def test_existing_pairs_are_read_by_column_name() -> None:
    """Round 3 #3 (missed in round 2): the stored pair maps by NAME on both
    targets — the select list is generated from EXISTING_COLUMNS, a shuffled
    row maps the same, a row with other keys refuses."""
    from serving import spanner as sp

    cols = ", ".join(wb.EXISTING_COLUMNS)
    assert wb.EXISTING_SQL == f"select {cols} from {wb.SEND_SCHEDULE}"
    assert sp.EXISTING_SQL == f"select {cols} from {sp.TABLE}"
    ts = datetime(2026, 1, 13, tzinfo=UTC)
    shuffled = {"computed_as_of": ts, "model_version": "v2", "user_id": "u-1"}
    assert wb.existing_of(shuffled) == ("u-1", ("v2", ts))
    with pytest.raises(ValueError, match="want"):
        wb.existing_of({"user_id": "u-1", "model_version": "v2"})
    with pytest.raises(ValueError, match="datetime"):
        wb.existing_of({"user_id": "u-1", "model_version": "v2", "computed_as_of": "x"})
    # round 4 #6 (Amendment N3): a non-str cell refuses, never `str()`-coerced
    for bad in (
        {"user_id": 1, "model_version": "v2"},
        {"user_id": "u-1", "model_version": 2},
    ):
        with pytest.raises(ValueError, match="want str"):
            wb.existing_of({**bad, "computed_as_of": ts})
    store = FakeSpanner("my-proj")
    store.con.execute(
        "insert into send_schedule values ('u-1', 'c', 8, 0, 'UTC', 0.5, 'v1', "
        "timestamp '2026-01-13', timestamp '2026-01-13')"
    )
    rows = store.transact(lambda t: t.read(sp.EXISTING_SQL))
    assert rows and set(rows[0]) == set(wb.EXISTING_COLUMNS)


def _streamed(fields: list[tuple[str, str]], rows: list[list[str]]) -> object:
    """A REAL google-cloud-spanner StreamedResultSet built offline from
    PartialResultSet protos — metadata (name + type per column) and the row
    cells as protobuf Values; no network, no client."""
    from google.cloud.spanner_v1 import (
        PartialResultSet,
        ResultSetMetadata,
        StructType,
        Type,
        TypeCode,
    )
    from google.cloud.spanner_v1.streamed import StreamedResultSet
    from google.protobuf import struct_pb2

    md = ResultSetMetadata(
        row_type=StructType(
            fields=[
                StructType.Field(name=n, type_=Type(code=getattr(TypeCode, t)))
                for n, t in fields
            ]
        )
    )
    first = PartialResultSet(metadata=md)
    for row in rows:
        first.values.extend(struct_pb2.Value(string_value=v) for v in row)
    return StreamedResultSet(iter([first]))


class _Backend:
    """What `execute_sql` returns is the adapter's only seam."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.sqls: list[str] = []

    def execute_sql(self, sql: str) -> object:
        self.sqls.append(sql)
        return self.result


def test_spanner_rows_come_from_the_library_by_name() -> None:
    """Amendment N3 (round 4 #1/#2): the Spanner read is the library's own
    `StreamedResultSet.to_dict_list()`, exercised on REAL result sets through
    the real adapter classes into `existing_of` — an empty table (the first
    write-back after a fresh apply), a shuffled column order, a zero-response
    stream. No mapping of ours sits between the wire and the guard."""
    from google.cloud.spanner_v1.streamed import StreamedResultSet

    from serving import spanner as sp

    shuffled = [
        ("computed_as_of", "TIMESTAMP"),
        ("user_id", "STRING"),
        ("model_version", "STRING"),
    ]
    # the transaction read (the write path)
    assert sp._GoogleTxn(_Backend(_streamed(shuffled, []))).read(sp.EXISTING_SQL) == []
    assert (
        sp._GoogleTxn(_Backend(StreamedResultSet(iter([])))).read(sp.EXISTING_SQL) == []
    )
    backend = _Backend(_streamed(shuffled, [["2026-01-13T00:00:00Z", "u-1", "v2"]]))
    rows = sp._GoogleTxn(backend).read(sp.EXISTING_SQL)
    assert backend.sqls == [sp.EXISTING_SQL]
    assert [set(r) for r in rows] == [set(wb.EXISTING_COLUMNS)]
    user_id, (version, ts) = wb.existing_of(rows[0])
    assert (user_id, version) == ("u-1", "v2")
    assert ts == datetime(2026, 1, 13, tzinfo=UTC) and ts.tzinfo is not None
    # the snapshot read (the integration read-back) — same library call
    client = sp.GoogleSpannerClient.__new__(sp.GoogleSpannerClient)

    class Db:
        def snapshot(self) -> Db:
            return self

        def __enter__(self) -> _Backend:
            return _Backend(
                _streamed(shuffled, [["2026-01-13T00:00:00Z", "u-1", "v2"]])
            )

        def __exit__(self, *a: object) -> bool:
            return False

    client._db = Db()  # type: ignore[attr-defined]
    assert [wb.existing_of(r)[0] for r in client.read(sp.EXISTING_SQL)] == ["u-1"]
    client._db = type(
        "Db2", (Db,), {"__enter__": lambda self: _Backend(_streamed(shuffled, []))}
    )()  # type: ignore[attr-defined]
    assert client.read(sp.EXISTING_SQL) == []


def test_bigquery_rows_come_from_the_library_by_name() -> None:
    """Round 5 #10 (O4, missed in round 4 — the Adapter contract on the second
    client): `GoogleQueryClient.query` maps by name through google-cloud-
    bigquery's own `Row.items()`, exercised on REAL `Row`s built offline with
    a shuffled field order and on an empty result — no client, no network."""
    from google.cloud.bigquery.table import Row

    from serving import spanner as sp

    values = {
        "user_id": "u-1",
        "cohort_id": "c",
        "send_hour_local": 8,
        "send_minute_local": 0,
        "tz": "UTC",
        "confidence": 0.5,
        "model_version": "v1",
        "computed_as_of": datetime(2026, 1, 13, tzinfo=UTC),
    }
    names = list(reversed(wb.CANDIDATE_FIELDS))
    rows = [Row(tuple(values[n] for n in names), {n: i for i, n in enumerate(names)})]

    class Job:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        def result(self) -> object:
            return iter(self._rows)

    class Client:
        def __init__(self, rows: list) -> None:
            self.rows, self.sqls = rows, []

        def query(self, sql: str) -> Job:
            self.sqls.append(sql)
            return Job(self.rows)

    q = sp.GoogleQueryClient.__new__(sp.GoogleQueryClient)
    q._client = Client(rows)  # type: ignore[attr-defined]
    out = q.query("select 1")
    assert q._client.sqls == ["select 1"]  # type: ignore[attr-defined]
    assert out == [values]
    assert wb.candidate_of(out[0]) == wb.Candidate(**values)
    q._client = Client([])  # type: ignore[attr-defined]
    assert q.query("select 1") == []
