"""Mart pins (spec Phase 4 invariants 1–7): the real `dbt build` of
fixtures/tiny (tests/test_staging.py's in-process build), then every assertion
reads the marts, the attribution table or the golden. No service."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from test_attribution import built as built  # noqa: PLC0414 — module fixture
from test_attribution import project_vars
from test_staging import build, q

from eval import golden, report
from tests import pins

ROOT = Path(__file__).parent.parent
DAILY = ROOT / "fixtures" / "tiny" / golden.ONTIME_RATE_DAILY.file
SPEC = golden.ONTIME_RATE_DAILY


def test_partition_holds_on_every_tiny_cohort_day(built: Path) -> None:  # noqa: F811
    """Invariant 1, recomputed from attribution in Python and compared row
    for row with the mart (the singular test says the same in SQL)."""
    want = {
        (c, str(d)): (n, delivered, ot, uf, tg, un, df)
        for c, d, n, delivered, ot, uf, tg, un, df in q(
            built,
            """
            select cohort_id, cast(sent_at_local as date), count(*),
                   count(*) filter (where delivered_in_grace),
                   count(*) filter (where label = 'on_time'),
                   count(*) filter (where label = 'upload_fault'),
                   count(*) filter (where label = 'timing_gap'),
                   count(*) filter (where label = 'unattributed'),
                   count(*) filter (where label = 'delivery_fault')
            from main_attribution.attribution group by 1, 2
            """,
        )
    }
    have = {
        (r[0], r[1]): tuple(int(v) for v in r[2:9]) for r in golden.export_rows(built, SPEC)
    }
    assert have == want
    assert len(have) == pins.COHORT_DAYS
    for n, delivered, ot, uf, tg, un, df in have.values():
        assert ot + uf + tg + un == delivered
        assert delivered + df == n
    assert sum(v[1] for v in have.values()) == pins.PROMPTS_DELIVERED


def test_daily_golden_matches_fixture(built: Path) -> None:  # noqa: F811
    rows = golden.export_rows(built, SPEC)
    assert golden.diff_rows(rows, golden.parse(DAILY.read_text(), SPEC), SPEC.key_width) == []
    assert golden.render(rows, SPEC) == DAILY.read_text()  # byte-identical CSV


def test_overall_rate_matches_pin(built: Path) -> None:  # noqa: F811
    assert report.overall_rate(built) == pins.ONTIME_RATE
    assert pins.ONTIME_RATE == 75 / 123


def test_prompt_date_is_local_on_tiny(built: Path) -> None:  # noqa: F811
    """Invariant 4: 34 prompts straddle the UTC date (Tokyo mornings); every
    one is counted on its local date, and no cohort-day row is dated by UTC."""
    (n,) = q(
        built,
        "select count(*) from main_attribution.attribution "
        "where cast(sent_at as date) <> cast(sent_at_local as date)",
    )[0]
    assert n == pins.LOCAL_DATE_DIFFERS_FROM_UTC
    local = {r[1] for r in golden.export_rows(built, SPEC)}
    assert local == {f"2026-01-{d:02d}" for d in range(5, 12)}
    assert "2026-01-04" not in local  # the UTC date of the first Tokyo morning


def test_retention_is_all_null_on_tiny(built: Path) -> None:  # noqa: F811
    rows = q(
        built,
        "select count(*), count(retained), count(ontime_rate), count(distinct user_id) "
        "from main_marts.ontime_retention",
    )
    assert rows == [(pins.RETENTION_ROWS, 0, pins.RETENTION_ROWS, pins.RETENTION_ROWS)]
    (opens,) = q(
        built, "select count(*) from main_staging.stg_events where event_type = 'app_opened'"
    )[0]
    assert opens == pins.ORGANIC_OPEN_ROWS


def test_retention_var_equals_pin() -> None:
    assert project_vars()["retention_days"] == pins.RETENTION_DAYS


def _marts(db: Path) -> tuple[list, list]:
    return (
        golden.export_rows(db, SPEC),
        q(db, "select * from main_marts.ontime_retention order by user_id"),
    )


def test_two_builds_give_the_same_marts(built: Path, tmp_path: Path) -> None:  # noqa: F811
    db2 = tmp_path / "again.duckdb"
    assert build(db2)
    assert _marts(built) == _marts(db2)


def test_marts_under_a_non_utc_host_zone_are_identical(built: Path, tmp_path: Path):  # noqa: F811
    db2 = tmp_path / "tokyo.duckdb"
    code = (
        "import sys; from pathlib import Path; from tests.test_staging import build; "
        "sys.exit(0 if build(Path(sys.argv[1])) else 1)"
    )
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONPATH": str(ROOT)}
    subprocess.run([sys.executable, "-c", code, str(db2)], cwd=ROOT, env=env, check=True)
    assert _marts(built) == _marts(db2)
