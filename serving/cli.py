"""`make writeback PROFILE=<p>` and `make pipeline PROFILE=<p>`.

One entry point validates PROFILE (`[a-z0-9_]+`) before any path is derived:
writeback — upsert scores_send_time + the open dim_user tz into
            serving.send_schedule (the DuckDB stand-in for Spanner, §2.9):
            replace a user's row only on a strictly greater
            (model_version, computed_as_of); idempotent (a re-run writes 0).
pipeline  — the local chain with no scheduler: dbt build → eval → write-back,
            producing scores_send_time and send_schedule. Phase 8b's Airflow DAG
            orders the WRITING steps (dbt build → write-back) as make targets;
            eval stays a union-only validation gate here and in CI (Amendment 1).
            The DAG produces the same two tables byte-identically."""

from __future__ import annotations

import argparse
import sys

from loader import load as loader
from loader.cli import NAME_RE, die
from serving import writeback as wb


def validate_name(kind: str, value: str) -> str:
    if not NAME_RE.match(value):
        die(f"{kind}: refused — {kind} must match [a-z0-9_]+, got {value!r}")
    return value


def _require_db(profile: str) -> None:
    db = loader.db_path(profile)
    if not db.is_file():
        rel = db.relative_to(loader.ROOT)
        die(f"refused — no {rel}; run `make dbt-build PROFILE={profile}` first")


def writeback(profile: str) -> int:
    validate_name("PROFILE", profile)
    _require_db(profile)
    candidates, written = wb.write_back(profile)
    print(f"writeback OK: {profile}, {candidates} users, {written} written")
    return 0


def pipeline(profile: str) -> int:
    """dbt build (loads then builds) → eval → write-back, one process. Any step's
    non-zero exit stops the chain; scores_send_time and send_schedule are the
    outputs the 8b DAG must reproduce byte-identically."""
    validate_name("PROFILE", profile)
    from eval.cli import score_cmd
    from loader.cli import dbt_build

    if dbt_build(profile, ""):
        return 1
    if score_cmd(profile):
        return 1
    if writeback(profile):
        return 1
    print(f"pipeline OK: {profile}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("writeback")
    w.add_argument("profile")
    p = sub.add_parser("pipeline")
    p.add_argument("profile")
    a = ap.parse_args(argv)
    if a.cmd == "writeback":
        return writeback(a.profile)
    return pipeline(a.profile)


if __name__ == "__main__":
    sys.exit(main())
