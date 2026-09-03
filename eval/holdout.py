"""Temporal holdout (fix/holdout-eval, ARCHITECTURE §7 report (d)): the served
schedule, trained on data landed with an upload-date cut, scored against the RAW
organic `app_opened` opens uploaded AFTER the cut — the reachability signals the
model did not see at serving time.

The non-circular counterpart to the simulation. The simulation re-draws every
outcome from the same latent that generated the data (it reads `truth/`); this
reads only observed behaviour off the warehouse — never `truth/`, never a
reachable-window or centre quantity (those are truth concepts), no clock. Two
DuckDB builds: a `through=cut` build gives the served schedule (trained on the
files uploaded ≤ cut); a full build gives the held-out opens (uploaded > cut).
Both measures are circular and read off the model's own served columns."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from eval.score import circular_abs_diff_hours
from landing import load as landing

ARMS = ("recommended", "cohort")
BEGIN = "<!-- holdout:begin {profile} -->"
END = "<!-- holdout:end {profile} -->"
DBT = landing.ROOT / "dbt"


# ------------------------------------------------------------------ the builds


def _build_in_process(profile: str, db: Path, through: str | None) -> bool:
    """Land `profile` into `db` (files uploaded ≤ `through`, or all when None)
    and run one `dbt build` against it in THIS process. Runs ONLY inside the
    isolated subprocess `build` spawns — it lands raw + dims, never truth."""
    os.environ.setdefault("DO_NOT_TRACK", "1")
    from dbt.cli.main import dbtRunner

    landing.load(profile, db, through)
    os.environ["OTR_DUCKDB_PATH"] = str(db)
    args = [
        "build",
        "--project-dir",
        str(DBT),
        "--profiles-dir",
        str(DBT),
        "--target",
        "duckdb",
        "--quiet",
        "--target-path",
        str(db.parent / f"t_{db.stem}"),
    ]
    return bool(dbtRunner().invoke(args).success)


def build(profile: str, db: Path, through: str | None) -> bool:
    """Land + build `profile` into `db` in an ISOLATED SUBPROCESS. The holdout
    builds two different DuckDB paths back-to-back (served ≤ cut, full); dbt's
    in-process adapter can hold its connection to the FIRST path, so two builds
    swapping `OTR_DUCKDB_PATH` in one process are nondeterministic (a rare
    served/full cross-resolve scored 0 users — fix/holdout-eval review). A fresh
    process per build resolves its own `OTR_DUCKDB_PATH` cleanly — exactly how
    the real `make dbt-build` runs one build per process. Returns True on
    success; `through` is passed as the empty string for None."""
    proc = subprocess.run(
        [sys.executable, "-m", "eval.holdout", profile, str(db), through or ""],
        cwd=str(landing.ROOT),
        env={**os.environ, "PYTHONPATH": str(landing.ROOT), "DO_NOT_TRACK": "1"},
    )
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    """`python -m eval.holdout <profile> <db> [<through>]` — one isolated build,
    the entry point `build` spawns (never used directly)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not 2 <= len(args) <= 3:
        print("usage: python -m eval.holdout <profile> <db> [<through>]")
        return 2
    profile, db = args[0], Path(args[1])
    through = args[2] if len(args) == 3 and args[2] else None
    return 0 if _build_in_process(profile, db, through) else 1


# ---------------------------------------------------------- reads off warehouse


def served_schedule(db: Path) -> dict[str, tuple[float, float]]:
    """user_id → (recommended served hour as a fraction, cohort_hour_local), off
    scores_send_time of the CUT build — the schedule trained on data ≤ cut. The
    served columns only, never the unclamped centre the model shrinks from."""
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            "select user_id, send_hour_local + send_minute_local / 60.0, "
            "cohort_hour_local from main_scores.scores_send_time"
        ).fetchall()
    finally:
        con.close()
    return {uid: (float(rec), float(coh)) for uid, rec, coh in rows}


