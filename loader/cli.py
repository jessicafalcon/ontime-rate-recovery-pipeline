"""`make load | dbt-build | drop-db PROFILE=<p> [TARGET=duckdb] [CONFIRM=yes]`.

One entry point validates every name (`[a-z0-9_]+`) before any path is derived:
load      — fixtures/<p>/{raw,dims} → data/<p>.duckdb schema `raw`.
dbt-build — load, then `dbt build` against that file (`OTR_DUCKDB_PATH` is
            the one env var dbt/profiles.yml reads); exit 1 on any failure.
drop-db   — delete data/<p>.duckdb; only with CONFIRM=yes from the command line."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import NoReturn

from loader import load as loader

NAME_RE = re.compile(r"^[a-z0-9_]+$")
THROUGH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # an upload date; a subset of [0-9-]+
DBT_DIR = loader.ROOT / "dbt"


def die(msg: str, code: int = 2) -> NoReturn:
    print(msg)
    sys.exit(code)


def validate_name(kind: str, value: str) -> str:
    if not NAME_RE.match(value):
        die(f"{kind}: refused — {kind} must match [a-z0-9_]+, got {value!r}")
    return value


def validate_through(value: str) -> str:
    """A landing cut-off is an upload date; it filters file names already under
    fixtures/<p>/raw/ and never becomes a path (Phase 7 threat model)."""
    if not THROUGH_RE.match(value):
        die(f"THROUGH: refused — THROUGH must be an upload date YYYY-MM-DD, got {value!r}")
    return value


def load(profile: str, through: str = "") -> int:
    validate_name("PROFILE", profile)
    cut = validate_through(through) if through else None
    try:
        source = loader.fixture_dir(profile)
        drift = loader.manifest_drift(source)
        if drift:
            print(f"load DRIFT: {len(drift)} files differ from {source.name}/manifest")
            for d in drift[:20]:
                print(f"    {d}")
            return 1
        files, events, dims = loader.load(profile, through=cut)
    except FileNotFoundError as e:
        die(f"load: refused — {e}")
    except loader.ConflictingDuplicates as e:
        ids = str(e).split(", ")
        print(
            f"load CONFLICT: {len(ids)} insert_ids with one clock triple, "
            f"more than one payload: {e}"
        )
        return 1
    tag = "" if source.parent.name == "fixtures" else " (unfrozen)"
    landing = f", landing ≤ {cut}" if cut else ""
    print(f"load: source={source.relative_to(loader.ROOT)}{tag}")
    print(
        f"load OK: {profile} — {files} files{landing}, "
        f"{events} event rows, {dims} dim rows"
    )
    return 0


LOCAL_TARGET = "duckdb"


def full_refresh_args(full: str, origin: str) -> list[str]:
    """['--full-refresh'] only when FULL=yes comes from the command line — a
    rebuild-from-scratch of the incremental tables (Phase 7). An env FULL is
    ignored (the $(origin) gate), so a stray one leaves a normal incremental
    build, visible in the console."""
    if full == "yes" and origin == "command line":
        return ["--full-refresh"]
    return []


def dbt_build(
    profile: str,
    target: str,
    confirm: str = "",
    origin: str = "",
    full: str = "",
    full_origin: str = "",
) -> int:
    validate_name("PROFILE", profile)
    if full and full != "yes":
        die(f"dbt-build: refused — FULL takes only the literal 'yes', got {full!r}")
    target = target or LOCAL_TARGET
    validate_name("TARGET", target)
    if target != LOCAL_TARGET and (origin != "command line" or confirm != "yes"):
        die(
            f"dbt-build: refused — TARGET={target} is a cloud target; "
            "pass CONFIRM=yes on the command line (CLAUDE.md: ask first, every time)"
        )
    if load(profile):
        return 1
    os.environ.setdefault("DO_NOT_TRACK", "1")  # belt to dbt_project.yml's braces
    from dbt.cli.main import dbtRunner

    os.environ["OTR_DUCKDB_PATH"] = str(loader.db_path(profile))
    res = dbtRunner().invoke(
        [
            "build",
            "--project-dir",
            str(DBT_DIR),
            "--profiles-dir",
            str(DBT_DIR),
            "--target",
            target,
        ]
        + full_refresh_args(full, full_origin)
    )
    if not res.success:
        print(f"dbt-build FAIL: {profile}/{target}")
        return 1
    print(f"dbt-build OK: {profile}/{target}")
    return 0


def drop_db(profile: str, confirm: str, origin: str) -> int:
    validate_name("PROFILE", profile)
    if origin != "command line" or confirm != "yes":
        die("drop-db: refused — pass CONFIRM=yes on the command line")
    path = loader.db_path(profile)
    wal = path.with_name(path.name + ".wal")  # a leftover WAL replays into the next db
    removed = [p.name for p in (path, wal) if p.is_file()]
    for p in (path, wal):
        if p.is_file():
            p.unlink()
    if not removed:
        print(f"drop-db OK: nothing at {path.name}")
        return 0
    print(f"drop-db OK: removed {', '.join(removed)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load")
    ld.add_argument("profile")
    ld.add_argument("--through", default="")
    b = sub.add_parser("dbt-build")
    b.add_argument("profile")
    b.add_argument("--target", default="")
    b.add_argument("--confirm", default="")
    b.add_argument("--confirm-origin", default="")
    b.add_argument("--full", default="")
    b.add_argument("--full-origin", default="")
    d = sub.add_parser("drop-db")
    d.add_argument("profile")
    d.add_argument("--confirm", default="")
    d.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "load":
        return load(a.profile, a.through)
    if a.cmd == "dbt-build":
        return dbt_build(
            a.profile, a.target, a.confirm, a.confirm_origin, a.full, a.full_origin
        )
    return drop_db(a.profile, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
