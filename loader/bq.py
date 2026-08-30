"""The BigQuery landing (Phase 9b): fixtures/<profile>/{raw,dims} → the GCS
staging bucket → the `raw` dataset, as `bq load` would do it.

The `make load` contract, second dialect: the SAME files the DuckDB loader
selects (`load.event_files`, THROUGH-filtered by name), an EXPLICIT schema
generated from the contract (`loader/bq_schema.json`, never inferred), and a
recreate (`WRITE_TRUNCATE`) so a second landing is byte-identical, never
appended — and that load job is the ONLY landing mechanism (Amendment X: an
empty selection lands a zero-byte object through it). Every cloud call goes
through a `Clients` object built by an
injectable factory: the offline suite injects a fake and the default factory
(the google clients dbt-bigquery brings) is never constructed there. Auth is
ADC — the operator's impersonated SA credential — never a keyfile."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from loader import load as loader

SCHEMA_PATH = loader.ROOT / "loader" / "bq_schema.json"
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
    """Per table, the files to land: the THROUGH-filtered `events_*.jsonl` (the
    DuckDB loader's own predicate) and the one dim seed."""
    return {
        "events": loader.event_files(fixture, through),
        "dim_user": [fixture / "dims" / "dim_user.csv"],
    }


def schema_fields(table: str) -> list[dict[str, str]]:
    return json.loads(SCHEMA_PATH.read_text())[table]


def object_name(profile: str, table: str, path: Path) -> str:
    return f"{LANDING_PREFIX}/{profile}/{FILES_PER_TABLE[table]}/{path.name}"


def load_job_config(table: str) -> dict[str, Any]:
    """The explicit load: contract schema, recreate. Events are newline JSON;
    the dim seed is a headed CSV whose empty `valid_to` is the open row."""
    cfg: dict[str, Any] = {
        "schema": schema_fields(table),
        "write_disposition": "WRITE_TRUNCATE",
    }
    if table == "events":
        cfg["source_format"] = "NEWLINE_DELIMITED_JSON"
    else:
        cfg["source_format"] = "CSV"
        cfg["skip_leading_rows"] = 1
        cfg["null_marker"] = ""
    return cfg


def bq_load(
    profile: str,
    project: str,
    through: str | None = None,
    clients: ClientFactory | None = None,
) -> tuple[int, int, int]:
    """(event files, event rows, dim rows). Uploads the selected files under
    `landing/<profile>/`, then ONE load job per table into `<project>.raw` —
    the only landing mechanism (Amendment X): a table with no selected file
    lands a zero-byte object through the same job, so `WRITE_TRUNCATE` + the
    contract schema re-create it empty."""
    fixture = loader.fixture_dir(profile)
    files = selected_files(fixture, through)
    c = (clients or default_clients())(project)
    bucket = bucket_name(project)
    rows: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / EMPTY_OBJECT
        empty.write_bytes(b"")
        for table, paths in files.items():
            uris = []
            for path in paths or [empty]:
                name = object_name(profile, table, path)
                c.upload(bucket, name, path)
                uris.append(f"gs://{bucket}/{name}")
            rows[table] = c.load(
                f"{project}.{RAW_DATASET}.{table}", uris, load_job_config(table)
            )
    return len(files["events"]), rows["events"], rows["dim_user"]
