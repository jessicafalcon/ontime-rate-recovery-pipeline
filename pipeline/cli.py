"""`make dbt-build | test-int-bigquery | test-int-spanner` (pipeline/).

The pipeline plumbing that is not landing: the TARGET-dispatched dbt build and
the integration-test launchers. Each validates every name (`[a-z0-9_]+`;
PROJECT by the GCP project-id shape) and gates CONFIRM before any client is
derived. The landing itself lives in `landing/` — this module imports the
landing orchestrator (`landing.cli.land`) and the shared validators, and adds
nothing to who-writes-what (Phase 10 exit: `fix/landing-package`).

dbt-build — the TARGET's landing (duckdb → landing.load(); bigquery →
            landing.bq_load(), never the other — THROUGH lands only files
            uploaded on or before it, a per-interval landing, Phase 8b), then
            `dbt build --target` (the duckdb target reads `OTR_DUCKDB_PATH`; the
            bigquery target reads `OTR_GCP_PROJECT`, set HERE from the validated
            PROJECT, never from the caller's environment); exit 1 on any failure.
test-int-bigquery — validate + gate, then the pin-parity pytest behind OTR_INT.
test-int-spanner — validate + gate, then the Spanner/federation pytest (Phase 10)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from infra.cli import confirmed, validate_project
from landing import bq
from landing import load as landing
from landing.cli import (
    CLOUD_TARGET,
    LOCAL_TARGET,
    die,
    land,
    require_confirm,
    validate_name,
)

DBT_DIR = landing.ROOT / "dbt"
INT_PROFILE = "tiny"  # the integration runs' pins are tiny's by definition

# The serving+landing image (fix/composer-cosmos) — the Artifact Registry path the
# Cloud-Composer KubernetesPodOperator pods pull. `:latest` is a demo convenience
# (Terraform sets OTR_SERVING_IMAGE to the same); a production pin is a digest.
SERVING_IMAGE_REGION = "us-central1"
SERVING_IMAGE_REPO = "ontime"
SERVING_IMAGE_NAME = "serving"
SERVING_IMAGE_TAG = "latest"


def serving_image_uri(project: str) -> str:
    """The AR image path for a validated project — the ONE place the registry
    path is spelled (the DAG reads it from OTR_SERVING_IMAGE, Terraform sets it)."""
    return (
        f"{SERVING_IMAGE_REGION}-docker.pkg.dev/{project}/"
        f"{SERVING_IMAGE_REPO}/{SERVING_IMAGE_NAME}:{SERVING_IMAGE_TAG}"
    )


def composer_manifest() -> int:
    """`make composer-dbt-manifest` (offline): `dbt parse` on the duckdb target
    renders `dbt/target/manifest.json` — the precompiled manifest Cosmos loads on
    Composer (LoadMode.DBT_MANIFEST), so the scheduler runs no dbt at parse. No
    cloud, no delete, no variable; the manifest is a gitignored build artifact the
    deploy uploads into the DAG bucket."""
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ["OTR_DUCKDB_PATH"] = ":memory:"  # parse never opens the db
    from dbt.cli.main import dbtRunner

    res = dbtRunner().invoke(
        [
            "parse",
            "--project-dir",
            str(DBT_DIR),
            "--profiles-dir",
            str(DBT_DIR),
            "--target",
            "duckdb",
        ]  # fmt: skip
    )
    if not res.success:
        print("composer-dbt-manifest FAIL")
        return 1
    manifest = DBT_DIR / "target" / "manifest.json"
    print(f"composer-dbt-manifest OK: {manifest.relative_to(landing.ROOT)}")
    return 0


def build_serving_image(project: str, confirm: str = "", origin: str = "") -> int:
    """`make build-serving-image PROJECT=<id> CONFIRM=yes` (cloud-cost — pushes to
    Artifact Registry): validate PROJECT and gate CONFIRM (command-line origin)
    BEFORE any docker/registry call, then build the serving+landing image and push
    it. Ask-first; the push runs in 7b."""
    validate_project(project)
    require_confirm("build-serving-image", confirm, origin)
    image = serving_image_uri(project)
    dockerfile = landing.ROOT / "orchestration" / "images" / "serving" / "Dockerfile"
    build = subprocess.run(
        ["docker", "build", "-f", str(dockerfile), "-t", image, "."],
        cwd=str(landing.ROOT),
    )
    if build.returncode:
        return build.returncode
    push = subprocess.run(["docker", "push", image], cwd=str(landing.ROOT))
    if push.returncode:
        return push.returncode
    print(f"build-serving-image OK: {image}")
    return 0


def full_refresh_args(full: str, origin: str) -> list[str]:
    """['--full-refresh'] only when FULL=yes comes from the command line — a
    rebuild-from-scratch of the incremental tables (Phase 7). An env FULL is
    ignored (the $(origin) gate), so a stray one leaves a normal incremental
    build, visible in the console. The origin rule is `infra.cli.confirmed`
    (round 5 O5: one predicate, no inlined copy)."""
    if confirmed(full, origin):
        return ["--full-refresh"]
    return []


def dbt_vars_args(dim_user_identifier: str) -> list[str]:
    """['--vars', '{dim_user_identifier: <relation>}'] for a validated
    `[a-z0-9_]+` relation name; [] when unset (the default build is
    unchanged). Anything else refuses — the seam admits exactly this one var."""
    if not dim_user_identifier:
        return []
    validate_name("dim_user_identifier", dim_user_identifier)
    return ["--vars", f"{{dim_user_identifier: {dim_user_identifier}}}"]


def dbt_build(
    profile: str,
    target: str,
    confirm: str = "",
    origin: str = "",
    full: str = "",
    full_origin: str = "",
    through: str = "",
    project: str = "",
    clients: bq.ClientFactory | None = None,
    dim_user_identifier: str = "",
) -> int:
    """`dim_user_identifier` is an internal seam (no make variable): the ONE
    dbt var a build may override from here — the Spanner integration run
    passes `dim_user_spanner` to build against the federation view (§3.3's
    source swap; the three goldens must not move). Validated like a name and
    rendered by `dbt_vars_args`; any other var override has no path in."""
    validate_name("PROFILE", profile)
    vars_args = dbt_vars_args(dim_user_identifier)
    if full and full != "yes":
        die(f"dbt-build: refused — FULL takes only the literal 'yes', got {full!r}")
    target = target or LOCAL_TARGET
    validate_name("TARGET", target)
    if target != LOCAL_TARGET:
        require_confirm(f"dbt-build TARGET={target}", confirm, origin)
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

    os.environ["OTR_DUCKDB_PATH"] = str(landing.db_path(profile))
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
        + vars_args
    )
    if not res.success:
        print(f"dbt-build FAIL: {profile}/{target}")
        return 1
    print(f"dbt-build OK: {profile}/{target}")
    return 0


def int_bigquery(profile: str, project: str, confirm: str, origin: str) -> int:
    """`make test-int-bigquery`: validate + gate in THIS process, then the
    parity pytest with OTR_INT=1 and the validated project in its env."""
    validate_name("PROFILE", profile)
    validate_project(project)
    require_confirm("test-int-bigquery", confirm, origin)
    # Amendment V: the gate that ran HERE is carried to the fixture, never
    # re-derived there (a bare pytest with OTR_INT=1 must not forge it).
    env = {
        **os.environ,
        "OTR_INT": "1",
        "OTR_GCP_PROJECT": project,
        "OTR_PROFILE": profile,
        "OTR_CONFIRM": confirm,
        "OTR_CONFIRM_ORIGIN": origin,
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_int_bigquery.py"],
        cwd=str(landing.ROOT),
        env=env,
    ).returncode


def int_spanner(profile: str, project: str, confirm: str, origin: str) -> int:
    """`make test-int-spanner` (Phase 10): validate + gate in THIS process, then
    the Spanner/federation pytest with OTR_INT=1 and the validated project in
    its env (the Amendment V shape: the gate that ran here is carried to the
    fixture, never re-derived there). PROFILE is `tiny` by definition — the
    goldens and the row hash it asserts are tiny's — so another value is a
    CLI refusal, not a fixture assertion after the gate."""
    validate_name("PROFILE", profile)
    if profile != INT_PROFILE:
        die(
            "test-int-spanner: refused — PROFILE is "
            f"{INT_PROFILE!r} only, got {profile!r}"
        )
    validate_project(project)
    require_confirm("test-int-spanner", confirm, origin)
    env = {
        **os.environ,
        "OTR_INT": "1",
        "OTR_GCP_PROJECT": project,
        "OTR_PROFILE": profile,
        "OTR_CONFIRM": confirm,
        "OTR_CONFIRM_ORIGIN": origin,
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_int_spanner.py"],
        cwd=str(landing.ROOT),
        env=env,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ib = sub.add_parser("test-int-bigquery")
    ib.add_argument("profile")
    ib.add_argument("--project", default="")
    ib.add_argument("--confirm", default="")
    ib.add_argument("--confirm-origin", default="")
    isp = sub.add_parser("test-int-spanner")
    isp.add_argument("profile")
    isp.add_argument("--project", default="")
    isp.add_argument("--confirm", default="")
    isp.add_argument("--confirm-origin", default="")
    b = sub.add_parser("dbt-build")
    b.add_argument("profile")
    b.add_argument("--target", default="")
    b.add_argument("--confirm", default="")
    b.add_argument("--confirm-origin", default="")
    b.add_argument("--full", default="")
    b.add_argument("--full-origin", default="")
    b.add_argument("--through", default="")
    b.add_argument("--project", default="")
    sub.add_parser("composer-manifest")
    bi = sub.add_parser("build-serving-image")
    bi.add_argument("project", nargs="?", default="")
    bi.add_argument("--confirm", default="")
    bi.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "test-int-bigquery":
        return int_bigquery(a.profile, a.project, a.confirm, a.confirm_origin)
    if a.cmd == "test-int-spanner":
        return int_spanner(a.profile, a.project, a.confirm, a.confirm_origin)
    if a.cmd == "composer-manifest":
        return composer_manifest()
    if a.cmd == "build-serving-image":
        return build_serving_image(a.project, a.confirm, a.confirm_origin)
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


if __name__ == "__main__":
    sys.exit(main())
