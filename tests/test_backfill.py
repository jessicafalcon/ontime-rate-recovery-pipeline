"""Phase 8b (specs/phase-8b-airflow-dag.md): backfill ≡ union at the make/
write-back level (Done-when clause 2, invariant 5).

Three THROUGH landings (BACKFILL_THROUGHS_TINY), each followed by the full chain
(dbt build → write-back) into ONE incremental DuckDB, land a send_schedule
byte-identical to a single union run. It holds because scores/marts are table
(recomputed each build), stg/attribution converge to the union at the final
landing (Phase 7, gaps ≤ lookback_days), and the write-back replaces a row only
on a strictly greater — monotone — (model_version, computed_as_of), so the union
interval's rows win. This is the make-level analogue of the DAG's backfill, which
test_int_airflow proves in the container. Real in-process builds into tmp DBs
(loader.DATA redirected); no service, no network."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import pytest

from loader import cli
from loader import load as loader
from serving import writeback as wb
from tests import pins
from tests.test_writeback import send_schedule_hash


def _run_chain(
    data_dir: Path, throughs: Sequence[str], monkeypatch: pytest.MonkeyPatch
) -> Path:
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


def _scores_state(db: Path) -> tuple[str, object]:
    """(content hash of the served score columns, max computed_as_of)."""
    con = duckdb.connect(str(db))
    try:
        # a content hash of the model output (scores_send_time) — a proxy for "the
        # scored row changed"; incl. confidence (a served column, round 4 #4).
        # Note: tz is served but sourced from dim_user_current (BACKLOG row 34), so
        # a tz-only change is out of this proxy's scope — unreachable on tiny.
        h = con.execute(
            "select md5(string_agg(r, '|' order by r)) from ("
            "select user_id || '/' || cohort_id || '/' "
            "|| cast(center_hour_local as varchar) || '/' "
            "|| cast(send_hour_local as varchar) || ':' "
            "|| cast(send_minute_local as varchar) || '/' "
            "|| cast(confidence as varchar) as r "
            "from main_scores.scores_send_time)"
        ).fetchone()[0]
        as_of = con.execute(
            "select max(computed_as_of) from main_scores.scores_send_time"
        ).fetchone()[0]
        return h, as_of
    finally:
        con.close()


def test_computed_as_of_advances_when_scores_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant 5's precondition (Amendment 2), stated as the property it needs:
    whenever a landing CHANGES the served scores, `computed_as_of` STRICTLY
    increases (ties only when the scores are unchanged) — so replace-iff-*greater*
    carries every change forward and the union interval wins. tiny exercises both:
    07→12 changes the scores (strict increase asserted), 12→13 does not (tie ok)."""
    monkeypatch.setattr(loader, "DATA", tmp_path / "mono")
    prev: tuple[str, object] | None = None
    for through in pins.BACKFILL_THROUGHS_TINY:
        assert cli.dbt_build("tiny", "", through=through) == 0
        state = _scores_state(loader.db_path("tiny"))
        if prev is not None:
            (prev_hash, prev_as_of), (cur_hash, cur_as_of) = prev, state
            if cur_hash != prev_hash:
                assert cur_as_of > prev_as_of, (through, cur_as_of, prev_as_of)
            else:
                assert cur_as_of >= prev_as_of, (through, cur_as_of, prev_as_of)
        prev = state


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
