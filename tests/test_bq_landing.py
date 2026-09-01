"""The BigQuery landing (specs/phase-9b-bigquery-dialect.md invariants 3, 7):
the same files the DuckDB landing selects, the generated schema, ONE
WRITE_TRUNCATE load job per table (a zero-byte object for an empty selection —
Amendment X) — against FAKE clients. No google client is ever built here: the
default factory is replaced by a sentinel that raises, so a code path
constructing one goes red offline instead of reaching the network."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from landing import bq, cli
from landing import load as landing
from pipeline import cli as pipeline_cli

TINY = landing.fixture_dir("tiny")
ROOT = Path(__file__).parent.parent


class Recorder:
    def __init__(self, project: str) -> None:
        self.project = project
        self.uploads: list[tuple[str, str, Path]] = []
        self.loads: list[tuple[str, list[str], dict]] = []
        self.sizes: dict[str, int] = {}

    def upload(self, bucket: str, name: str, path: Path) -> None:
        self.uploads.append((bucket, name, path))
        self.sizes[name] = path.stat().st_size  # read now: a temp file may vanish

    def load(self, table_id: str, uris: list[str], config: dict) -> int:
        """Rows follow the URIs, as BigQuery's would: a zero-byte object loads
        0 rows (round 4 #2 — a constant could not pin the empty landing)."""
        self.loads.append((table_id, uris, config))
        if all(u.endswith(bq.EMPTY_OBJECT) for u in uris):
            return 0
        return {"events": 970, "dim_user": 22}[table_id.rsplit(".", 1)[1]]


def _factory() -> tuple[list[Recorder], bq.ClientFactory]:
    made: list[Recorder] = []

    def make(project: str) -> Recorder:
        r = Recorder(project)
        made.append(r)
        return r

    return made, make


def test_selects_the_same_files_as_the_duckdb_landing() -> None:
    for through in (None, "2026-01-07", "2026-01-13", "2025-01-01"):
        files = bq.selected_files(TINY, through)
        assert files["events"] == landing.event_files(TINY, through)
        assert files["dim_user"] == [TINY / "dims" / "dim_user.csv"]
    assert len(bq.selected_files(TINY, "2026-01-07")["events"]) == 4
    assert bq.selected_files(TINY, "2025-01-01")["events"] == []


def test_uploads_then_loads_with_the_generated_schema() -> None:
    """Invariant 3: every selected file is uploaded under landing/<profile>/,
    then ONE load job per table over exactly those URIs, with the contract
    schema (landing/bq_schema.json — generated) and WRITE_TRUNCATE."""
    made, make = _factory()
    files, events, dims = bq.bq_load("tiny", "my-project", "2026-01-07", make)
    assert (files, events, dims) == (4, 970, 22)
    assert [r.project for r in made] == ["my-project"]
    r = made[0]
    assert all(b == "my-project-ontime" for b, _, _ in r.uploads)
    names = [n for _, n, _ in r.uploads]
    assert names == [
        *[
            f"landing/tiny/raw/{p.name}"
            for p in landing.event_files(TINY, "2026-01-07")
        ],
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
    assert all(bq.EMPTY_OBJECT not in n for _, n, _ in r.uploads)  # no empty marker


def test_landing_reads_nothing_and_uses_one_mechanism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Amendment X (invariant 3): the `Clients` protocol declares upload and
    load ONLY — no read, query or DDL — so a landing cannot depend on prior
    table state; every table id follows RAW_DATASET (round 3 #4); the source
    interpolates no identifier into SQL."""
    assert sorted(n for n in vars(bq.Clients) if not n.startswith("_")) == [
        "load",
        "upload",
    ]
    monkeypatch.setattr(bq, "RAW_DATASET", "zz")
    made, make = _factory()
    bq.bq_load("tiny", "my-project", None, make)
    assert [t for t, _, _ in made[0].loads] == [
        "my-project.zz.events",
        "my-project.zz.dim_user",
    ]
    for module in ("bq.py", "cli.py"):  # round 4 #1: the whole cloud path
        src = (ROOT / "landing" / module).read_text()
        for forbidden in (".query(", "truncate", "delete_table", "create_table"):
            assert forbidden not in src, (module, forbidden)


def test_cli_empty_landing_succeeds_and_reports_zero(
    capsys: pytest.CaptureFixture,
) -> None:
    """Round 4 #1/#2: the CLI path to a successful EMPTY landing — the line
    Evidence row 4 quotes, off the fake whose rows follow the URIs."""
    made, make = _factory()
    rc = cli.bq_load("tiny", "my-project", "yes", "command line", "2025-01-01", make)
    assert rc == 0
    out = capsys.readouterr().out
    assert (
        "bq-load OK: tiny — 0 files, landing ≤ 2025-01-01, 0 event rows, 22 dim rows"
        in out
    )
    assert [t for t, _, _ in made[0].loads] == [
        "my-project.raw.events",
        "my-project.raw.dim_user",
    ]


def test_empty_selection_lands_a_zero_byte_object_through_the_same_job() -> None:
    """Amendments W → X (invariant 3): a THROUGH before the first upload lands
    an EMPTY raw.events by the ONE mechanism — a zero-byte `_empty.jsonl`
    uploaded and loaded with the contract schema + WRITE_TRUNCATE (BigQuery
    rejects a job over zero URIs; a second mechanism was the cap's cause) —
    and the dim seed as usual: the same function of (fixture, THROUGH) as the
    DuckDB landing's exit-0-empty."""
    made, make = _factory()
    files, events, dims = bq.bq_load("tiny", "my-project", "2025-01-01", make)
    assert (files, events, dims) == (0, 0, 22)
    r = made[0]
    empties = [(n, p) for _, n, p in r.uploads if n.endswith(bq.EMPTY_OBJECT)]
    assert len(empties) == 1 and empties[0][0] == "landing/tiny/raw/_empty.jsonl"
    assert r.sizes[empties[0][0]] == 0
    ev_id, ev_uris, ev_cfg = r.loads[0]
    assert ev_id == "my-project.raw.events"
    assert ev_uris == ["gs://my-project-ontime/landing/tiny/raw/_empty.jsonl"]
    assert ev_cfg == bq.load_job_config("events")  # the same job, same schema
    assert [t for t, _, _ in r.loads][1] == "my-project.raw.dim_user"


def test_no_client_is_built_before_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Invariant 7: PROFILE, PROJECT, THROUGH and CONFIRM are checked before the
    factory is called; the DEFAULT factory (what a caller gets when it passes
    none — resolved at call time, review round 1 #1) is a sentinel here, so the
    refusals below are exercised WITHOUT `clients=`."""

    def sentinel(project: str) -> bq.Clients:
        raise AssertionError(f"a real client was requested for {project!r}")

    assert bq.default_clients() is bq.GoogleClients  # round 2 #1: the real one
    monkeypatch.setattr(bq, "default_clients", lambda: sentinel)
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
            cli.bq_load(profile, project, confirm, origin, through)
        assert e.value.code == 2, (profile, project, confirm, origin, through)
    assert "refused" in capsys.readouterr().out
    # a VALID call with no clients= reaches the (sentinel) default — the control
    # is real, not a claim
    with pytest.raises(AssertionError, match="a real client was requested"):
        cli.bq_load("tiny", "my-project", "yes", "command line")
    with pytest.raises(AssertionError, match="a real client was requested"):
        bq.bq_load("tiny", "my-project")
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
            pipeline_cli.int_bigquery(profile, project, confirm, origin)
        assert e.value.code == 2
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], cwd: str, env: dict[str, str]) -> object:
        seen.update(argv=argv, env=env)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert pipeline_cli.int_bigquery("tiny", "my-project", "yes", "command line") == 0
    assert seen["argv"][-1].endswith("tests/integration/test_int_bigquery.py")
    env = seen["env"]
    assert env["OTR_INT"] == "1" and env["OTR_GCP_PROJECT"] == "my-project"
    # Amendment V: the gate that ran here is carried to the fixture verbatim
    assert env["OTR_CONFIRM"] == "yes" and env["OTR_CONFIRM_ORIGIN"] == "command line"


def test_parity_fixture_refuses_without_the_carried_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Amendment V's own pin (round 2 #3): with no OTR_CONFIRM /
    OTR_CONFIRM_ORIGIN in the env the parity module refuses before any build;
    with the pair it passes them through unchanged, never a forged literal."""
    from tests.integration import test_int_bigquery as parity

    for var in ("OTR_CONFIRM", "OTR_CONFIRM_ORIGIN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="refused"):
        parity.carried_gate()
    monkeypatch.setenv("OTR_CONFIRM", "yes")
    with pytest.raises(RuntimeError, match="refused"):
        parity.carried_gate()
    monkeypatch.setenv("OTR_CONFIRM_ORIGIN", "environment")
    with pytest.raises(RuntimeError, match="refused"):
        parity.carried_gate()  # round 2 #7: the origin is re-checked, not just present
    monkeypatch.setenv("OTR_CONFIRM_ORIGIN", "command line")
    assert parity.carried_gate() == ("yes", "command line")  # passed through
    src = (ROOT / "tests" / "integration" / "test_int_bigquery.py").read_text()
    assert '"command line"' not in src  # never forged in the module
    # round 3 #10: the planted conflict is always cleaned up, and #9: the
    # insert names its columns from the contract
    cleanup = r"finally:\n\s+client\.query\(f\"delete from \{table\} where insert_id"
    assert re.search(cleanup, src)
    assert "insert into {table} ({columns}) values" in src


def test_duplicate_guard_keys_are_the_contract() -> None:
    """Round 2 #2: the singular test's key list equals the union of
    generator/models.py::PROPERTY_KEYS — a dropped or invented key is red."""
    from generator.models import PROPERTY_KEYS

    sql = (ROOT / "dbt" / "tests" / "assert_no_conflicting_duplicates.sql").read_text()
    m = re.search(r"set keys = \[(.*?)\]", sql)
    listed = {k.strip().strip("'") for k in m.group(1).split(",")}
    assert listed == set().union(*PROPERTY_KEYS.values())


def _tf_default(var: str) -> str:
    text = (ROOT / "infra" / "variables.tf").read_text()
    block = re.search(r'variable "' + var + r'" \{(.*?)\n\}', text, re.S).group(1)
    return re.search(r'default\s*=\s*"([^"]*)"', block).group(1)


def test_dataset_and_bucket_names_are_the_terraform_defaults() -> None:
    """Review round 1 #8: the landing's dataset ids and the derived bucket name
    are Terraform's, pinned like `location` — a default change in `infra/`
    fails offline, not only in the cloud."""
    from tests.integration import test_int_bigquery as parity

    assert bq.RAW_DATASET == _tf_default("raw_dataset")
    assert parity.MODELS_DATASET == _tf_default("models_dataset")
    profiles = (ROOT / "dbt" / "profiles.yml").read_text()  # round 2 #10
    assert re.search(
        r"^\s*dataset:\s*" + _tf_default("models_dataset") + r"\s*$", profiles, re.M
    )
    main = (ROOT / "infra" / "main.tf").read_text()
    m = re.search(r'staging_bucket\s*=\s*"\$\{var\.project_id\}(-[a-z0-9-]+)"', main)
    assert m and bq.bucket_name("p") == "p" + m.group(1)
