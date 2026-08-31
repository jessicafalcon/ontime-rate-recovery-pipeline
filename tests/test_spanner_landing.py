"""Phase 10: the Spanner dims landing (loader/spanner.py) against fakes — the
seed lands with the generated contract's columns and types, idempotently; the
CLI gates before any client. No service, no network."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from loader import cli, spanner
from loader import load as loader
from tests import pins

ROOT = Path(__file__).parent.parent
TINY = ROOT / "fixtures" / "tiny"


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
    # one open row per user (the two tz-change users each closed one row)
    assert len(open_rows) == pins.DIM_USER_ROWS - pins.DIM_USER_CLOSED_ROWS


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
    monkeypatch.setenv("OTR_CONFIRM_ORIGIN", "environment")
    with pytest.raises(RuntimeError, match="refused"):
        integ.carried_gate()  # round 2 #7: an env-origin pair is refused here too
    monkeypatch.setenv("OTR_CONFIRM_ORIGIN", "command line")
    assert integ.carried_gate() == ("yes", "command line")
    src = (ROOT / "tests" / "integration" / "test_int_spanner.py").read_text()
    assert '"command line"' not in src  # never forged: loader.cli.confirmed decides


def test_spanner_load_cli_gates_before_any_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spanner-load refuses (exit 2) on a missing/env CONFIRM, a bad PROJECT or
    PROFILE — before the client factory is resolved."""

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


