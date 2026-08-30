"""The Spanner dims landing (Phase 10): dims/dim_user.csv → the Spanner
`dim_user` table — the production dims home BigQuery federates from (§2.3,
§3.3).

The `make load` contract, third engine: the SAME seed file the DuckDB and
BigQuery landings select, columns and types from the GENERATED contract
(`loader/bq_schema.json` — never inferred, never hand-listed), one idempotent
batch upsert keyed (user_id, valid_from) — re-landing the same seed rewrites
identical rows. One injectable client (loader/bq.py's pattern): the offline
suite injects a fake; google-cloud-spanner is never constructed there. Auth is
ADC, never a keyfile."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Protocol

from loader import bq
from loader import load as loader

TABLE = "dim_user"
# The ONE place the Spanner instance/database names live in Python
# (serving/spanner.py imports them); tests/test_infra.py pins both to the
# module's `name = "…"` so an infra rename cannot drift silently.
INSTANCE = "ontime"  # infra/modules/spanner/main.tf google_spanner_instance.this
DATABASE = "ontime"  # infra/modules/spanner/main.tf google_spanner_database.this


class DimClient(Protocol):
    """The ONE Spanner call the landing makes — a batch upsert; no read, no
    DDL (Terraform owns the schema), no delete."""

    def upsert(
        self, table: str, columns: tuple[str, ...], rows: list[tuple]
    ) -> None: ...


class GoogleDimClient:
    """google-cloud-spanner on ADC (the Phase 10 allowlist package);
    `disable_builtin_metrics=True` — no Cloud Monitoring exporter thread
    (serving/spanner.py::GoogleSpannerClient says why)."""

    def __init__(self, project: str) -> None:
        from google.cloud import spanner

        client = spanner.Client(project=project, disable_builtin_metrics=True)
        self._db = client.instance(INSTANCE).database(DATABASE)

    def upsert(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        with self._db.batch() as batch:
            batch.insert_or_update(table=table, columns=columns, values=rows)


DimClientFactory = Callable[[str], DimClient]


def default_clients() -> DimClientFactory:
    """Resolved at CALL time so the offline suite can replace it."""
    return GoogleDimClient


def dim_fields() -> list[dict[str, str]]:
    """The dim_user columns from the generated contract, in field order."""
    return json.loads(bq.SCHEMA_PATH.read_text())[TABLE]


def _cell(value: str, field: dict[str, str]) -> object:
    """One CSV cell to the contract's type. An empty NULLABLE cell is NULL (the
    open SCD2 row's valid_to); timestamps are UTC instants, stamped so."""
    if value == "" and field["mode"] == "NULLABLE":
        return None
    if field["type"] == "DATE":
        return date.fromisoformat(value)
    if field["type"] == "TIMESTAMP":
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    return value


def read_rows(profile: str) -> tuple[tuple[str, ...], list[tuple]]:
    """(columns, rows) from the profile's seed file, header checked against the
    generated contract — a drifted seed refuses, never lands sideways."""
    fields = dim_fields()
    names = tuple(f["name"] for f in fields)
    path = loader.fixture_dir(profile) / "dims" / "dim_user.csv"
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        header = tuple(next(reader))
        if header != names:
            raise ValueError(f"dim_user.csv header {header} != contract {names}")
        rows = [
            tuple(_cell(v, f) for v, f in zip(r, fields, strict=True)) for r in reader
        ]
    return names, rows


def load_dims(
    profile: str, project: str, clients: DimClientFactory | None = None
) -> int:
    """Rows landed. One batch insert_or_update of the whole seed — idempotent
    (same key (user_id, valid_from), same values → same table state)."""
    names, rows = read_rows(profile)
    c = (clients or default_clients())(project)
    c.upsert(TABLE, names, rows)
    return len(rows)
