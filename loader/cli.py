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
DBT_DIR = loader.ROOT / "dbt"


def die(msg: str, code: int = 2) -> NoReturn:
    print(msg)
    sys.exit(code)


def validate_name(kind: str, value: str) -> str:
    if not NAME_RE.match(value):
        die(f"{kind}: refused — {kind} must match [a-z0-9_]+, got {value!r}")
    return value


def load(profile: str) -> int:
    validate_name("PROFILE", profile)
    try:
        source = loader.fixture_dir(profile)
        drift = loader.manifest_drift(source)
        if drift:
            print(f"load DRIFT: {len(drift)} files differ from {source.name}/manifest")
            for d in drift[:20]:
                print(f"    {d}")
            return 1
        files, events, dims = loader.load(profile)
    except FileNotFoundError as e:
        die(f"load: refused — {e}")
    except loader.ConflictingDuplicates as e:
        print(f"load CONFLICT: insert_ids with one clock triple and two payloads: {e}")
        return 1
    tag = "" if source.parent.name == "fixtures" else " (unfrozen)"
    print(f"load: source={source.relative_to(loader.ROOT)}{tag}")
    print(f"load OK: {profile} — {files} files, {events} event rows, {dims} dim rows")
    return 0


LOCAL_TARGET = "duckdb"


def dbt_build(profile: str, target: str, confirm: str = "", origin: str = "") -> int:
    validate_name("PROFILE", profile)
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
    sub.add_parser("load").add_argument("profile")
    b = sub.add_parser("dbt-build")
    b.add_argument("profile")
    b.add_argument("--target", default="")
    b.add_argument("--confirm", default="")
    b.add_argument("--confirm-origin", default="")
    d = sub.add_parser("drop-db")
    d.add_argument("profile")
    d.add_argument("--confirm", default="")
    d.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "load":
        return load(a.profile)
    if a.cmd == "dbt-build":
        return dbt_build(a.profile, a.target, a.confirm, a.confirm_origin)
    return drop_db(a.profile, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
