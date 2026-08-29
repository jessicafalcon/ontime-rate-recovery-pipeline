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


def test_through_loads_only_files_on_or_before(tmp_path: Path) -> None:
    """Phase 7: THROUGH filters landing files by upload date (a landing is the
    raw-table state); None loads them all."""
    kept = loader.event_files(TINY, through=pins.LANDING_SPLIT_TINY)
    assert [f.name for f in kept] == [
        f"events_{d}.jsonl"
        for d in (
            "2026-01-04",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
            "2026-01-09",
            "2026-01-10",
            "2026-01-11",
            "2026-01-12",
        )
    ]
    assert loader.event_files(TINY, through="2025-12-31") == []  # before every file
    assert loader.event_files(TINY, through=None) == loader.event_files(TINY)
    n_files, _, _ = loader.load(
        "tiny", tmp_path / "t.duckdb", through=pins.LANDING_SPLIT_TINY
    )
    assert n_files == pins.RAW_FILES - 1  # the late file (01-13) is not landed


def test_load_refuses_bad_through() -> None:
    """THROUGH must be an upload date and never becomes a path."""
    for bad in ("../x", 'a"; b', "2026-1-1", "13", "yesterday"):
        with pytest.raises(SystemExit) as e:
            cli.load("tiny", bad)
        assert e.value.code == 2


def test_full_refresh_only_on_command_line_yes() -> None:
    """FULL=yes adds --full-refresh only from the command line; an env FULL is
    ignored; a non-yes value is refused."""
    assert cli.full_refresh_args("yes", "command line") == ["--full-refresh"]
    assert cli.full_refresh_args("yes", "environment") == []
    assert cli.full_refresh_args("", "command line") == []
    assert cli.full_refresh_args("no", "command line") == []
    with pytest.raises(SystemExit) as e:  # a non-empty non-yes FULL is refused
        cli.dbt_build("tiny", "duckdb", full='"; rm', full_origin="command line")
    assert e.value.code == 2


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


def test_duplicate_insert_id_across_files_is_loaded_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw keeps both copies (the source has no unique test by design) — the
    loader never dedupes and never drops a file. Staging them to one row is
    the dbt unit test + tests/test_staging.py's job."""
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


def test_connection_session_is_utc_and_format_is_the_contract(tmp_path: Path) -> None:
    from generator.models import AMPLITUDE_TS

    assert loader.TS_FORMAT == AMPLITUDE_TS
    con = loader.connect(tmp_path / "t.duckdb")
    assert con.execute("select current_setting('TimeZone')").fetchone() == ("UTC",)
    con.close()


def test_column_spec_refuses_a_quoted_identifier(tmp_path: Path) -> None:
    con = loader.connect(tmp_path / "t.duckdb")
    loader.create_raw_tables(con)
    con.execute('alter table raw.dim_user add column "x\'y" varchar')
    with pytest.raises(ValueError, match="refusing column spec"):
        loader.column_spec(con, "dim_user")
    con.close()


def test_cloud_target_requires_confirm_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    for confirm, origin in (
        ("", "file"),
        ("yes", "environment"),
        ("no", "command line"),
    ):
        with pytest.raises(SystemExit) as e:
            cli.dbt_build("tiny", "bigquery", confirm, origin)
        assert e.value.code == 2
    # duckdb needs no confirmation (the validation error proves we got past the gate)
    with pytest.raises(SystemExit) as e:
        cli.dbt_build("nosuchprofile", "duckdb")
    assert e.value.code == 2


