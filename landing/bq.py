"""The BigQuery landing (Phase 9b): fixtures/<profile>/{raw,dims} → the GCS
staging bucket → the `raw` dataset, as `bq load` would do it.

The `make load` contract, second dialect: the SAME files the DuckDB landing
selects (`load.event_files`, THROUGH-filtered by name), an EXPLICIT schema
generated from the contract (`landing/bq_schema.json`, never inferred), and
`WRITE_TRUNCATE` load jobs — append-only (fix/append-landing): one per
upload-date partition into the DAY-partitioned `raw.events$YYYYMMDD` (re-landing
a date replaces just that partition, 0 net rows) plus one for the `raw.dim_user`
seed (full replace). Load jobs only — no read, query or DDL — so the landing
never depends on prior table state (Amendment X: an empty events selection lands
a zero-byte object into the base table through the same job — BigQuery rejects a
job over zero URIs but loads a zero-byte object as 0 rows). Every cloud call goes
through a `Clients` object built by an injectable
factory: the offline suite injects a fake and the default factory (the google
clients dbt-bigquery brings) is never constructed there. Auth is ADC — the
operator's impersonated SA credential — never a keyfile."""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from landing import load as landing

PARTITION_FIELD = "server_upload_time"  # raw.events DAY partition (append-landing)

SCHEMA_PATH = landing.ROOT / "landing" / "bq_schema.json"
RAW_DATASET = "raw"  # infra/variables.tf raw_dataset default
LANDING_PREFIX = "landing"  # objects: landing/<profile>/<raw|dims>/<file>
FILES_PER_TABLE = {"events": "raw", "dim_user": "dims"}  # table → fixture subtree
EMPTY_OBJECT = "_empty.jsonl"  # the zero-byte landing of an empty selection (X)


class Clients(Protocol):
    """The two cloud calls the landing makes — and only these: no read, no
    query, no DDL (Amendment X) — so a fake implements the same two."""

    def upload(self, bucket: str, name: str, path: Path) -> None: ...

    def load(self, table_id: str, uris: list[str], config: dict[str, Any]) -> int:
        """Load `uris` into `table_id` under `config`; returns rows loaded."""
        ...


class GoogleClients:
    """The default: google-cloud-storage + google-cloud-bigquery on ADC."""

    def __init__(self, project: str) -> None:
        from google.cloud import bigquery, storage

        self._bq = bigquery
        self._storage = storage.Client(project=project)
        self._client = bigquery.Client(project=project)

    def upload(self, bucket: str, name: str, path: Path) -> None:
        self._storage.bucket(bucket).blob(name).upload_from_filename(str(path))

    def load(self, table_id: str, uris: list[str], config: dict[str, Any]) -> int:
        cfg = dict(config)
        cfg["schema"] = [
            self._bq.SchemaField(f["name"], f["type"], mode=f["mode"])
            for f in cfg["schema"]
        ]
        if "time_partitioning" in cfg:  # DAY partition on raw.events (append-landing)
            tp = cfg["time_partitioning"]
            cfg["time_partitioning"] = self._bq.TimePartitioning(
                type_=tp["type"], field=tp["field"]
            )
        job = self._client.load_table_from_uri(
            uris, table_id, job_config=self._bq.LoadJobConfig(**cfg)
        )
        job.result()
        return int(job.output_rows or 0)


ClientFactory = Callable[[str], Clients]


def default_clients() -> ClientFactory:
    """The factory used when a caller passes none — resolved at CALL time, so
    the offline suite can replace it with a sentinel (review round 1 #1: a
    default bound at import could not be)."""
    return GoogleClients


def bucket_name(project: str) -> str:
    """9a's managed staging bucket, derived `${project_id}-ontime` (never a var)."""
    return f"{project}-ontime"


def selected_files(fixture: Path, through: str | None = None) -> dict[str, list[Path]]:
    """Per table, the files to land: the THROUGH-filtered `events_*.jsonl.gz` (the
    DuckDB landing's own predicate) and the one dim seed."""
    return {
        "events": landing.event_files(fixture, through),
        "dim_user": [fixture / "dims" / "dim_user.csv"],
    }