@pytest.mark.parametrize("target", ["spanner-load", "bq-load"])
def test_cloud_landings_refuse_manifest_drift(
    target: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Round 1 #6: one edited byte in a frozen fixture → `<target> DRIFT`, exit
    1, and the client is never called — the same pin `load` has
    (tests/test_loader.py), for both cloud landings (bq-load shared the gap)."""
    root = tmp_path / "repo"
    shutil.copytree(TINY, root / "fixtures" / "tiny")
    monkeypatch.setattr(loader, "ROOT", root)
    monkeypatch.setattr(loader, "DATA", root / "data")
    f = root / "fixtures" / "tiny" / "dims" / "dim_user.csv"
    f.write_text(f.read_text().replace("u-000008", "u-000009", 1))
    calls: list[str] = []

    class Never:
        def __init__(self, project: str) -> None:
            calls.append(project)

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"{name} called on a drifted fixture")

    args = ("tiny", "ontime-rate-recovery", "yes", "command line")
    if target == "spanner-load":
        rc = cli.spanner_load(*args, clients=Never)
    else:
        rc = cli.bq_load(*args, clients=Never)
    assert rc == 1
    assert f"{target} DRIFT: 1 files" in capsys.readouterr().out
    assert calls == []


def test_dbt_build_admits_exactly_one_var_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 1 #5: the internal `dim_user_identifier` seam is the ONLY var a
    build can override, it is validated like a name, and it reaches dbt as
    `--vars {dim_user_identifier: <relation>}` — deleting the append or
    widening the seam reddens here."""
    assert cli.dbt_vars_args("") == []
    assert cli.dbt_vars_args("dim_user_spanner") == [
        "--vars",
        "{dim_user_identifier: dim_user_spanner}",
    ]
    for bad in ("../x", 'a"; rm', "dim_user_spanner, model_version: x1", "A"):
        with pytest.raises(SystemExit) as e:
            cli.dbt_vars_args(bad)
        assert e.value.code == 2, bad
    seen: dict[str, list[str]] = {}

    class Runner:
        def invoke(self, args: list[str]) -> object:
            seen["args"] = args
            return type("R", (), {"success": True})()

    import dbt.cli.main as dbt_main

    monkeypatch.setattr(dbt_main, "dbtRunner", Runner)
    monkeypatch.setattr(cli, "load", lambda p, t="": 0)
    assert cli.dbt_build("tiny", "duckdb", dim_user_identifier="dim_user_spanner") == 0
    assert seen["args"][-2:] == ["--vars", "{dim_user_identifier: dim_user_spanner}"]
    assert cli.dbt_build("tiny", "duckdb") == 0
    assert "--vars" not in seen["args"]  # the default build is unchanged
    with pytest.raises(SystemExit):
        cli.dbt_build("tiny", "duckdb", dim_user_identifier="../x")


def test_int_spanner_cli_refuses_a_non_tiny_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 1 #23: the integration run's pins are tiny's; another PROFILE is
    a CLI refusal (exit 2) before the gate carries anything to pytest."""
    import subprocess

    def never(*a: object, **k: object) -> object:
        raise AssertionError("pytest spawned for a refused profile")

    monkeypatch.setattr(subprocess, "run", never)
    for profile in ("medium", "../x", ""):
        with pytest.raises(SystemExit) as e:
            cli.int_spanner(profile, "ontime-rate-recovery", "yes", "command line")
        assert e.value.code == 2, profile
    assert cli.INT_PROFILE == "tiny"


def test_spanner_clients_disable_the_builtin_metrics_exporter() -> None:
    """Round 1 #8: every google-cloud-spanner Client the repo constructs passes
    `disable_builtin_metrics=True` — no Cloud Monitoring exporter thread, no
    unreviewed egress, no failing exports under a SA with no monitoring grant
    (dbt telemetry is off for the same reason)."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    calls: list[tuple[str, str]] = []
    for rel in tracked:  # the whole tracked tree (round 2 #5), not a fixed list
        text = (ROOT / rel).read_text()
        calls += [(rel, c) for c in re.findall(r"spanner\.Client\(([^)]*)\)", text)]
    assert {rel for rel, _ in calls} == {"loader/spanner.py", "serving/spanner.py"}, (
        calls
    )
    assert all("disable_builtin_metrics=True" in c for _, c in calls), calls


def test_cell_refuses_instead_of_coercing() -> None:
    """Round 2 #10: an empty REQUIRED cell and an offset-bearing timestamp are
    refusals (the contract is naive UTC wall times and REQUIRED means
    present), beside the values the contract does admit."""
    fields = {f["name"]: f for f in spanner.dim_fields()}
    assert spanner._cell("", fields["valid_to"]) is None  # NULLABLE: the open row
    with pytest.raises(ValueError, match="REQUIRED"):
        spanner._cell("", fields["user_id"])
    with pytest.raises(ValueError, match="REQUIRED"):
        spanner._cell("", fields["valid_from"])
    with pytest.raises(ValueError, match="offset"):
        spanner._cell("2026-01-01T00:00:00+09:00", fields["valid_from"])
    with pytest.raises(ValueError, match="offset"):
        spanner._cell("2026-01-01T00:00:00Z", fields["valid_to"])
    got = spanner._cell("2026-01-01T00:00:00", fields["valid_from"])
    assert got == datetime(2026, 1, 1, tzinfo=UTC)
    assert spanner._cell("2026-01-01", fields["signup_date"]) == date(2026, 1, 1)


@pytest.mark.parametrize(
    "row",
    [
        "u-1,UTC,c,2026-01-01,2026-01-01T00:00:00",
        "u-1,UTC,c,2026-01-01,2026-01-01T00:00:00,,extra",
    ],
)
def test_row_width_drift_refuses(
    row: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 #11 (the tester's surviving mutation): a data row with fewer or
    more cells than the contract refuses with the line number — never a
    truncated or positionally-shifted landing."""
    dims = tmp_path / "dims"
    dims.mkdir()
    header = ",".join(f["name"] for f in spanner.dim_fields())
    (dims / "dim_user.csv").write_text(f"{header}\n{row}\n")
    monkeypatch.setattr(spanner.loader, "fixture_dir", lambda p: tmp_path)
    store = FakeDim()
    with pytest.raises(ValueError, match="line 2"):
        spanner.load_dims("tiny", "my-proj", clients=lambda p: store)
    assert store.upserts == 0


CLOUD_ENTRY_POINTS = [
    "bq-load",
    "spanner-load",
    "dbt-build:bigquery",
    "test-int-bigquery",
    "test-int-spanner",
    "writeback:spanner",
]


@pytest.mark.parametrize("entry", CLOUD_ENTRY_POINTS)
@pytest.mark.parametrize(
    "var",
    [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CREDENTIALS",
        "GOOGLE_BACKUP_CREDENTIALS_JSON",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "CLOUDSDK_AUTH_ACCESS_TOKEN",
    ],
)
def test_every_cloud_command_refuses_a_credential_in_the_env(
    entry: str, var: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Round 2 #2: a key file / inline key / bearer token in the environment
    would silently become the identity of every google client. Every cloud
    entry point refuses it (exit 2) BEFORE any client factory is resolved —
    one policy (infra.cli.refuse_keyfile_env) behind the one gate
    (loader.cli.require_confirm), matched by name shape so a new spelling is
    caught too."""
    import subprocess

    from serving import cli as scli
    from serving import spanner as sp

    def boom(*a: object, **k: object) -> object:
        raise AssertionError("a client factory / child resolved despite a keyfile env")

    monkeypatch.setattr(spanner, "default_clients", boom)
    monkeypatch.setattr(sp, "default_query_clients", boom)
    monkeypatch.setattr(sp, "default_spanner_clients", boom)
    monkeypatch.setattr(cli.bq, "default_clients", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setenv(var, "/tmp/key.json")
    gate = ("yes", "command line")
    proj = "ontime-rate-recovery"
    calls = {
        "bq-load": lambda: cli.bq_load("tiny", proj, *gate),
        "spanner-load": lambda: cli.spanner_load("tiny", proj, *gate),
        "dbt-build:bigquery": lambda: cli.dbt_build(
            "tiny", "bigquery", *gate, project=proj
        ),
        "test-int-bigquery": lambda: cli.int_bigquery("tiny", proj, *gate),
        "test-int-spanner": lambda: cli.int_spanner("tiny", proj, *gate),
        "writeback:spanner": lambda: scli.writeback("tiny", "spanner", proj, *gate),
    }
    with pytest.raises(SystemExit) as e:
        calls[entry]()
    assert e.value.code == 2
    assert f"refused — {var} in the environment" in capsys.readouterr().out
