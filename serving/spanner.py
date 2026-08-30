"""The Spanner write-back (Phase 10): TARGET=spanner reads the served pair off
BigQuery (`<project>.ontime`) and writes the Spanner `send_schedule` — the §2.9
serving store the DuckDB table stood in for.

The guard is the SAME Python (`should_replace` / `version_key` / `winners_of`);
only the reader and the applier dispatch — one TARGET knob, two named
configurations (spec reconciliation item 1), never a read×write matrix. Every
cloud call goes through an injectable factory (loader/bq.py's pattern): the
offline suite injects fakes and the google clients are never constructed there.
Auth is ADC (the impersonated SA), never a keyfile. `written_at =
computed_as_of` — data-derived, no clock, so two runs over the same scores
write 0 and the row hash never moves (§4 invariant 5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from serving import writeback as wb

MODELS_DATASET = "ontime"  # infra/variables.tf models_dataset default (9a's pin)
INSTANCE = "ontime"  # infra/modules/spanner/main.tf
DATABASE = "ontime"
TABLE = "send_schedule"
# The nine §2.9 columns, in order — serving/ddl.sql and the module's Spanner DDL
# carry the same list (pinned by tests/test_dbt_sources.py).
COLUMNS = (
    "user_id",
    "cohort_id",
    "send_hour_local",
    "send_minute_local",
    "tz",
    "confidence",
    "model_version",
    "computed_as_of",
    "written_at",
)
EXISTING_SQL = f"select user_id, model_version, computed_as_of from {TABLE}"


class QueryClient(Protocol):
    """The ONE BigQuery call the cloud read makes — a select; no DDL, no load."""

    def query(self, sql: str) -> list[tuple]: ...


class SpannerClient(Protocol):
    """The two Spanner calls the write path makes — a snapshot read and a batch
    upsert; nothing else (no DDL — Terraform owns the schema; no delete)."""

    def read(self, sql: str) -> list[tuple]: ...

    def upsert(
        self, table: str, columns: tuple[str, ...], rows: list[tuple]
    ) -> None: ...


class GoogleQueryClient:
    """google-cloud-bigquery on ADC (dbt-bigquery's transitive client)."""

    def __init__(self, project: str) -> None:
        from google.cloud import bigquery

        self._client = bigquery.Client(project=project)

    def query(self, sql: str) -> list[tuple]:
        return [tuple(r) for r in self._client.query(sql).result()]


class GoogleSpannerClient:
    """google-cloud-spanner on ADC (the Phase 10 allowlist package)."""

    def __init__(self, project: str) -> None:
        from google.cloud import spanner

        self._db = spanner.Client(project=project).instance(INSTANCE).database(DATABASE)

    def read(self, sql: str) -> list[tuple]:
        with self._db.snapshot() as snap:
            return [tuple(r) for r in snap.execute_sql(sql)]

    def upsert(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        with self._db.batch() as batch:
            batch.insert_or_update(table=table, columns=columns, values=rows)


QueryFactory = Callable[[str], QueryClient]
SpannerFactory = Callable[[str], SpannerClient]


def default_query_clients() -> QueryFactory:
    """Resolved at CALL time so the offline suite can replace it (bq.py's
    round-1 lesson: a default bound at import could not be)."""
    return GoogleQueryClient


def default_spanner_clients() -> SpannerFactory:
    return GoogleSpannerClient


def relations(project: str) -> tuple[str, str]:
    """(scores, dims) on the bigquery target — every model lands in `ontime`
    (9b's generate_schema_name; two datasets stays 9a's pin)."""
    return (
        f"`{project}.{MODELS_DATASET}.scores_send_time`",
        f"`{project}.{MODELS_DATASET}.dim_user_current`",
    )


def _utc(ts: datetime) -> datetime:
    """Spanner TIMESTAMP is a UTC instant; a naive value is stamped UTC (the
    warehouse timestamps ARE UTC wall times) so the client never guesses."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def row_of(w: wb.Candidate) -> tuple:
    """The nine column values, `written_at = computed_as_of` (data-derived)."""
    as_of = _utc(w.computed_as_of)
    return (
        w.user_id,
        w.cohort_id,
        w.send_hour_local,
        w.send_minute_local,
        w.tz,
        w.confidence,
        w.model_version,
        as_of,
        as_of,  # written_at = computed_as_of (data-derived)
    )


def apply_writeback(client: SpannerClient, winners: list[wb.Candidate]) -> None:
    """Batch insert_or_update of the winners only — the delete half of the
    DuckDB idiom is Spanner's upsert semantics; a loser is never touched."""
    if not winners:
        return
    client.upsert(TABLE, COLUMNS, [row_of(w) for w in winners])


def write_back(
    project: str,
    bq_clients: QueryFactory | None = None,
    spanner_clients: SpannerFactory | None = None,
) -> tuple[int, int]:
    """(candidates, written). The same shape as the DuckDB write_back: read the
    served pair + open tz (BigQuery), read the stored pairs (Spanner), replace
    only on strictly greater (model_version, computed_as_of). Idempotent."""
    q = (bq_clients or default_query_clients())(project)
    s = (spanner_clients or default_spanner_clients())(project)
    scores, dims = relations(project)
    candidates = [
        replace(c, computed_as_of=_utc(c.computed_as_of))
        for c in (wb.Candidate(*r) for r in q.query(wb.candidates_sql(scores, dims)))
    ]
    existing = {r[0]: (r[1], _utc(r[2])) for r in s.read(EXISTING_SQL)}
    winners = wb.winners_of(candidates, existing)
    apply_writeback(s, winners)
    return len(candidates), len(winners)
