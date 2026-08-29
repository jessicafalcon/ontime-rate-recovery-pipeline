"""Phase 8b (specs/phase-8b-airflow-dag.md): the THROUGH-aware `make dbt-build`.

Invariant 2 — a per-interval `make dbt-build … THROUGH=<ds>` lands and builds
only files uploaded on or before `<ds>`; unset loads all. Invariant 3 — an
ill-formed THROUGH is refused before any landing. A real in-process build into a
tmp DuckDB (loader.DATA redirected), no service, no network. This is the build
path the DAG runs per interval, so the backfill's partial landings are exercised
here at the make level."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from loader import cli
from loader import load as loader
from tests import pins


def _raw_events(db: Path) -> tuple[int, str]:
    con = duckdb.connect(str(db))
    try:
        n, mx = con.execute(
            "select count(*), max(cast(server_upload_time as date))::varchar "
            "from raw.events"
        ).fetchone()
        return n, mx
    finally:
        con.close()


def _build(tmp: Path, monkeypatch: pytest.MonkeyPatch, through: str = "") -> Path:
    """Redirect the output DB to tmp (fixtures stay real) and run dbt-build."""
    monkeypatch.setattr(loader, "DATA", tmp / "data")
    assert cli.dbt_build("tiny", "", through=through) == 0
    return loader.db_path("tiny")


def test_dbt_build_through_lands_only_files_le_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cut 2026-01-07 lands 4 of tiny's 10 files: no event uploaded after it,
    fewer rows than the full set. Kills `event_files invert-guard` (which would
    land all files → max upload 2026-01-13 > the cut)."""
    db = _build(tmp_path, monkeypatch, through="2026-01-07")
    n, max_upload = _raw_events(db)
    assert max_upload <= "2026-01-07", max_upload
    assert n < pins.RAW_EVENT_ROWS  # a strict subset of the whole landing


def test_dbt_build_no_through_loads_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THROUGH unset is the default build — every file, the Phase 2 pin. Also kills
    `event_files invert-guard` (through=None would fall into `_file_date <= None`
    and error)."""
    db = _build(tmp_path, monkeypatch)
    n, _ = _raw_events(db)
    assert n == pins.RAW_EVENT_ROWS


def test_build_refuses_bad_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed THROUGH is refused before any landing (it never becomes a
    path). Kills `validate_through invert-guard` (which would accept it)."""
    monkeypatch.setattr(loader, "DATA", tmp_path / "data")
    with pytest.raises(SystemExit):
        cli.dbt_build("tiny", "", through="not-a-date")
