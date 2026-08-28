"""Phase 8b (specs/phase-8b-airflow-dag.md): backfill ≡ union at the make/
write-back level (Done-when clause 2, invariant 5).

Three THROUGH landings (BACKFILL_THROUGHS_TINY), each followed by the full chain
(dbt build → write-back) into ONE incremental DuckDB, land a send_schedule
byte-identical to a single union run. It holds because scores/marts are table
(recomputed each build), stg/attribution converge to the union at the final
landing (Phase 7, gaps ≤ lookback_days), and the write-back replaces a row only
on a strictly greater — monotone — (model_version, computed_as_of), so the union
interval's rows win. This is the make-level analogue of the DAG's catchup, which
test_int_airflow proves in the container. Real in-process builds into tmp DBs
(loader.DATA redirected); no service, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from loader import cli
from loader import load as loader
from serving import writeback as wb
from tests import pins
from tests.test_writeback import send_schedule_hash


def _run_chain(data_dir: Path, throughs, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the output DB to `data_dir`, then for each landing dbt-build the
    subset and write it back into the same (incremental) DB. Returns the DB path."""
    monkeypatch.setattr(loader, "DATA", data_dir)
    for through in throughs:
        assert cli.dbt_build("tiny", "", through=through) == 0
        wb.write_back("tiny")
    return loader.db_path("tiny")


def test_three_through_landings_equal_the_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three-interval backfill and a single union run land the identical
    send_schedule (the pinned hash)."""
    with monkeypatch.context() as m:
        db_backfill = _run_chain(tmp_path / "backfill", pins.BACKFILL_THROUGHS_TINY, m)
        h_backfill = send_schedule_hash(db_backfill)
    with monkeypatch.context() as m:
        db_union = _run_chain(tmp_path / "union", [""], m)  # "" = one full landing
        h_union = send_schedule_hash(db_union)
    assert h_backfill == h_union == pins.SEND_SCHEDULE_SHA256_TINY


def test_backfill_interval_twice_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running the final interval writes zero rows and leaves send_schedule
    byte-identical (idempotence under backfill; replace-iff-*strictly*-greater)."""
    with monkeypatch.context() as m:
        db = _run_chain(tmp_path / "bf", pins.BACKFILL_THROUGHS_TINY, m)
        h1 = send_schedule_hash(db)
        assert cli.dbt_build("tiny", "", through=pins.BACKFILL_THROUGHS_TINY[-1]) == 0
        _, written = wb.write_back("tiny")
        h2 = send_schedule_hash(db)
    assert written == 0
    assert h2 == h1 == pins.SEND_SCHEDULE_SHA256_TINY
