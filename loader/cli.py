"""`make load | bq-load | dbt-build | drop-db | test-int-bigquery` (loader/).

One entry point validates every name (`[a-z0-9_]+`; PROJECT by the GCP
project-id shape `infra.cli.PROJECT_RE`) before any path, env var or client
is derived:
load      — fixtures/<p>/{raw,dims} → data/<p>.duckdb schema `raw`.
bq-load   — the same files → GCS staging → BigQuery `raw` (Phase 9b; cloud:
            CONFIRM=yes from the command line).
dbt-build — the TARGET's landing (duckdb → load(); bigquery → bq_load(), never
            the other — THROUGH lands only files uploaded on or before it, a
            per-interval landing, Phase 8b), then `dbt build --target` (the
            duckdb target reads `OTR_DUCKDB_PATH`; the bigquery target reads
            `OTR_GCP_PROJECT`, set HERE from the validated PROJECT, never from
            the caller's environment); exit 1 on any failure.
drop-db   — delete data/<p>.duckdb; only with CONFIRM=yes from the command line.
test-int-bigquery — validate + gate, then the pin-parity pytest behind OTR_INT."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import NoReturn

from infra.cli import validate_project
from loader import bq
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
        die(f"THROUGH: refused — want an upload date YYYY-MM-DD, got {value!r}")
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
CLOUD_TARGET = "bigquery"


def require_confirm(what: str, confirm: str, origin: str) -> None:
    if origin != "command line" or confirm != "yes":
        die(
            f"{what}: refused — a cloud-cost command; pass CONFIRM=yes on the "
            "command line (CLAUDE.md: ask first, every time)"
        )


def bq_load(
    profile: str,
    project: str,
    confirm: str = "",
    origin: str = "",
    through: str = "",
    clients: bq.ClientFactory = bq.GoogleClients,
) -> int:
    """The BigQuery landing (Phase 9b): PROFILE/PROJECT/THROUGH validated and
    CONFIRM gated before any client exists; recreates raw.events/raw.dim_user."""
    validate_name("PROFILE", profile)
    validate_project(project)
    cut = validate_through(through) if through else None
    require_confirm("bq-load", confirm, origin)
    try:
        source = loader.fixture_dir(profile)
        drift = loader.manifest_drift(source)
        if drift:
            print(
                f"bq-load DRIFT: {len(drift)} files differ from {source.name}/manifest"
            )
            return 1
        files, events, dims = bq.bq_load(profile, project, cut, clients)
    except FileNotFoundError as e:
        die(f"bq-load: refused — {e}")
    tag = "" if source.parent.name == "fixtures" else " (unfrozen)"
    landing = f", landing ≤ {cut}" if cut else ""
    print(f"bq-load: source={source.relative_to(loader.ROOT)}{tag} → {project}.raw")
    print(
        f"bq-load OK: {profile} — {files} files{landing}, "
        f"{events} event rows, {dims} dim rows"
    )
    return 0


def land(
    profile: str,
    target: str,
    project: str,
    through: str,
    confirm: str,
    origin: str,
    clients: bq.ClientFactory = bq.GoogleClients,
) -> int:
    """The TARGET's landing and only that one (Phase 9b, closes the 8b BACKLOG
    row): the DuckDB file for duckdb, GCS → BigQuery for bigquery."""
    if target == LOCAL_TARGET:
        return load(profile, through)
    return bq_load(profile, project, confirm, origin, through, clients)


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
    through: str = "",
    project: str = "",
    clients: bq.ClientFactory = bq.GoogleClients,
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
    if target not in (LOCAL_TARGET, CLOUD_TARGET):
        die(f"dbt-build: refused — no such target {target!r} (duckdb | bigquery)")
    if target == CLOUD_TARGET:
        # Phase 9b: the bigquery profile reads OTR_GCP_PROJECT with no default;
        # it is set here, from the validated PROJECT, and from nowhere else.
        os.environ["OTR_GCP_PROJECT"] = validate_project(project)
    # THROUGH lands only files uploaded on or before it, so a per-interval build
    # sees just that landing (Phase 8b); the landing validates the date and never
    # lets it become a path. Unset ⇒ loads all (the default build is unchanged).
    if land(profile, target, project, through, confirm, origin, clients):
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


def int_bigquery(profile: str, project: str, confirm: str, origin: str) -> int:
    """`make test-int-bigquery`: validate + gate in THIS process, then the
    parity pytest with OTR_INT=1 and the validated project in its env."""
    validate_name("PROFILE", profile)
    validate_project(project)
    require_confirm("test-int-bigquery", confirm, origin)
    env = {
        **os.environ,
        "OTR_INT": "1",
        "OTR_GCP_PROJECT": project,
        "OTR_PROFILE": profile,
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_int_bigquery.py"],
        cwd=str(loader.ROOT),
        env=env,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load")
    ld.add_argument("profile")
    ld.add_argument("--through", default="")
    bl = sub.add_parser("bq-load")
    bl.add_argument("profile")
    bl.add_argument("--project", default="")
    bl.add_argument("--confirm", default="")
    bl.add_argument("--confirm-origin", default="")
    bl.add_argument("--through", default="")
    ib = sub.add_parser("test-int-bigquery")
    ib.add_argument("profile")
    ib.add_argument("--project", default="")
    ib.add_argument("--confirm", default="")
    ib.add_argument("--confirm-origin", default="")
    b = sub.add_parser("dbt-build")
    b.add_argument("profile")
    b.add_argument("--target", default="")
    b.add_argument("--confirm", default="")
    b.add_argument("--confirm-origin", default="")
    b.add_argument("--full", default="")
    b.add_argument("--full-origin", default="")
    b.add_argument("--through", default="")
    b.add_argument("--project", default="")
    d = sub.add_parser("drop-db")
    d.add_argument("profile")
    d.add_argument("--confirm", default="")
    d.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "load":
        return load(a.profile, a.through)
    if a.cmd == "bq-load":
        return bq_load(a.profile, a.project, a.confirm, a.confirm_origin, a.through)
    if a.cmd == "test-int-bigquery":
        return int_bigquery(a.profile, a.project, a.confirm, a.confirm_origin)
    if a.cmd == "dbt-build":
        return dbt_build(
            a.profile,
            a.target,
            a.confirm,
            a.confirm_origin,
            a.full,
            a.full_origin,
            a.through,
            a.project,
        )
    return drop_db(a.profile, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