def schema_fields(table: str) -> list[dict[str, str]]:
    return json.loads(SCHEMA_PATH.read_text())[table]


def object_name(profile: str, table: str, path: Path) -> str:
    return f"{LANDING_PREFIX}/{profile}/{FILES_PER_TABLE[table]}/{path.name}"


def load_job_config(table: str) -> dict[str, Any]:
    """The explicit load: contract schema, WRITE_TRUNCATE. Events are newline
    JSON (gzip is auto-detected by BigQuery) on a DAY-partitioned table
    (`server_upload_time`), so a per-partition decorator load replaces just that
    one partition (append-only); the dim seed is a headed CSV whose empty
    `valid_to` is the open row."""
    cfg: dict[str, Any] = {
        "schema": schema_fields(table),
        "write_disposition": "WRITE_TRUNCATE",
    }
    if table == "events":
        cfg["source_format"] = "NEWLINE_DELIMITED_JSON"
        cfg["time_partitioning"] = {"type": "DAY", "field": PARTITION_FIELD}
    else:
        cfg["source_format"] = "CSV"
        cfg["skip_leading_rows"] = 1
        cfg["null_marker"] = ""
    return cfg


def _partition_decorator(upload_date: str) -> str:
    """`raw.events$YYYYMMDD` from a validated upload date (`_file_date`, the
    `\\d{4}-\\d{2}-\\d{2}` shape) — never a user string (spec Threat model)."""
    return upload_date.replace("-", "")


def bq_load(
    profile: str,
    project: str,
    through: str | None = None,
    clients: ClientFactory | None = None,
) -> tuple[int, int, int]:
    """(event files, event rows, dim rows). Append-only (fix/append-landing):
    uploads the selected files under `landing/<profile>/`, then a WRITE_TRUNCATE
    load per upload-date partition into `<project>.raw.events$YYYYMMDD` (the
    DAY-partitioned table — re-landing a date replaces just that partition, 0
    net rows) and one WRITE_TRUNCATE load into `<project>.raw.dim_user` (the one
    seed, full replace). An empty events selection lands a zero-byte object into
    the base table so `WRITE_TRUNCATE` + the contract schema create it empty
    (Amendment X: BigQuery rejects a job over zero URIs). Load jobs only — no
    read, query or DDL — so the landing never depends on prior table state."""
    fixture = landing.fixture_dir(profile)
    files = selected_files(fixture, through)
    c = (clients or default_clients())(project)
    bucket = bucket_name(project)
    events_cfg = load_job_config("events")

    def upload_uri(table: str, path: Path) -> str:
        name = object_name(profile, table, path)
        c.upload(bucket, name, path)
        return f"gs://{bucket}/{name}"

    with tempfile.TemporaryDirectory() as tmp:
        by_date: dict[str, list[Path]] = defaultdict(list)
        for path in files["events"]:
            by_date[landing._file_date(path.name)].append(path)
        event_rows = 0
        if by_date:  # one load per upload-date partition (append-only)
            for date in sorted(by_date):
                uris = [upload_uri("events", p) for p in by_date[date]]
                table_id = (
                    f"{project}.{RAW_DATASET}.events${_partition_decorator(date)}"
                )
                event_rows += c.load(table_id, uris, events_cfg)
        else:  # empty selection: create/empty the base partitioned table (X)
            empty = Path(tmp) / EMPTY_OBJECT
            empty.write_bytes(b"")
            uri = upload_uri("events", empty)
            event_rows = c.load(f"{project}.{RAW_DATASET}.events", [uri], events_cfg)
        dim_path = files["dim_user"][0]
        dim_rows = c.load(
            f"{project}.{RAW_DATASET}.dim_user",
            [upload_uri("dim_user", dim_path)],
            load_job_config("dim_user"),
        )
    return len(files["events"]), event_rows, dim_rows
