"""Raw landing pins (spec Phase 2 invariants 1, 2, 5, 8). DuckDB in-process —
no service, no network; every db lives in tmp_path."""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

from landing import cli
from landing import load as landing
from pipeline import cli as pipeline_cli
from tests import pins

ROOT = Path(__file__).parent.parent
TINY = ROOT / "fixtures" / "tiny"


def test_loader_globs_every_raw_file(tmp_path: Path) -> None:
    files = landing.event_files(TINY)
    assert [f.name for f in files][0] == "events_2026-01-04.jsonl"  # the Tokyo day
    assert len(files) == pins.RAW_FILES
    n_files, n_events, n_dims = landing.load("tiny", tmp_path / "t.duckdb")
    assert (n_files, n_events, n_dims) == (
        pins.RAW_FILES,
        pins.RAW_EVENT_ROWS,
        pins.DIM_USER_ROWS,
    )
    assert n_events == sum(1 for f in files for _ in f.open())


def test_load_twice_gives_the_same_row_count(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    assert landing.load("tiny", db) == landing.load("tiny", db)


def test_through_loads_only_files_on_or_before(tmp_path: Path) -> None:
    """Phase 7: THROUGH filters landing files by upload date (a landing is the
    raw-table state); None loads them all."""
    kept = landing.event_files(TINY, through=pins.LANDING_SPLIT_TINY)
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
    assert landing.event_files(TINY, through="2025-12-31") == []  # before every file
    assert landing.event_files(TINY, through=None) == landing.event_files(TINY)
    n_files, _, _ = landing.load(
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
    assert pipeline_cli.full_refresh_args("yes", "command line") == ["--full-refresh"]
    assert pipeline_cli.full_refresh_args("yes", "environment") == []
    assert pipeline_cli.full_refresh_args("", "command line") == []
    assert pipeline_cli.full_refresh_args("no", "command line") == []
    with pytest.raises(SystemExit) as e:  # a non-empty non-yes FULL is refused
        pipeline_cli.dbt_build(
            "tiny", "duckdb", full='"; rm', full_origin="command line"
        )
    assert e.value.code == 2


def test_empty_valid_to_loads_as_null(tmp_path: Path) -> None:
    landing.load("tiny", tmp_path / "t.duckdb")
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
    landing.load("tiny", tmp_path / "t.duckdb")
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
    landing never dedupes and never drops a file. Staging them to one row is
    the dbt unit test + tests/test_staging.py's job."""
    _mini_fixture(
        tmp_path,
        {
            "events_2026-01-05.jsonl": [_event("e-1", "2026-01-05 08:01:00.000000")],
            "events_2026-01-06.jsonl": [_event("e-1", "2026-01-06 08:01:00.000000")],
        },
    )
    monkeypatch.setattr(landing, "ROOT", tmp_path)
    monkeypatch.setattr(landing, "DATA", tmp_path / "data")
    assert landing.load("mini", tmp_path / "m.duckdb") == (2, 2, 1)
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    assert (
        con.execute("select count(distinct insert_id) from raw.events").fetchone()[0]
        == 1
    )


def test_connection_session_is_utc_and_format_is_the_contract(tmp_path: Path) -> None:
    from generator.models import AMPLITUDE_TS

    assert landing.TS_FORMAT == AMPLITUDE_TS
    con = landing.connect(tmp_path / "t.duckdb")
    assert con.execute("select current_setting('TimeZone')").fetchone() == ("UTC",)
    con.close()


def test_column_spec_refuses_a_quoted_identifier(tmp_path: Path) -> None:
    con = landing.connect(tmp_path / "t.duckdb")
    landing.create_raw_tables(con)
    con.execute('alter table raw.dim_user add column "x\'y" varchar')
    with pytest.raises(ValueError, match="refusing column spec"):
        landing.column_spec(con, "dim_user")
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
            pipeline_cli.dbt_build("tiny", "bigquery", confirm, origin)
        assert e.value.code == 2
    # duckdb needs no confirmation (the validation error proves we got past the gate)
    with pytest.raises(SystemExit) as e:
        pipeline_cli.dbt_build("nosuchprofile", "duckdb")
    assert e.value.code == 2


class _FakeClients:
    """The two cloud calls, recorded (landing/bq.py Clients)."""

    calls: list[tuple] = []

    def __init__(self, project: str) -> None:
        _FakeClients.calls.append(("init", project))

    def upload(self, bucket: str, name: str, path: Path) -> None:
        _FakeClients.calls.append(("upload", bucket, name, path.name))

    def load(self, table_id: str, uris: list[str], config: dict) -> int:
        _FakeClients.calls.append(("load", table_id, tuple(uris), config))
        return 7


def test_bigquery_target_needs_a_validated_project(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Phase 9b (the 9b form of Amendment S's test): a confirmed bigquery build
    with an empty / path-shaped / malformed PROJECT exits 2 before any landing
    and any dbt call, and OTR_GCP_PROJECT is never set."""

    def never(*a: object, **k: object) -> int:
        raise AssertionError("a landing ran before PROJECT was validated")

    monkeypatch.setattr(cli, "land", never)
    monkeypatch.setitem(os.environ, "OTR_GCP_PROJECT", "sentinel")  # restored
    os.environ.pop("OTR_GCP_PROJECT")
    for bad in ("", "../x", "Bad Id", "my-proj\n", "x"):
        with pytest.raises(SystemExit) as e:
            pipeline_cli.dbt_build(
                "tiny", "bigquery", "yes", "command line", project=bad
            )
        assert e.value.code == 2, bad
        assert "PROJECT" in capsys.readouterr().out
        assert "OTR_GCP_PROJECT" not in os.environ
    with pytest.raises(SystemExit) as e:  # no third target
        pipeline_cli.dbt_build(
            "tiny", "spanner", "yes", "command line", project="my-project"
        )
    assert e.value.code == 2


def test_bigquery_build_lands_through_bq_not_duckdb(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Phase 9b invariant 4 (closes the 8b BACKLOG row): the bigquery build's
    landing is the BigQuery one — the DuckDB load() never runs — with the
    validated PROJECT exported to dbt from inside the process; the duckdb
    build's landing is load() and the fake clients are never built."""
    monkeypatch.setitem(os.environ, "OTR_GCP_PROJECT", "sentinel")  # restored
    os.environ.pop("OTR_GCP_PROJECT")

    def duckdb_never(*a: object, **k: object) -> int:
        raise AssertionError("the DuckDB load() ran for TARGET=bigquery")

    seen: dict[str, object] = {}

    class Runner:
        def invoke(self, args: list[str]) -> object:
            seen["args"] = args
            seen["env"] = os.environ.get("OTR_GCP_PROJECT")
            return type("R", (), {"success": True})()

    import dbt.cli.main as dbt_main

    monkeypatch.setattr(dbt_main, "dbtRunner", Runner)
    monkeypatch.setattr(cli, "load", duckdb_never)
    _FakeClients.calls = []
    rc = pipeline_cli.dbt_build(
        "tiny",
        "bigquery",
        "yes",
        "command line",
        through="2026-01-07",
        project="my-project",
        clients=_FakeClients,
    )
    assert rc == 0
    assert seen["env"] == "my-project" and "--target" in seen["args"]
    assert seen["args"][seen["args"].index("--target") + 1] == "bigquery"
    kinds = [c[0] for c in _FakeClients.calls]
    assert kinds[0] == "init" and "upload" in kinds and kinds.count("load") == 2
    uploads = [c for c in _FakeClients.calls if c[0] == "upload"]
    assert all(c[1] == "my-project-ontime" for c in uploads)
    # THROUGH selected the same subset the DuckDB landing would (4 files ≤ 01-07)
    assert len([u for u in uploads if u[3].startswith("events_")]) == len(
        landing.event_files(landing.fixture_dir("tiny"), "2026-01-07")
    )
    assert "bq-load OK: tiny — 4 files, landing ≤ 2026-01-07" in capsys.readouterr().out
    # the duckdb build: load() runs, no client is built
    monkeypatch.setattr(cli, "load", lambda p, t="": 0)
    _FakeClients.calls = []
    assert pipeline_cli.dbt_build("tiny", "duckdb", clients=_FakeClients) == 0
    assert _FakeClients.calls == []


def test_load_reports_its_source_and_refuses_manifest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(TINY, root / "fixtures" / "tiny")
    monkeypatch.setattr(landing, "ROOT", root)
    monkeypatch.setattr(landing, "DATA", root / "data")
    assert landing.manifest_drift(root / "fixtures" / "tiny") == []
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
    monkeypatch.setattr(landing, "ROOT", root)
    monkeypatch.setattr(landing, "DATA", root / "data")
    other = next(
        d for d in fx.iterdir() if d.is_dir() and d.name not in ("raw", "dims")
    )
    victim = next(other.iterdir())
    victim.write_text(victim.read_text() + "\n")
    assert landing.manifest_drift(fx) == []
    assert cli.load("tiny") == 0
    (fx / "dims" / "dim_user.csv").write_text(
        (fx / "dims" / "dim_user.csv").read_text().replace("Europe/London", "UTC", 1)
    )
    assert landing.manifest_drift(fx) == ["dims/dim_user.csv: changed"]
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
    monkeypatch.setattr(landing, "ROOT", tmp_path)
    monkeypatch.setattr(landing, "DATA", tmp_path / "data")
    with pytest.raises(landing.ConflictingDuplicates, match="e-1"):
        landing.load("mini", tmp_path / "m.duckdb")
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
    assert pipeline_cli.dbt_build("mini", "duckdb") == 1
    assert "dbt-build" not in capsys.readouterr().out
    _mini_fixture(tmp_path / "ok", {"events_2026-01-05.jsonl": [a, same]})
    monkeypatch.setattr(landing, "ROOT", tmp_path / "ok")
    assert landing.load("mini", tmp_path / "ok.duckdb") == (1, 2, 1)


def test_distinct_insert_ids_sharing_all_clocks_are_not_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conflict key includes insert_id: two different ids with identical
    whole-second clocks and different payloads are two events, not a conflict
    (reachable at medium/prod scale; tiny has none)."""
    a = _event("e-1", "2026-01-05 08:01:00.000000")
    b = dict(_event("e-2", "2026-01-05 08:01:00.000000"), event_properties={"x": 1})
    _mini_fixture(tmp_path, {"events_2026-01-05.jsonl": [a, b]})
    monkeypatch.setattr(landing, "ROOT", tmp_path)
    monkeypatch.setattr(landing, "DATA", tmp_path / "data")
    assert landing.load("mini", tmp_path / "m.duckdb") == (1, 2, 1)
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    assert landing.conflicting_duplicates(con) == []
    con.close()


def test_profile_and_target_are_validated() -> None:
    for bad in ("", "../x", 'a"; b', "Tiny", "a b"):
        with pytest.raises(SystemExit) as e:
            cli.load(bad)
        assert e.value.code == 2
        with pytest.raises(SystemExit) as e:
            pipeline_cli.dbt_build("tiny", bad or "../x", "yes", "command line")
        assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.load("nosuchprofile")
    assert e.value.code == 2


def test_drop_db_removes_only_the_named_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(landing, "DATA", tmp_path)
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
