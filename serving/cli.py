"""`make writeback PROFILE=<p> [TARGET=duckdb|spanner]` and `make pipeline`.

One entry point validates every name (PROFILE/TARGET `[a-z0-9_]+`; PROJECT by
the GCP project-id shape) before any path or client is derived:
writeback — upsert scores_send_time + the open dim_user tz into send_schedule,
            replacing a user's row only on a strictly greater
            (model_version, computed_as_of); idempotent (a re-run writes 0).
            TARGET=duckdb (default): serving.send_schedule in data/<p>.duckdb
            (the stand-in, §2.9). TARGET=spanner (Phase 10): read the same two
            relations off BigQuery `ontime`, write the Spanner table — a
            cloud-cost command, CONFIRM=yes from the command line, gated
            BEFORE any client exists. PROFILE names no input there (the read
            is the warehouse's `ontime`, whatever build landed it), so it is
            optional, validated when given, and the OK line names the
            warehouse read instead: `writeback OK: <project>.ontime → spanner`.
pipeline  — the local chain with no scheduler: dbt build → eval → write-back,
            producing scores_send_time and send_schedule. Phase 8b's Airflow DAG
            orders the WRITING steps (dbt build → write-back) as make targets;
            eval stays a union-only validation gate here and in CI (Amendment 1).
            The DAG produces the same two tables byte-identically."""

from __future__ import annotations

import argparse
import sys

from infra.cli import validate_project
from loader import load as loader
from loader.cli import NAME_RE, die, require_confirm
from serving import spanner as spanner_wb
from serving import writeback as wb

LOCAL_TARGET = "duckdb"
CLOUD_TARGET = "spanner"


def validate_name(kind: str, value: str) -> str:
    if not NAME_RE.match(value):
        die(f"{kind}: refused — {kind} must match [a-z0-9_]+, got {value!r}")
    return value


def _require_db(profile: str) -> None:
    db = loader.db_path(profile)
    if not db.is_file():
        rel = db.relative_to(loader.ROOT)
        die(f"refused — no {rel}; run `make dbt-build PROFILE={profile}` first")


def writeback(
    profile: str,
    target: str = "",
    project: str = "",
    confirm: str = "",
    origin: str = "",
) -> int:
    target = target or LOCAL_TARGET
    validate_name("TARGET", target)
    if target not in (LOCAL_TARGET, CLOUD_TARGET):
        die(f"writeback: refused — no such target {target!r} (duckdb | spanner)")
    if target == CLOUD_TARGET:
        if profile:  # not an input on this target; still never an unvalidated value
            validate_name("PROFILE", profile)
        require_confirm("writeback TARGET=spanner", confirm, origin)
        validate_project(project)
        candidates, written = spanner_wb.write_back(project)
        source = f"{project}.{spanner_wb.MODELS_DATASET} → spanner"
    else:
        validate_name("PROFILE", profile)
        _require_db(profile)
        candidates, written = wb.write_back(profile)
        source = profile
    print(f"writeback OK: {source}, {candidates} users, {written} written")
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
    w.add_argument("--target", default="")
    w.add_argument("--project", default="")
    w.add_argument("--confirm", default="")
    w.add_argument("--confirm-origin", default="")
    p = sub.add_parser("pipeline")
    p.add_argument("profile")
    a = ap.parse_args(argv)
    if a.cmd == "writeback":
        return writeback(a.profile, a.target, a.project, a.confirm, a.confirm_origin)
    return pipeline(a.profile)


if __name__ == "__main__":
    sys.exit(main())
