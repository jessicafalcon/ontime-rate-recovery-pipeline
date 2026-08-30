"""The write-back: scores_send_time + the open dim_user tz → serving.send_schedule.

The serving table is the DuckDB stand-in for Spanner (§2.9, §3.3). A batch upsert
keyed `user_id` replaces a user's row ONLY when the incoming
`(model_version, computed_as_of)` is strictly greater than the stored pair — on
the row's own data-derived columns, never a caller-supplied marker. So a re-run
over the same scores is a no-op (§4 invariant 5): every candidate ties its
existing row, is not strictly greater, and nothing is written.

`written_at = computed_as_of` (data-derived, never a clock): a per-row function of
the winning score row is what keeps send_schedule byte-identical on a re-run and
under a backfill (Phase 8b). The write-back re-derives no score — it consumes the
served pair verbatim — and reads neither the generator side-file nor raw (§3.1):
tz comes from the `dim_user_current` dbt model (the open row), not `raw.dim_user`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from loader import load as loader

DDL = loader.ROOT / "serving" / "ddl.sql"
SCORES = "main_scores.scores_send_time"
DIM_CURRENT = "main_marts.dim_user_current"
SEND_SCHEDULE = "serving.send_schedule"

VERSION_RE = re.compile(r"^v(\d+)$")


@dataclass(frozen=True)
class Candidate:
    """One served schedule row, tz joined from the open dim_user row. The nine
    send_schedule columns are these eight plus `written_at = computed_as_of`."""

    user_id: str
    cohort_id: str
    send_hour_local: int
    send_minute_local: int
    tz: str
    confidence: float
    model_version: str
    computed_as_of: datetime


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create serving.send_schedule if absent (never `create or replace` — the
    table persists across runs so a re-run can be a no-op)."""
    con.execute(DDL.read_text())


def candidates_sql(scores: str = SCORES, dims: str = DIM_CURRENT) -> str:
    """The ONE read the write-back makes on any target: the served pair joined
    to the open dim_user tz. `scores`/`dims` override the DuckDB relation names
    (TARGET=spanner reads `<project>.ontime.…` off BigQuery — Phase 10, the
    Golden-style relation seam)."""
    return (
        "select s.user_id, s.cohort_id, s.send_hour_local, s.send_minute_local, "
        "d.tz, s.confidence, s.model_version, s.computed_as_of "
        f"from {scores} as s "
        f"join {dims} as d using (user_id) "
        "order by s.user_id"
    )


def read_candidates(con: duckdb.DuckDBPyConnection) -> list[Candidate]:
    """The served pair from scores_send_time, tz from the open dim_user row
    (dim_user_current) — never the source events or the unclamped centre."""
    rows = con.execute(candidates_sql()).fetchall()
    return [Candidate(*r) for r in rows]


def read_existing(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[str, datetime]]:
    """user_id → the stored (model_version, computed_as_of) the guard compares."""
    rows = con.execute(
        f"select user_id, model_version, computed_as_of from {SEND_SCHEDULE}"
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def version_key(model_version: str) -> tuple[int]:
    """The order `model_version` compares under: `v<int>` parsed numerically, so
    `v10 > v2` (Phase 10; lexical tuple comparison had `'v10' < 'v2'`). Any
    other shape is a loud refusal — never a lexical fallback that would
    silently re-introduce the bug."""
    m = VERSION_RE.match(model_version)
    if not m:
        raise ValueError(f"model_version must be v<int>, got {model_version!r}")
    return (int(m.group(1)),)


def should_replace(candidate: Candidate, existing: tuple[str, datetime] | None) -> bool:
    """Replace iff the candidate's `(model_version, computed_as_of)` is strictly
    greater than the stored pair — model_version under `version_key`'s numeric
    order. An absent user inserts; a tie or a lesser pair leaves the row
    untouched (§4 invariant 5). Data-derived, no caller marker."""
    if existing is None:
        return True
    return (version_key(candidate.model_version), candidate.computed_as_of) > (
        version_key(existing[0]),
        existing[1],
    )


def winners_of(
    candidates: list[Candidate], existing: dict[str, tuple[str, datetime]]
) -> list[Candidate]:
    """The rows to write — shared by every target (versions parsed up front, so
    a malformed one refuses before anything is written)."""
    return [c for c in candidates if should_replace(c, existing.get(c.user_id))]


def apply_writeback(con: duckdb.DuckDBPyConnection, winners: list[Candidate]) -> None:
    """Delete the winners' old rows and insert the new ones — the repo's
    DuckDB idiom (there is no MERGE/ON CONFLICT macro; the write-back is serving
    Python, not a dbt model). `written_at = computed_as_of`."""
    if not winners:
        return
    ids = [w.user_id for w in winners]
    marks = ", ".join(["?"] * len(ids))
    con.execute(f"delete from {SEND_SCHEDULE} where user_id in ({marks})", ids)
    con.executemany(
        f"insert into {SEND_SCHEDULE} "
        "(user_id, cohort_id, send_hour_local, send_minute_local, tz, "
        "confidence, model_version, computed_as_of, written_at) "
        "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                w.user_id,
                w.cohort_id,
                w.send_hour_local,
                w.send_minute_local,
                w.tz,
                w.confidence,
                w.model_version,
                w.computed_as_of,
                w.computed_as_of,  # written_at = computed_as_of (data-derived)
            )
            for w in winners
        ],
    )


def write_back(profile: str, db: Path | None = None) -> tuple[int, int]:
    """(candidates, written). Upsert the served schedule into send_schedule,
    replacing a user's row only on a strictly greater version/as-of pair.
    Idempotent: a second run over the same scores writes zero."""
    con = loader.connect(db or loader.db_path(profile))
    try:
        ensure_table(con)
        candidates = read_candidates(con)
        existing = read_existing(con)
        winners = winners_of(candidates, existing)
        apply_writeback(con, winners)
    finally:
        con.close()
    return len(candidates), len(winners)
