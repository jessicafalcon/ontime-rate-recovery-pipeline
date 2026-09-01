"""Phase 8a (specs/phase-8a-write-back.md): `make pipeline` == the chain by hand.

`serving.cli.pipeline` runs dbt build → eval → write-back in one process, into
data/<p>.duckdb (built from the committed fixture, so this is self-contained even
in a fresh worktree). The send_schedule it produces matches the pin and the
standalone write-back; scores_send_time equals the frozen golden (the chain
re-derives no score). No service, no network."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from eval import golden
from landing import load as landing
from serving import cli as scli
from serving import writeback as wb
from tests import pins
from tests.test_writeback import send_schedule_hash

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="module")
def piped() -> Path:
    """The real chain into data/tiny.duckdb (dbt build → eval → write-back)."""
    assert scli.pipeline("tiny") == 0
    return landing.db_path("tiny")


def test_pipeline_send_schedule_matches_pin(piped: Path) -> None:
    con = duckdb.connect(str(piped))
    try:
        n = con.execute("select count(*) from serving.send_schedule").fetchone()[0]
    finally:
        con.close()
    assert n == pins.SEND_SCHEDULE_ROWS_TINY
    assert send_schedule_hash(piped) == pins.SEND_SCHEDULE_SHA256_TINY


def test_pipeline_scores_equal_frozen_golden(piped: Path) -> None:
    """The chain re-derives no score: scores_send_time == fixtures/tiny golden."""
    built = golden.export_rows(piped, golden.SCORES_SEND_TIME)
    frozen = golden.parse(
        (FIXTURES / "tiny" / golden.SCORES_SEND_TIME.file).read_text(),
        golden.SCORES_SEND_TIME,
    )
    assert golden.diff_rows(built, frozen, golden.SCORES_SEND_TIME.key_width) == []


def test_pipeline_equals_standalone_writeback(piped: Path) -> None:
    """Rebuild send_schedule via the standalone write-back after dropping it; the
    pipeline's chained write-back and the standalone one produce the same table."""
    con = duckdb.connect(str(piped))
    try:
        con.execute("drop schema serving cascade")
    finally:
        con.close()
    cand, written = wb.write_back("tiny", piped)
    assert (cand, written) == (
        pins.SEND_SCHEDULE_ROWS_TINY,
        pins.SEND_SCHEDULE_ROWS_TINY,
    )
    assert send_schedule_hash(piped) == pins.SEND_SCHEDULE_SHA256_TINY


def test_cli_refuses_bad_profile() -> None:
    for bad in ("../x", "", 'a"; rm'):
        with pytest.raises(SystemExit) as e:
            scli.pipeline(bad)
        assert e.value.code == 2
