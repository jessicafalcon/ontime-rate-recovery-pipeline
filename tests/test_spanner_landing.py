"""Phase 10: the Spanner dims landing (loader/spanner.py) against fakes — the
seed lands with the generated contract's columns and types, idempotently; the
CLI gates before any client. No service, no network."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from loader import spanner
from tests import pins


class FakeDim:
    """The one call the landing makes, recorded."""

    def __init__(self) -> None:
        self.table: dict[tuple, tuple] = {}
        self.upserts = 0

    def upsert(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        assert table == "dim_user"
        self.columns = columns
        self.upserts += 1
        for r in rows:
            self.table[(r[0], r[4])] = r  # key (user_id, valid_from)


def test_load_dims_lands_the_seed_with_contract_types() -> None:
    store = FakeDim()
    n = spanner.load_dims("tiny", "my-proj", clients=lambda p: store)
    assert n == pins.DIM_USER_ROWS
    assert len(store.table) == pins.DIM_USER_ROWS
    assert store.columns == tuple(f["name"] for f in spanner.dim_fields())
    for row in store.table.values():
        assert isinstance(row[3], date)  # signup_date
        assert isinstance(row[4], datetime) and row[4].tzinfo is UTC  # valid_from
        assert row[5] is None or (
            isinstance(row[5], datetime) and row[5].tzinfo is UTC
        )  # valid_to: NULL = the open SCD2 row
    open_rows = [r for r in store.table.values() if r[5] is None]
    assert len(open_rows) == 20  # one open row per user (two users changed tz)


def test_load_dims_is_idempotent() -> None:
    """Same seed twice → the same table state (insert_or_update on the same
    key with the same values)."""
    store = FakeDim()
    spanner.load_dims("tiny", "my-proj", clients=lambda p: store)
    before = dict(store.table)
    spanner.load_dims("tiny", "my-proj", clients=lambda p: store)
    assert store.table == before
    assert store.upserts == 2


def test_header_drift_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A seed whose header drifted from the generated contract refuses — the
    landing never maps columns by position onto a different shape."""
    dims = tmp_path / "dims"
    dims.mkdir()
    (dims / "dim_user.csv").write_text("user_id,tz\nu-1,UTC\n")
    monkeypatch.setattr(spanner.loader, "fixture_dir", lambda p: tmp_path)
    store = FakeDim()
    with pytest.raises(ValueError, match="header"):
        spanner.load_dims("tiny", "my-proj", clients=lambda p: store)
    assert store.upserts == 0


def test_int_spanner_fixture_refuses_without_the_carried_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Amendment V shape, pinned for the Spanner run too: with no
    OTR_CONFIRM / OTR_CONFIRM_ORIGIN pair the integration module refuses before
    any cloud call — a bare `pytest` with OTR_INT=1 cannot forge the gate."""
    from tests.integration import test_int_spanner as integ

    for var in ("OTR_CONFIRM", "OTR_CONFIRM_ORIGIN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="refused"):
        integ.carried_gate()
    monkeypatch.setenv("OTR_CONFIRM", "yes")
    with pytest.raises(RuntimeError, match="refused"):
        integ.carried_gate()


def test_spanner_load_cli_gates_before_any_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spanner-load refuses (exit 2) on a missing/env CONFIRM, a bad PROJECT or
    PROFILE — before the client factory is resolved."""
    from loader import cli

    def boom() -> None:
        raise AssertionError("client factory resolved before the gate")

    monkeypatch.setattr(spanner, "default_clients", boom)
    cases = [
        ("tiny", "ontime-rate-recovery", "", ""),
        ("tiny", "ontime-rate-recovery", "yes", "environment"),
        ("tiny", "../x", "yes", "command line"),
        ("../x", "ontime-rate-recovery", "yes", "command line"),
    ]
    for args in cases:
        with pytest.raises(SystemExit) as e:
            cli.spanner_load(*args)
        assert e.value.code == 2, args
