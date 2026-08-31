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
write 0 and the row hash never moves (§4 invariant 5).

Serialization (spec Amendment A): the stored-pair read and the winners' upsert
run inside ONE Spanner read-write transaction (`run_in_transaction`), so
replace-iff-greater holds across concurrent write-backs, not just within a
run — a transaction that read a pair another commit moved is aborted and
retried on the fresh read. The function the transaction runs is re-runnable
by construction: it recomputes the winners from what it reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from loader.spanner import DATABASE, INSTANCE
from serving import writeback as wb

MODELS_DATASET = "ontime"  # infra/variables.tf models_dataset default (9a's pin)
TABLE = "send_schedule"
# The nine §2.9 columns, in order — ONE tuple for every writer
# (serving/writeback.py::COLUMNS); serving/ddl.sql and the module's Spanner DDL
# carry the same list (pinned by tests/test_writeback.py, tests/test_dbt_sources.py).
COLUMNS = wb.COLUMNS
EXISTING_SQL = wb.existing_sql(TABLE)

T = TypeVar("T")


class QueryClient(Protocol):
    """The ONE BigQuery call the cloud read makes — a select; no DDL, no load.
    Rows come back keyed by column NAME (the write path maps by name too —
    round 2 #9)."""

    def query(self, sql: str) -> list[dict[str, object]]: ...


class Txn(Protocol):
    """What the write path may do INSIDE its one read-write transaction: read
    the stored pairs, upsert the winners. No DDL (Terraform owns the schema),
    no delete. Rows come back keyed by column NAME (round 3 #3) — the
    library's own `StreamedResultSet.to_dict_list()`, not a mapping of ours
    (Amendment N3)."""

    def read(self, sql: str) -> list[dict[str, object]]: ...

    def upsert(
        self, table: str, columns: tuple[str, ...], rows: list[tuple]
    ) -> None: ...


class SpannerClient(Protocol):
    """`transact` runs `fn` in one read-write transaction (the write path);
    `read` is a snapshot read for readers (the integration read-back)."""

    def transact(self, fn: Callable[[Txn], T]) -> T: ...

    def read(self, sql: str) -> list[dict[str, object]]: ...


class GoogleQueryClient:
    """google-cloud-bigquery on ADC (dbt-bigquery's transitive client)."""

    def __init__(self, project: str) -> None:
        from google.cloud import bigquery

        self._client = bigquery.Client(project=project)

    def query(self, sql: str) -> list[dict[str, object]]:
        return [dict(r.items()) for r in self._client.query(sql).result()]


class _GoogleTxn:
    """The Txn protocol over google-cloud-spanner's Transaction."""

    def __init__(self, txn: object) -> None:
        self._txn = txn

    def read(self, sql: str) -> list[dict[str, object]]:
        return self._txn.execute_sql(sql).to_dict_list()  # type: ignore[attr-defined]

    def upsert(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        self._txn.insert_or_update(table=table, columns=columns, values=rows)  # type: ignore[attr-defined]


class GoogleSpannerClient:
    """google-cloud-spanner on ADC (the Phase 10 allowlist package).
    `disable_builtin_metrics=True`: the client's default Cloud Monitoring
    exporter is an egress thread the pipeline never asked for (and the SA has
    no monitoring grant, so it could only fail loudly) — off, like dbt's
    telemetry (review round 1, finding 8)."""

    def __init__(self, project: str) -> None:
        from google.cloud import spanner

        client = spanner.Client(project=project, disable_builtin_metrics=True)
        self._db = client.instance(INSTANCE).database(DATABASE)

    def transact(self, fn: Callable[[Txn], T]) -> T:
        return self._db.run_in_transaction(lambda txn: fn(_GoogleTxn(txn)))

    def read(self, sql: str) -> list[dict[str, object]]:
        with self._db.snapshot() as snap:
            return snap.execute_sql(sql).to_dict_list()


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
    warehouse timestamps ARE UTC wall times) so the client never guesses — and
    so a naive candidate never meets an aware stored value in the guard's
    comparison (a TypeError, not a wrong answer, but a wedged run)."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def row_of(w: wb.Candidate) -> tuple:
    """The nine column values by NAME (writeback.row_of), `computed_as_of` and
    `written_at` as UTC instants."""
    return wb.row_of(replace(w, computed_as_of=_utc(w.computed_as_of)))


def apply_writeback(txn: Txn, winners: list[wb.Candidate]) -> None:
    """Batch insert_or_update of the winners only — the delete half of the
    DuckDB idiom is Spanner's upsert semantics; a loser is never touched."""
    if not winners:
        return
    txn.upsert(TABLE, COLUMNS, [row_of(w) for w in winners])


def write_back(
    project: str,
    bq_clients: QueryFactory | None = None,
    spanner_clients: SpannerFactory | None = None,
) -> tuple[int, int]:
    """(candidates, written). The same shape as the DuckDB write_back: read the
    served pair + open tz (BigQuery), then in ONE Spanner read-write
    transaction read the stored pairs and upsert the winners — replace only on
    strictly greater (model_version, computed_as_of). Idempotent."""
    q = (bq_clients or default_query_clients())(project)
    s = (spanner_clients or default_spanner_clients())(project)
    scores, dims = relations(project)
    candidates = [
        replace(c, computed_as_of=_utc(c.computed_as_of))
        for c in map(wb.candidate_of, q.query(wb.candidates_sql(scores, dims)))
    ]

    def guard_and_write(txn: Txn) -> int:
        existing: dict[str, tuple[str, datetime]] = {}
        for row in txn.read(EXISTING_SQL):
            user_id, (version, as_of) = wb.existing_of(row)
            existing[user_id] = (version, _utc(as_of))
        winners = wb.winners_of(candidates, existing)
        apply_writeback(txn, winners)
        return len(winners)

    written = s.transact(guard_and_write)
    return len(candidates), written
