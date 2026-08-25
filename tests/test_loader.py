"""Raw landing pins (spec Phase 2 invariants 1, 2, 5, 8). DuckDB in-process —
no service, no network; every db lives in tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from loader import cli
from loader import load as loader
from tests import pins

ROOT = Path(__file__).parent.parent
TINY = ROOT / "fixtures" / "tiny"


def test_loader_globs_every_raw_file(tmp_path: Path) -> None:
    files = loader.event_files(TINY)
    assert [f.name for f in files][0] == "events_2026-01-04.jsonl"  # the Tokyo day
    assert len(files) == pins.RAW_FILES
    n_files, n_events, n_dims = loader.load("tiny", tmp_path / "t.duckdb")
    assert (n_files, n_events, n_dims) == (
        pins.RAW_FILES,
        pins.RAW_EVENT_ROWS,
        pins.DIM_USER_ROWS,
    )
    assert n_events == sum(1 for f in files for _ in f.open())


def test_load_twice_gives_the_same_row_count(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    assert loader.load("tiny", db) == loader.load("tiny", db)


def test_empty_valid_to_loads_as_null(tmp_path: Path) -> None:
    loader.load("tiny", tmp_path / "t.duckdb")
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    total, closed = con.execute(
        "select count(*), count(valid_to) from raw.dim_user"
    ).fetchone()
    assert total == pins.DIM_USER_ROWS
    assert closed == pins.DIM_USER_CLOSED_ROWS
    assert (
        con.execute("select typeof(valid_to) from raw.dim_user limit 1").fetchone()[0]
        == "TIMESTAMP"
    )


def test_json_null_error_code_survives_as_json_null(tmp_path: Path) -> None:
    loader.load("tiny", tmp_path / "t.duckdb")
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    assert (
        con.execute(
            "select typeof(event_properties) from raw.events limit 1"
        ).fetchone()[0]
        == "JSON"
    )
    nulls = con.execute(
        "select count(*) from raw.events where event_type like 'upload_%' "
        "and json_extract_string(event_properties, 'error_code') is null"
    ).fetchone()[0]
    assert nulls == pins.RAW_UPLOAD_ERROR_CODE_NULLS
    # the key is still present (exact-keys rule), not dropped by inference
    keys = con.execute(
        "select count(*) from raw.events where event_type = 'upload_started' "
        "and not json_exists(event_properties, '$.error_code')"
    ).fetchone()[0]
    assert keys == 0


def _mini_fixture(root: Path, rows_by_file: dict[str, list[dict]]) -> Path:
    fx = root / "fixtures" / "mini"
    (fx / "raw").mkdir(parents=True)
    (fx / "dims").mkdir()
    for name, rows in rows_by_file.items():
        (fx / "raw" / name).write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        )
    (fx / "dims" / "dim_user.csv").write_text(
        "user_id,tz,cohort_id,signup_date,valid_from,valid_to\n"
        "u-000001,UTC,c-morning,2025-12-01,2025-12-01 00:00:00.000000,\n"
    )
    return fx


def _event(
    insert_id: str, upload: str, received: str = "2026-01-05 08:00:00.000000"
) -> dict:
    return {
        "insert_id": insert_id,
        "event_type": "app_opened",
        "user_id": "u-000001",
        "device_id": "d-000001",
        "client_event_time": "2026-01-05 08:00:00.000000",
        "server_received_time": received,
        "server_upload_time": upload,
        "event_properties": {},
    }


def test_duplicate_insert_id_across_files_is_loaded_twice_and_staged_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw keeps both copies (the source has no unique test by design); the
    staging dedupe is proven by the dbt unit test + tests/test_staging.py.
    Here: the loader itself never dedupes and never drops a file."""
    _mini_fixture(
        tmp_path,
        {
            "events_2026-01-05.jsonl": [_event("e-1", "2026-01-05 08:01:00.000000")],
            "events_2026-01-06.jsonl": [_event("e-1", "2026-01-06 08:01:00.000000")],
        },
    )
    monkeypatch.setattr(loader, "ROOT", tmp_path)
    monkeypatch.setattr(loader, "DATA", tmp_path / "data")
    assert loader.load("mini", tmp_path / "m.duckdb") == (2, 2, 1)
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    assert (
        con.execute("select count(distinct insert_id) from raw.events").fetchone()[0]
        == 1
    )


def test_profile_and_target_are_validated() -> None:
    for bad in ("", "../x", 'a"; b', "Tiny", "a b"):
        with pytest.raises(SystemExit) as e:
            cli.load(bad)
        assert e.value.code == 2
        with pytest.raises(SystemExit) as e:
            cli.dbt_build("tiny", bad or "../x")
        assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.load("nosuchprofile")
    assert e.value.code == 2


def test_drop_db_removes_only_the_named_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "DATA", tmp_path)
    (tmp_path / "tiny.duckdb").write_bytes(b"x")
    (tmp_path / "other.duckdb").write_bytes(b"y")
    for confirm, origin in (
        ("yes", "environment"),
        ("", "file"),
        ("no", "command line"),
    ):
        with pytest.raises(SystemExit) as e:
            cli.drop_db("tiny", confirm, origin)
        assert e.value.code == 2
    assert (tmp_path / "tiny.duckdb").exists()
    with pytest.raises(SystemExit):
        cli.drop_db("../x", "yes", "command line")
    assert cli.drop_db("tiny", "yes", "command line") == 0
    assert not (tmp_path / "tiny.duckdb").exists()
    assert (tmp_path / "other.duckdb").exists()
    assert cli.drop_db("tiny", "yes", "command line") == 0  # idempotent
