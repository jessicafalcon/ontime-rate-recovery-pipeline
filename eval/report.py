"""`make report`: the overall on-time rate over the ontime_rate_daily mart —
sum(on_time) / sum(prompts_delivered), the §4.6 denominator — read off the
built table, never recomputed from events (the mart is the definition)."""

from __future__ import annotations

from pathlib import Path

import duckdb

RELATION = "main_marts.ontime_rate_daily"


def overall_rate(db: Path) -> float:
    con = duckdb.connect(str(db))
    try:
        on_time, delivered = con.execute(
            f"select sum(on_time), sum(prompts_delivered) from {RELATION}"
        ).fetchone()
    finally:
        con.close()
    return float(on_time) / float(delivered)
