"""The golden: the built attribution table as canonical CSV, and the diff
against a frozen copy. Row content only — sorted by prompt_id, never by
insertion order (invariant 5)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import duckdb

COLUMNS = ("prompt_id", "user_id", "cohort_id", "label")


def export_rows(db: Path) -> list[tuple[str, ...]]:
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            f"select {', '.join(COLUMNS)} from main_attribution.attribution"
        ).fetchall()
    finally:
        con.close()
    return sorted((tuple(str(v) for v in r) for r in rows), key=lambda r: (r[0], r[1]))


def render(rows: list[tuple[str, ...]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    w.writerows(rows)
    return buf.getvalue()


def parse(text: str) -> list[tuple[str, ...]]:
    reader = csv.reader(io.StringIO(text))
    header = tuple(next(reader))
    if header != COLUMNS:
        raise ValueError(f"golden header {header} != {COLUMNS}")
    return [tuple(r) for r in reader]


def diff_rows(built: list[tuple[str, ...]], frozen: list[tuple[str, ...]]) -> list[str]:
    """One line per prompt_id whose row differs, is missing, or is extra."""
    have = {r[0]: r for r in built}
    want = {r[0]: r for r in frozen}
    out: list[str] = []
    for pid in sorted(set(have) | set(want)):
        if have.get(pid) != want.get(pid):
            state = (
                "missing"
                if pid not in have
                else "extra"
                if pid not in want
                else "changed"
            )
            out.append(f"{pid}: {state} built={have.get(pid)} frozen={want.get(pid)}")
    return out