def heldout_opens(db: Path, cut: str) -> dict[str, list[float]]:
    """user_id → local hours (fraction) of organic app_opened uploaded AFTER the
    cut, off stg_events of the FULL build. The cut is an upload date; an event's
    upload date is `cast(server_upload_time as date)` (the file it landed in).
    Ordered by the unique insert_id so the per-user lists are deterministic."""
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            "select user_id, extract(hour from client_event_time_local) "
            "+ extract(minute from client_event_time_local) / 60.0 "
            "from main_staging.stg_events "
            "where event_type = 'app_opened' "
            "and cast(server_upload_time as date) > cast(? as date) "
            "order by user_id, insert_id",
            [cut],
        ).fetchall()
    finally:
        con.close()
    out: dict[str, list[float]] = {}
    for uid, hour in rows:
        out.setdefault(uid, []).append(float(hour))
    return out


# ------------------------------------------------------------------ the measure


@dataclass(frozen=True)
class ArmResult:
    arm: str
    n_users: int
    n_opens: int
    in_window_share: float
    mean_nearest_hours: float


def _served_hour(served: tuple[float, float], arm: str) -> float:
    """recommended = the served per-user hour; cohort = the band anchor."""
    return served[0] if arm == "recommended" else served[1]


def evaluate(
    served: dict[str, tuple[float, float]],
    opens: dict[str, list[float]],
    window: float,
) -> list[ArmResult]:
    """One ArmResult per arm over the users with a served row AND ≥1 held-out
    open (an unserved user cannot be scored; a user with no held-out open cannot
    score one). `in_window_share` is over opens (a per-open hit inside ±window of
    the served hour); `mean_nearest_hours` is the mean over users of the circular
    distance from the served hour to that user's nearest held-out open."""
    users = sorted(u for u in opens if u in served and opens[u])
    results: list[ArmResult] = []
    for arm in ARMS:
        in_window = 0
        total = 0
        nearest_sum = 0.0
        for u in users:
            served_hour = _served_hour(served[u], arm)
            dists = [circular_abs_diff_hours(served_hour, h) for h in opens[u]]
            in_window += sum(1 for d in dists if d <= window)
            total += len(dists)
            nearest_sum += min(dists)
        share = in_window / total if total else 0.0
        mean_nearest = nearest_sum / len(users) if users else 0.0
        results.append(ArmResult(arm, len(users), total, share, mean_nearest))
    return results


def run(profile: str, cut: str, window: float, workdir: Path) -> list[ArmResult]:
    """Build the served (≤ cut) and full (held-out) warehouses under `workdir`,
    then evaluate. Raises on a failed build or an empty scored set — a run that
    scored no user means the served or held-out warehouse came out empty (never
    a real result for a seeded profile), so it fails LOUDLY instead of rendering
    a zero block."""
    served_db = workdir / f"{profile}_served.duckdb"
    full_db = workdir / f"{profile}_full.duckdb"
    if not build(profile, served_db, cut):
        raise RuntimeError(f"holdout: served build failed ({profile}, ≤ {cut})")
    if not build(profile, full_db, None):
        raise RuntimeError(f"holdout: full build failed ({profile})")
    results = evaluate(served_schedule(served_db), heldout_opens(full_db, cut), window)
    if not results or results[0].n_users == 0:
        raise RuntimeError(
            f"holdout: no scored users ({profile}, cut {cut}) — the served or "
            "held-out warehouse is empty"
        )
    return results


# ------------------------------------------------------------------- the block


def render_block(
    profile: str, cut: str, window: float, results: list[ArmResult]
) -> str:
    """The Markdown table (one row per arm, ARMS order) plus the lift line —
    recommended − cohort on both measures."""
    header = ["arm", "users", "held_out_opens", "in_window_share", "mean_nearest_hours"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    by = {r.arm: r for r in results}
    for arm in ARMS:  # explicit ARMS order, never dict insertion order
        r = by[arm]
        lines.append(
            f"| {r.arm} | {r.n_users} | {r.n_opens} | "
            f"{r.in_window_share:.6f} | {r.mean_nearest_hours:.6f} |"
        )
    rec, coh = by["recommended"], by["cohort"]
    lines.append("")
    lines.append(
        f"Held-out opens uploaded after {cut}, window ±{window:g} h around the "
        f"served hour. Lift, recommended − cohort: in_window_share "
        f"{rec.in_window_share - coh.in_window_share:+.6f}; mean_nearest_hours "
        f"{rec.mean_nearest_hours - coh.mean_nearest_hours:+.6f}. Profile "
        f"`{profile}`, {rec.n_users} users with a served row and ≥1 held-out "
        f"open, {rec.n_opens} opens."
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