def test_bigquery_target_is_refused_before_9b(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Amendment S: a confirmed bigquery build is still refused before 9b —
    before load() (no DuckDB file appears) and before any dbt call."""

    def never(*a: object, **k: object) -> int:
        raise AssertionError("load() ran before the 9b refusal")

    monkeypatch.setattr(cli, "load", never)
    with pytest.raises(SystemExit) as e:
        cli.dbt_build("tiny", "bigquery", "yes", "command line")
    assert e.value.code == 2
    assert "lands in Phase 9b" in capsys.readouterr().out


def test_load_reports_its_source_and_refuses_manifest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(TINY, root / "fixtures" / "tiny")
    monkeypatch.setattr(loader, "ROOT", root)
    monkeypatch.setattr(loader, "DATA", root / "data")
    assert loader.manifest_drift(root / "fixtures" / "tiny") == []
    assert cli.load("tiny") == 0
    out = capsys.readouterr().out
    assert "load: source=fixtures/tiny\n" in out and "(unfrozen)" not in out
    # one edited byte in a frozen fixture → refused, exit 1, nothing loaded
    f = root / "fixtures" / "tiny" / "raw" / "events_2026-01-04.jsonl"
    f.write_text(f.read_text().replace("u-000008", "u-000009", 1))
    assert cli.load("tiny") == 1
    assert "load DRIFT: 1 files" in capsys.readouterr().out
    # an unfrozen profile (no manifest) loads from data/out and says so
    shutil.copytree(TINY, root / "data" / "out" / "mine")
    (root / "data" / "out" / "mine" / "MANIFEST.sha256").unlink()
    assert cli.load("mine") == 0
    assert "load: source=data/out/mine (unfrozen)" in capsys.readouterr().out


def test_manifest_check_reads_only_raw_and_dims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change confined to the side-file directory never touches the load;
    a change to a staged file refuses it."""
    import shutil

    root = tmp_path / "repo"
    fx = root / "fixtures" / "tiny"
    shutil.copytree(TINY, fx)
    monkeypatch.setattr(loader, "ROOT", root)
    monkeypatch.setattr(loader, "DATA", root / "data")
    other = next(
        d for d in fx.iterdir() if d.is_dir() and d.name not in ("raw", "dims")
    )
    victim = next(other.iterdir())
    victim.write_text(victim.read_text() + "\n")
    assert loader.manifest_drift(fx) == []
    assert cli.load("tiny") == 0
    (fx / "dims" / "dim_user.csv").write_text(
        (fx / "dims" / "dim_user.csv").read_text().replace("Europe/London", "UTC", 1)
    )
    assert loader.manifest_drift(fx) == ["dims/dim_user.csv: changed"]
    assert cli.load("tiny") == 1


def test_conflicting_duplicate_is_refused_at_landing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Same insert_id, same three clocks, different payload: never a dedupe
    choice — the load fails and leaves no raw tables behind."""
    a = _event("e-1", "2026-01-05 08:01:00.000000")
    b = dict(a, event_properties={"x": 1}, event_type="app_opened")
    same = dict(a)  # an exact copy is fine
    _mini_fixture(tmp_path, {"events_2026-01-05.jsonl": [a, b, same]})
    monkeypatch.setattr(loader, "ROOT", tmp_path)
    monkeypatch.setattr(loader, "DATA", tmp_path / "data")
    with pytest.raises(loader.ConflictingDuplicates, match="e-1"):
        loader.load("mini", tmp_path / "m.duckdb")
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    tables = {
        r[0]
        for r in con.execute(
            "select table_name from information_schema.tables"
        ).fetchall()
    }
    assert "events" not in tables
    con.close()
    assert cli.load("mini") == 1
    assert "load CONFLICT" in capsys.readouterr().out
    # make dbt-build reaches the same refusal and never starts dbt
    assert cli.dbt_build("mini", "duckdb") == 1
    assert "dbt-build" not in capsys.readouterr().out
    _mini_fixture(tmp_path / "ok", {"events_2026-01-05.jsonl": [a, same]})
    monkeypatch.setattr(loader, "ROOT", tmp_path / "ok")
    assert loader.load("mini", tmp_path / "ok.duckdb") == (1, 2, 1)


def test_distinct_insert_ids_sharing_all_clocks_are_not_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conflict key includes insert_id: two different ids with identical
    whole-second clocks and different payloads are two events, not a conflict
    (reachable at medium/prod scale; tiny has none)."""
    a = _event("e-1", "2026-01-05 08:01:00.000000")
    b = dict(_event("e-2", "2026-01-05 08:01:00.000000"), event_properties={"x": 1})
    _mini_fixture(tmp_path, {"events_2026-01-05.jsonl": [a, b]})
    monkeypatch.setattr(loader, "ROOT", tmp_path)
    monkeypatch.setattr(loader, "DATA", tmp_path / "data")
    assert loader.load("mini", tmp_path / "m.duckdb") == (1, 2, 1)
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    assert loader.conflicting_duplicates(con) == []
    con.close()


def test_profile_and_target_are_validated() -> None:
    for bad in ("", "../x", 'a"; b', "Tiny", "a b"):
        with pytest.raises(SystemExit) as e:
            cli.load(bad)
        assert e.value.code == 2
        with pytest.raises(SystemExit) as e:
            cli.dbt_build("tiny", bad or "../x", "yes", "command line")
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
    (tmp_path / "tiny.duckdb.wal").write_bytes(b"w")  # a leftover WAL replays
    (tmp_path / "other.duckdb.wal").write_bytes(b"w")
    assert cli.drop_db("tiny", "yes", "command line") == 0
    assert not (tmp_path / "tiny.duckdb").exists()
    assert not (tmp_path / "tiny.duckdb.wal").exists()
    assert (tmp_path / "other.duckdb").exists()
    assert (tmp_path / "other.duckdb.wal").exists()
    assert cli.drop_db("tiny", "yes", "command line") == 0  # idempotent
