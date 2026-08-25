"""Raw landing: fixtures/<profile>/{raw,dims} → DuckDB schema `raw`.

Column types come from `loader/ddl.sql` (generated from the contract), never
from file inference: `event_properties` stays `json` so a JSON `null` value
survives, and an empty `valid_to` becomes SQL NULL (the open SCD2 row).
Idempotent: every load recreates both tables from the files."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

from generator import manifest  # hashes only; names no side-file

ROOT = Path(__file__).parent.parent
DDL = ROOT / "loader" / "ddl.sql"
DATA = ROOT / "data"
TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"  # the Amplitude export string


def db_path(profile: str) -> Path:
    return DATA / f"{profile}.duckdb"


def fixture_dir(profile: str) -> Path:
    """The committed fixture when one exists, else the generator's output."""
    for d in (ROOT / "fixtures" / profile, DATA / "out" / profile):
        if (d / "raw").is_dir():
            return d
    raise FileNotFoundError(f"no raw/ under fixtures/{profile} or data/out/{profile}")


STAGED_SUBTREES = ("raw", "dims")  # the only bytes the loader ever reads


def manifest_drift(fixture: Path) -> list[str]:
    """Staged files (raw/, dims/) differing from `<fixture>/MANIFEST.sha256`;
    [] when there is no manifest (an unfrozen profile) or they all match.
    Hashes the two subtrees only — nothing else under the fixture is read."""
    m = fixture / manifest.NAME
    if not m.is_file():
        return []
    have = {
        f"{sub}/{rel}": digest
        for sub in STAGED_SUBTREES
        if (fixture / sub).is_dir()
        for rel, digest in manifest.compute(fixture / sub).items()
    }
    want = {
        k: v
        for k, v in manifest.parse(m.read_text()).items()
        if k.split("/", 1)[0] in STAGED_SUBTREES
    }

    def state(k: str) -> str:
        if k not in have:
            return "missing"
        return "unexpected" if k not in want else "changed"

    return sorted(
        f"{k}: {state(k)}" for k in set(have) | set(want) if have.get(k) != want.get(k)
    )


def conflicting_duplicates(con: duckdb.DuckDBPyConnection) -> list[str]:
    """insert_ids with two rows tying on all three clocks but differing in
    event_properties — a data conflict, never a dedupe choice (invariant 1)."""
    rows = con.execute(
        "select insert_id from raw.events "
        "group by insert_id, client_event_time, server_received_time, "
        "server_upload_time "
        "having count(distinct cast(event_properties as varchar)) > 1 "
        "order by insert_id"
    ).fetchall()
    return [r[0] for r in rows]


def event_files(fixture: Path) -> list[Path]:
    """Every landing file, sorted by name (= by upload date); never `days`."""
    return sorted((fixture / "raw").glob("events_*.jsonl"))


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("set TimeZone = 'UTC'")  # never the host's zone
    return con


def create_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL.read_text())


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9 ()]*$")


def column_spec(con: duckdb.DuckDBPyConnection, table: str) -> str:
    """`{col: 'type', …}` for read_json/read_csv, from the DDL-created table.
    The spec is interpolated (DuckDB has no parameter for `columns=`), so every
    identifier and type is asserted quote-free first."""
    rows = con.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_schema = 'raw' and table_name = ? order by ordinal_position",
        [table],
    ).fetchall()
    for c, t in rows:
        if not (_IDENT.match(c) and _TYPE.match(t)):
            raise ValueError(f"refusing column spec {c!r}: {t!r}")
    return "{" + ", ".join(f"{c}: '{t}'" for c, t in rows) + "}"


def load_events(con: duckdb.DuckDBPyConnection, files: list[Path]) -> int:
    spec = column_spec(con, "events")
    for f in files:
        con.execute(
            "insert into raw.events select * from read_json(?, "
            f"format='newline_delimited', columns={spec}, "
            f"timestampformat='{TS_FORMAT}')",
            [str(f)],
        )
    return con.execute("select count(*) from raw.events").fetchone()[0]


def load_dims(con: duckdb.DuckDBPyConnection, csv: Path) -> int:
    spec = column_spec(con, "dim_user")
    con.execute(
        "insert into raw.dim_user select * from read_csv(?, header=true, "
        f"columns={spec}, nullstr='', timestampformat='{TS_FORMAT}')",
        [str(csv)],
    )
    return con.execute("select count(*) from raw.dim_user").fetchone()[0]


class ConflictingDuplicates(ValueError):
    pass


def load(profile: str, db: Path | None = None) -> tuple[int, int, int]:
    """(files, event rows, dim rows). Recreates `raw.*` from the fixture;
    raises ConflictingDuplicates (tables dropped again) on a payload conflict."""
    fixture = fixture_dir(profile)
    files = event_files(fixture)
    con = connect(db or db_path(profile))
    try:
        create_raw_tables(con)
        n_events = load_events(con, files)
        n_dims = load_dims(con, fixture / "dims" / "dim_user.csv")
        conflicts = conflicting_duplicates(con)
        if conflicts:
            con.execute("drop table raw.events")
            con.execute("drop table raw.dim_user")
            raise ConflictingDuplicates(", ".join(conflicts))
    finally:
        con.close()
    return len(files), n_events, n_dims
