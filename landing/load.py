"""Raw landing: fixtures/<profile>/{raw,dims} → DuckDB schema `raw`.

Column types come from `landing/ddl.sql` (generated from the contract), never
from file inference: `event_properties` stays `json` so a JSON `null` value
survives, and an empty `valid_to` becomes SQL NULL (the open SCD2 row).

Append-only (fix/append-landing): `raw.events` persists across loads; each load
overwrites the selected upload-date partitions (delete-then-insert per
`cast(server_upload_time as date)`) and never recreates the whole table, so
re-landing an already-landed date adds 0 net rows. THROUGH accumulates forward
within a warehouse (`drop-db` resets); a smaller THROUGH after a larger one does
not trim. `raw.dim_user` is the one seed and is a full replace each load."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import duckdb

from generator import manifest  # hashes only; names no side-file

ROOT = Path(__file__).parent.parent
DDL = ROOT / "landing" / "ddl.sql"
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


STAGED_SUBTREES = ("raw", "dims")  # the only bytes the landing ever reads


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


_UPLOAD_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _file_date(name: str) -> str:
    """The upload date in `events_YYYY-MM-DD[_HH].jsonl.gz` (the partition key):
    the first 10 chars of the stem. The hour, when present, is packaging only —
    every event in the file has `cast(server_upload_time as date)` == this date.
    Asserts the `YYYY-MM-DD` shape and refuses otherwise, so the value fed to a
    DuckDB partition delete or a `raw.events$YYYYMMDD` decorator is always the
    declared shape, never a raw slice — the guarantee the spec Threat model and
    `bq._partition_decorator` rely on (round: security note)."""
    stem = name[len("events_") : -len(".jsonl.gz")]
    date = stem[:10]
    if not _UPLOAD_DATE.match(date):
        raise ValueError(
            f"refusing raw file name (no YYYY-MM-DD upload date): {name!r}"
        )
    return date


def event_files(fixture: Path, through: str | None = None) -> list[Path]:
    """Every landing file, sorted by name (= by upload date then hour); never
    `days`. `through` keeps only files uploaded on or before that date — a
    landing is the raw-table state after loading a subset (Phase 7); None loads
    them all."""
    files = sorted((fixture / "raw").glob("events_*.jsonl.gz"))
    if through is None:
        return files
    return [f for f in files if _file_date(f.name) <= through]


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


def partition_overwrite_events(
    con: duckdb.DuckDBPyConnection, files: list[Path]
) -> int:
    """Append-only load: for each upload-date partition in the selection, delete
    that date's rows then insert the date's gzipped hourly files — so re-landing
    a date is idempotent (0 net rows) and an unselected date is untouched. The
    partition key is the file name's date (`_file_date`), which equals every
    row's `cast(server_upload_time as date)` by contract. Returns the table's
    total row count. Mirrors the `partition_overwrite` dbt strategy one layer up."""
    spec = column_spec(con, "events")
    by_date: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_date[_file_date(f.name)].append(f)
    for date in sorted(by_date):
        con.execute(
            "delete from raw.events "
            "where cast(server_upload_time as date) = cast(? as date)",
            [date],
        )
        for f in by_date[date]:
            con.execute(
                "insert into raw.events select * from read_json(?, "
                f"format='newline_delimited', columns={spec}, "
                f"timestampformat='{TS_FORMAT}', compression='gzip')",
                [str(f)],
            )
    return con.execute("select count(*) from raw.events").fetchone()[0]


def load_dims(con: duckdb.DuckDBPyConnection, csv: Path) -> int:
    """Full replace: the one dim seed has no upload-date partition."""
    spec = column_spec(con, "dim_user")
    con.execute("delete from raw.dim_user")
    con.execute(
        "insert into raw.dim_user select * from read_csv(?, header=true, "
        f"columns={spec}, nullstr='', timestampformat='{TS_FORMAT}')",
        [str(csv)],
    )
    return con.execute("select count(*) from raw.dim_user").fetchone()[0]


class ConflictingDuplicates(ValueError):
    pass


def load(
    profile: str, db: Path | None = None, through: str | None = None
) -> tuple[int, int, int]:
    """(files, event rows, dim rows). Append-only: keeps `raw.events` across
    loads and overwrites the selected upload-date partitions (the files uploaded
    on or before `through`, all of them when None — a landing is the raw-table
    state, Phase 7); `raw.dim_user` is a full replace. The whole load is one
    transaction: a payload conflict rolls it back (nothing committed) and raises
    ConflictingDuplicates, so a fresh warehouse is left empty and an existing one
    unchanged."""
    fixture = fixture_dir(profile)
    files = event_files(fixture, through)
    con = connect(db or db_path(profile))
    try:
        create_raw_tables(con)  # if not exists — the tables persist across loads
        con.execute("begin transaction")
        n_events = partition_overwrite_events(con, files)
        n_dims = load_dims(con, fixture / "dims" / "dim_user.csv")
        conflicts = conflicting_duplicates(con)
        if conflicts:
            con.execute("rollback")
            raise ConflictingDuplicates(", ".join(conflicts))
        con.execute("commit")
    finally:
        con.close()
    return len(files), n_events, n_dims
