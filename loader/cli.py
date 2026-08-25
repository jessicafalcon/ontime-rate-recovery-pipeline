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
        files, events, dims = loader.load(profile)
    except FileNotFoundError as e:
        die(f"load: refused — {e}")
    print(f"load OK: {profile} — {files} files, {events} event rows, {dims} dim rows")
    return 0


def dbt_build(profile: str, target: str) -> int:
    validate_name("PROFILE", profile)
    validate_name("TARGET", target or "duckdb")
    target = target or "duckdb"
    if load(profile):
        return 1
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
    if not path.is_file():
        print(f"drop-db OK: nothing at {path.name}")
        return 0
    path.unlink()
    print(f"drop-db OK: removed {path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("load").add_argument("profile")
    b = sub.add_parser("dbt-build")
    b.add_argument("profile")
    b.add_argument("--target", default="")
    d = sub.add_parser("drop-db")
    d.add_argument("profile")
    d.add_argument("--confirm", default="")
    d.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "load":
        return load(a.profile)
    if a.cmd == "dbt-build":
        return dbt_build(a.profile, a.target)
    return drop_db(a.profile, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
