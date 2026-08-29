"""The BigQuery landing (specs/phase-9b-bigquery-dialect.md invariants 3, 7):
the same files the DuckDB loader selects, the generated schema, a recreate —
against FAKE clients. No google client is ever built here: the default factory
is replaced by a sentinel that raises, so a code path constructing one goes red
offline instead of reaching the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loader import bq, cli
from loader import load as loader

TINY = loader.fixture_dir("tiny")


class Recorder:
    def __init__(self, project: str) -> None:
        self.project = project
        self.uploads: list[tuple[str, str, Path]] = []
        self.loads: list[tuple[str, list[str], dict]] = []

    def upload(self, bucket: str, name: str, path: Path) -> None:
        self.uploads.append((bucket, name, path))

    def load(self, table_id: str, uris: list[str], config: dict) -> int:
        self.loads.append((table_id, uris, config))
        return {"events": 970, "dim_user": 22}[table_id.rsplit(".", 1)[1]]


def _factory() -> tuple[list[Recorder], bq.ClientFactory]:
    made: list[Recorder] = []

    def make(project: str) -> Recorder:
        r = Recorder(project)
        made.append(r)
        return r

    return made, make


def test_selects_the_same_files_as_the_duckdb_loader() -> None:
    for through in (None, "2026-01-07", "2026-01-13", "2025-01-01"):
        files = bq.selected_files(TINY, through)
        assert files["events"] == loader.event_files(TINY, through)
        assert files["dim_user"] == [TINY / "dims" / "dim_user.csv"]
    assert len(bq.selected_files(TINY, "2026-01-07")["events"]) == 4
    assert bq.selected_files(TINY, "2025-01-01")["events"] == []


def test_uploads_then_loads_with_the_generated_schema() -> None:
    """Invariant 3: every selected file is uploaded under landing/<profile>/,
    then ONE load job per table over exactly those URIs, with the contract
    schema (loader/bq_schema.json — generated) and WRITE_TRUNCATE."""
    made, make = _factory()
    files, events, dims = bq.bq_load("tiny", "my-project", "2026-01-07", make)
    assert (files, events, dims) == (4, 970, 22)
    assert [r.project for r in made] == ["my-project"]
    r = made[0]
    assert all(b == "my-project-ontime" for b, _, _ in r.uploads)
    names = [n for _, n, _ in r.uploads]
    assert names == [
        *[f"landing/tiny/raw/{p.name}" for p in loader.event_files(TINY, "2026-01-07")],
        "landing/tiny/dims/dim_user.csv",
    ]
    assert [t for t, _, _ in r.loads] == [
        "my-project.raw.events",
        "my-project.raw.dim_user",
    ]
    ev_uris, dim_uris = r.loads[0][1], r.loads[1][1]
    assert ev_uris == [f"gs://my-project-ontime/{n}" for n in names[:-1]]
    assert dim_uris == [f"gs://my-project-ontime/{names[-1]}"]
    schema = json.loads(bq.SCHEMA_PATH.read_text())
    for (table_id, _, cfg), table in zip(r.loads, ("events", "dim_user"), strict=True):
        assert cfg["schema"] == schema[table], table_id
        assert cfg["write_disposition"] == "WRITE_TRUNCATE", table_id
    assert r.loads[0][2]["source_format"] == "NEWLINE_DELIMITED_JSON"
    assert r.loads[1][2]["source_format"] == "CSV"
    assert r.loads[1][2]["skip_leading_rows"] == 1
    assert r.loads[1][2]["null_marker"] == ""  # empty valid_to = the open row
    # the config is the only place the disposition lives (a mutation target)
    assert bq.load_job_config("events")["write_disposition"] == "WRITE_TRUNCATE"


def test_no_client_is_built_before_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Invariant 7: PROFILE, PROJECT, THROUGH and CONFIRM are checked before the
    factory is called; the default factory is a sentinel in this suite."""

    def sentinel(project: str) -> bq.Clients:
        raise AssertionError(f"a real client was requested for {project!r}")

    monkeypatch.setattr(bq, "GoogleClients", sentinel)
    bad = [
        ("../x", "my-project", "yes", "command line", ""),
        ("tiny", "", "yes", "command line", ""),
        ("tiny", "Bad Id", "yes", "command line", ""),
        ("tiny", "my-project", "yes", "environment", ""),
        ("tiny", "my-project", "", "command line", ""),
        ("tiny", "my-project", "yes", "command line", "../x"),
    ]
    for profile, project, confirm, origin, through in bad:
        with pytest.raises(SystemExit) as e:
            cli.bq_load(profile, project, confirm, origin, through, clients=sentinel)
        assert e.value.code == 2, (profile, project, confirm, origin, through)
    assert "refused" in capsys.readouterr().out
    # the valid call reaches the (fake) factory exactly once
    made, make = _factory()
    assert cli.bq_load("tiny", "my-project", "yes", "command line", clients=make) == 0
    assert len(made) == 1
    out = capsys.readouterr().out
    assert "bq-load: source=fixtures/tiny → my-project.raw" in out
    assert "bq-load OK: tiny — 10 files, 970 event rows, 22 dim rows" in out


def test_int_bigquery_entry_validates_and_gates_before_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    def never(*a: object, **k: object) -> None:
        raise AssertionError("pytest spawned before validation")

    monkeypatch.setattr(subprocess, "run", never)
    for profile, project, confirm, origin in (
        ("tiny", "", "yes", "command line"),
        ("tiny", "my-project", "yes", "environment"),
        ("../x", "my-project", "yes", "command line"),
    ):
        with pytest.raises(SystemExit) as e:
            cli.int_bigquery(profile, project, confirm, origin)
        assert e.value.code == 2
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], cwd: str, env: dict[str, str]) -> object:
        seen.update(argv=argv, env=env)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli.int_bigquery("tiny", "my-project", "yes", "command line") == 0
    assert seen["argv"][-1].endswith("tests/integration/test_int_bigquery.py")
    env = seen["env"]
    assert env["OTR_INT"] == "1" and env["OTR_GCP_PROJECT"] == "my-project"
