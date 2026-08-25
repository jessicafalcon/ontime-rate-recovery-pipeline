"""Staging pins (spec Phase 2 invariants 1, 2, 3, 5): a real `dbt build` of
fixtures/tiny into a tmp DuckDB file, in-process. No service, no network.
One build per session (module fixture); every assertion reads the tables."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from generator.dims import tz_at
from generator.models import DimUserRow
from loader import load as loader
from tests import pins

ROOT = Path(__file__).parent.parent
DBT = ROOT / "dbt"


def build(db: Path) -> bool:
    from dbt.cli.main import dbtRunner

    loader.load("tiny", db)
    args = ["build", "--project-dir", str(DBT), "--profiles-dir", str(DBT)]
    args += ["--target", "duckdb", "--quiet", "--target-path", str(db.parent / "t")]
    with pytest.MonkeyPatch.context() as mp:  # never leaks into later tests
        mp.setenv("OTR_DUCKDB_PATH", str(db))
        return bool(dbtRunner().invoke(args).success)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("build") / "tiny.duckdb"
    assert build(db)
    return db


def q(db: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db))  # dbt keeps its own handle open in-process
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def table_hash(db: Path, table: str) -> tuple:
    sql = (
        "select md5(string_agg(r::varchar, '|' order by r::varchar)) "
        f"from (select {table} as r from main_staging.{table})"
    )
    return q(db, sql)[0]


def test_tiny_build_is_green(built: Path) -> None:
    tables = {
        r[0] for r in q(built, "select table_name from information_schema.tables")
    }
    assert {"events", "dim_user", "stg_events", "stg_prompts"} <= tables


def test_pins_are_reproduced(built: Path) -> None:
    (n_raw,) = q(built, "select count(*) from raw.events")[0]
    (n_stg,) = q(built, "select count(*) from main_staging.stg_events")[0]
    (n_prompts,) = q(built, "select count(*) from main_staging.stg_prompts")[0]
    (n_dim,) = q(built, "select count(*) from raw.dim_user")[0]
    (n_nulls,) = q(
        built,
        "select count(*) from main_staging.stg_events "
        "where event_type like 'upload_%' and error_code is null",
    )[0]
    assert (n_raw, n_stg, n_prompts, n_dim, n_nulls) == (
        pins.RAW_EVENT_ROWS,
        pins.STG_EVENT_ROWS,
        pins.STG_PROMPT_ROWS,
        pins.DIM_USER_ROWS,
        pins.STG_UPLOAD_ERROR_CODE_NULLS,
    )


def test_dedupe_count_matches_pin(built: Path) -> None:
    (n_raw, n_ids) = q(
        built, "select count(*), count(distinct insert_id) from raw.events"
    )[0]
    (n_stg, n_stg_ids) = q(
        built, "select count(*), count(distinct insert_id) from main_staging.stg_events"
    )[0]
    assert n_raw - n_stg == pins.DEDUPE_COUNT
    assert n_stg == n_stg_ids == n_ids
    assert pins.DEDUPE_COUNT > 0  # the injector is on in tiny


def test_tz_change_users_are_converted_under_each_row(built: Path) -> None:
    """Every event of a two-row user gets the tz generator/dims.py::tz_at gives,
    and the local time is that zone's wall clock — independent reference."""
    rows = q(
        built,
        "select user_id, tz, cohort_id, signup_date, valid_from, valid_to "
        "from raw.dim_user order by user_id, valid_from",
    )
    dims: dict[str, list[DimUserRow]] = {}
    for user_id, tz, cohort, signup, vf, vt in rows:
        dims.setdefault(user_id, []).append(
            DimUserRow(
                user_id=user_id,
                tz=tz,
                cohort_id=cohort,
                signup_date=signup,
                valid_from=vf.replace(tzinfo=ZoneInfo("UTC")),
                valid_to=None if vt is None else vt.replace(tzinfo=ZoneInfo("UTC")),
            )
        )
    changers = [u for u, r in dims.items() if len(r) > 1]
    assert changers == ["u-000008", "u-000010"]
    users = ", ".join(f"'{u}'" for u in changers)
    events = q(
        built,
        "select user_id, tz, client_event_time, client_event_time_local "
        f"from main_staging.stg_events where user_id in ({users})",
    )
    assert len({tz for _, tz, _, _ in events}) > 1  # both rows actually used
    for user_id, tz, t, local in events:
        t_utc = t.replace(tzinfo=ZoneInfo("UTC"))
        assert tz == tz_at(dims[user_id], t_utc), (user_id, t)
        assert local == t_utc.astimezone(ZoneInfo(tz)).replace(tzinfo=None), (
            user_id,
            t,
        )


def test_every_event_has_a_tz_and_undelivered_prompts_match_pin(built: Path) -> None:
    assert q(
        built, "select count(*) from main_staging.stg_events where tz is null"
    ) == [(0,)]
    (n_prompts, n_delivered) = q(
        built, "select count(*), count(delivered_at) from main_staging.stg_prompts"
    )[0]
    assert n_prompts == pins.STG_PROMPT_ROWS
    assert n_prompts - n_delivered == pins.STG_PROMPTS_UNDELIVERED
    # the receipt taken is the earliest one for its prompt
    earlier = q(
        built,
        "select count(*) from main_staging.stg_prompts p "
        "join main_staging.stg_events e on e.prompt_id = p.prompt_id "
        "and e.event_type = 'prompt_delivered' "
        "and e.client_event_time < p.delivered_at",
    )
    assert earlier == [(0,)]


def test_upload_delay_sign_and_range_match_pins(built: Path) -> None:
    """received − client: negative = client clock ahead (skew injector)."""
    lo, hi, neg = q(
        built,
        "select min(upload_delay_seconds), max(upload_delay_seconds), "
        "sum(case when upload_delay_seconds < 0 then 1 else 0 end) "
        "from main_staging.stg_events",
    )[0]
    assert (lo, hi) == pins.STG_DELAY_RANGE_SECONDS
    assert neg == pins.STG_NEGATIVE_DELAY_ROWS


def test_two_builds_are_identical(built: Path, tmp_path: Path) -> None:
    db2 = tmp_path / "again.duckdb"
    assert build(db2)
    for table in ("stg_events", "stg_prompts"):
        assert table_hash(built, table) == table_hash(db2, table), table


def test_build_under_a_non_utc_host_zone_is_identical(built: Path, tmp_path: Path):
    """A second process with TZ=Asia/Tokyo (DuckDB's default session zone
    follows the host) produces the same staged bytes (spec invariant 3)."""
    db2 = tmp_path / "tokyo.duckdb"
    code = (
        "import sys; from pathlib import Path; from tests.test_staging import build; "
        "sys.exit(0 if build(Path(sys.argv[1])) else 1)"
    )
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONPATH": str(ROOT)}
    subprocess.run(
        [sys.executable, "-c", code, str(db2)], cwd=ROOT, env=env, check=True
    )
    for table in ("stg_events", "stg_prompts"):
        assert table_hash(built, table) == table_hash(db2, table), table


def test_tokyo_day_one_lands_on_the_previous_utc_day(built: Path) -> None:
    """events_2026-01-04.jsonl: 08:00 Tokyo local is 23:00 UTC on the 4th."""
    rows = q(
        built,
        "select sent_at, sent_at_local, tz from main_staging.stg_prompts "
        "where sent_at < timestamp '2026-01-05' order by sent_at limit 1",
    )
    assert rows and rows[0][2] == "Asia/Tokyo"
    assert rows[0][1] == datetime(2026, 1, 5, 8, 0)
