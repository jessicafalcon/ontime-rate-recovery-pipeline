"""A golden: a built dbt table as canonical CSV, and the diff against a frozen
copy. Row content only — every golden sorts by its first two columns, the
declared key: attribution (prompt_id, user_id) — prompt_id is unique, user_id
names the tie-break; ontime_rate_daily (cohort_id, prompt_date) — unique
together. Never insertion order (Phase 3 invariant 5, Phase 4 invariant 5)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class Golden:
    """One frozen table: where it is built, which columns, how many leading
    columns identify a row in the diff, and its file under expected/."""

    relation: str
    columns: tuple[str, ...]
    key_width: int
    file: str


ATTRIBUTION = Golden(
    relation="main_attribution.attribution",
    columns=("prompt_id", "user_id", "cohort_id", "label"),
    key_width=1,
    file="expected/attribution.csv",
)
ONTIME_RATE_DAILY = Golden(
    relation="main_marts.ontime_rate_daily",
    columns=(
        "cohort_id",
        "prompt_date",
        "prompts_sent",
        "prompts_delivered",
        "on_time",
        "upload_fault",
        "timing_gap",
        "unattributed",
        "delivery_fault",
        "ontime_rate",
    ),
    key_width=2,
    file="expected/ontime_rate_daily.csv",
)
COLUMNS = ATTRIBUTION.columns  # the Phase 3 name, kept for its readers


def _cell(v: object) -> str:
    return "" if v is None else str(v)


def export_rows(db: Path, spec: Golden = ATTRIBUTION) -> list[tuple[str, ...]]:
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            f"select {', '.join(spec.columns)} from {spec.relation}"
        ).fetchall()
    finally:
        con.close()
    return sorted(
        (tuple(_cell(v) for v in r) for r in rows), key=lambda r: (r[0], r[1])
    )


def render(rows: list[tuple[str, ...]], spec: Golden = ATTRIBUTION) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(spec.columns)
    w.writerows(rows)
    return buf.getvalue()


def parse(text: str, spec: Golden = ATTRIBUTION) -> list[tuple[str, ...]]:
    reader = csv.reader(io.StringIO(text))
    header = tuple(next(reader))
    if header != spec.columns:
        raise ValueError(f"golden header {header} != {spec.columns}")
    return [tuple(r) for r in reader]


def diff_rows(
    built: list[tuple[str, ...]], frozen: list[tuple[str, ...]], key_width: int = 1
) -> list[str]:
    """One line per key whose row differs, is missing, or is extra."""
    have = {r[:key_width]: r for r in built}
    want = {r[:key_width]: r for r in frozen}
    out: list[str] = []
    for key in sorted(set(have) | set(want)):
        if have.get(key) != want.get(key):
            state = (
                "missing"
                if key not in have
                else "extra"
                if key not in want
                else "changed"
            )
            out.append(
                f"{'/'.join(key)}: {state} built={have.get(key)} frozen={want.get(key)}"
            )
    return out
