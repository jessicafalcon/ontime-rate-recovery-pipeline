"""`make load | bq-load | spanner-load | drop-db` (landing/).

One entry point validates every name (`[a-z0-9_]+`; PROJECT by the GCP
project-id shape `infra.cli.PROJECT_RE`) before any path, env var or client
is derived:
load      — fixtures/<p>/{raw,dims} → data/<p>.duckdb schema `raw`.
bq-load   — the same files → GCS staging → BigQuery `raw` (Phase 9b; cloud:
            CONFIRM=yes from the command line).
spanner-load — the same dim seed → the Spanner `dim_user` table, the
            production dims home BigQuery federates from (Phase 10; cloud:
            CONFIRM=yes from the command line).
drop-db   — delete data/<p>.duckdb; only with CONFIRM=yes from the command line.

The TARGET-dispatched dbt build and the integration-test launchers moved to
`pipeline/cli.py` (Phase 10 exit: `fix/landing-package`); they import `land`
and the validators here. `land` is the landing dispatcher both keep sharing."""

from __future__ import annotations

import argparse
import re
import sys
from typing import NoReturn

from infra.cli import confirmed, refuse_cloud_env, validate_project
from landing import bq, spanner
from landing import load as landing

NAME_RE = re.compile(r"^[a-z0-9_]+$")
THROUGH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # an upload date; a subset of [0-9-]+


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
        source = landing.fixture_dir(profile)
        drift = landing.manifest_drift(source)
        if drift:
            print(f"load DRIFT: {len(drift)} files differ from {source.name}/manifest")
            for d in drift[:20]:
                print(f"    {d}")
            return 1
        files, events, dims = landing.load(profile, through=cut)
    except FileNotFoundError as e:
        die(f"load: refused — {e}")
    except landing.ConflictingDuplicates as e:
        ids = str(e).split(", ")
        print(
            f"load CONFLICT: {len(ids)} insert_ids with one clock triple, "
            f"more than one payload: {e}"
        )
        return 1
    tag = "" if source.parent.name == "fixtures" else " (unfrozen)"
    landing_note = f", landing ≤ {cut}" if cut else ""
    print(f"load: source={source.relative_to(landing.ROOT)}{tag}")
    print(
        f"load OK: {profile} — {files} files{landing_note}, "
        f"{events} event rows, {dims} dim rows"
    )
    return 0


LOCAL_TARGET = "duckdb"
CLOUD_TARGET = "bigquery"


def require_confirm(what: str, confirm: str, origin: str) -> None:
    """The ONE gate every cloud-cost command passes through, before any
    client: CONFIRM=yes from the command line, and no unlisted Google-namespace
    variable in the environment (infra.cli's cloud-env allowlist — round 2 #2,
    round 4 Amendment N2)."""
    if not confirmed(confirm, origin):
        die(
            f"{what}: refused — a cloud-cost command; pass CONFIRM=yes on the "
            "command line (CLAUDE.md: ask first, every time)"
        )
    refuse_cloud_env(what)


def bq_load(
    profile: str,
    project: str,
    confirm: str = "",
    origin: str = "",
    through: str = "",
    clients: bq.ClientFactory | None = None,
) -> int:
    """The BigQuery landing (Phase 9b): PROFILE/PROJECT/THROUGH validated and
    CONFIRM gated before any client exists; recreates raw.events/raw.dim_user."""
    validate_name("PROFILE", profile)
    validate_project(project)
    cut = validate_through(through) if through else None
    require_confirm("bq-load", confirm, origin)
    try:
        source = landing.fixture_dir(profile)
        drift = landing.manifest_drift(source)
        if drift:
            print(
                f"bq-load DRIFT: {len(drift)} files differ from {source.name}/manifest"
            )
            return 1
        files, events, dims = bq.bq_load(profile, project, cut, clients)
    except FileNotFoundError as e:
        die(f"bq-load: refused — {e}")
    tag = "" if source.parent.name == "fixtures" else " (unfrozen)"
    landing_note = f", landing ≤ {cut}" if cut else ""
    print(f"bq-load: source={source.relative_to(landing.ROOT)}{tag} → {project}.raw")
    print(
        f"bq-load OK: {profile} — {files} files{landing_note}, "
        f"{events} event rows, {dims} dim rows"
    )
    return 0


def spanner_load(
    profile: str,
    project: str,
    confirm: str = "",
    origin: str = "",
    clients: spanner.DimClientFactory | None = None,
) -> int:
    """The Spanner dims landing (Phase 10): PROFILE/PROJECT validated and
    CONFIRM gated before any client exists; upserts the seed into `dim_user`
    (idempotent — same key, same values). Needs a spanner-enabled stack."""
    validate_name("PROFILE", profile)
    validate_project(project)
    require_confirm("spanner-load", confirm, origin)
    try:
        source = landing.fixture_dir(profile)
        drift = landing.manifest_drift(source)
        if drift:
            print(
                f"spanner-load DRIFT: {len(drift)} files differ from "
                f"{source.name}/manifest"
            )
            return 1
        rows = spanner.load_dims(profile, project, clients)
    except FileNotFoundError as e:
        die(f"spanner-load: refused — {e}")
    except ValueError as e:
        die(f"spanner-load: refused — {e}")
    tag = "" if source.parent.name == "fixtures" else " (unfrozen)"
    print(
        f"spanner-load: source={source.relative_to(landing.ROOT)}{tag} "
        f"→ {project} spanner {spanner.INSTANCE}/{spanner.DATABASE}"
    )
    print(f"spanner-load OK: {profile} — {rows} dim rows")
    return 0


def land(
    profile: str,
    target: str,
    project: str,
    through: str,
    confirm: str,
    origin: str,
    clients: bq.ClientFactory | None = None,
) -> int:
    """The TARGET's landing and only that one (Phase 9b, closes the 8b BACKLOG
    row): the DuckDB file for duckdb, GCS → BigQuery for bigquery. The dbt build
    that consumes it lives in `pipeline/cli.py` and calls this."""
    if target == LOCAL_TARGET:
        return load(profile, through)
    return bq_load(profile, project, confirm, origin, through, clients)


def drop_db(profile: str, confirm: str, origin: str) -> int:
    validate_name("PROFILE", profile)
    if not confirmed(confirm, origin):
        die("drop-db: refused — pass CONFIRM=yes on the command line")
    path = landing.db_path(profile)
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
    bl = sub.add_parser("bq-load")
    bl.add_argument("profile")
    bl.add_argument("--project", default="")
    bl.add_argument("--confirm", default="")
    bl.add_argument("--confirm-origin", default="")
    bl.add_argument("--through", default="")
    sl = sub.add_parser("spanner-load")
    sl.add_argument("profile")
    sl.add_argument("--project", default="")
    sl.add_argument("--confirm", default="")
    sl.add_argument("--confirm-origin", default="")
    d = sub.add_parser("drop-db")
    d.add_argument("profile")
    d.add_argument("--confirm", default="")
    d.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "load":
        return load(a.profile, a.through)
    if a.cmd == "bq-load":
        return bq_load(a.profile, a.project, a.confirm, a.confirm_origin, a.through)
    if a.cmd == "spanner-load":
        return spanner_load(a.profile, a.project, a.confirm, a.confirm_origin)
    return drop_db(a.profile, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
