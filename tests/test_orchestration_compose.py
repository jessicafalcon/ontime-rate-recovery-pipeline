"""Phase 12 (specs/phase-12-live-run.md): the Docker-Airflow cloud REHEARSAL
override. `make test-int-airflow` runs on the BASE compose alone (offline DuckDB
parity); the demo-day rehearsal adds `docker-compose.cloud.yml` as a second `-f`
to run one DAG against real BigQuery + Spanner. This pins the security-critical
shape offline WITHOUT a YAML dependency (round 1 #5 — the files are small and
fixed, so a minimal indentation parse extracts the airflow service's env keys and
volumes) and checks credentials with an EXACT allowlist + the repo's own cloud-env
predicate `infra.cli.in_cloud_namespace` (round 1 #4), never a hand denylist."""

from __future__ import annotations

from pathlib import Path

from infra.cli import in_cloud_namespace

ORCH = Path(__file__).resolve().parent.parent / "orchestration"
BASE = ORCH / "docker-compose.yml"
CLOUD = ORCH / "docker-compose.cloud.yml"

# The override may declare EXACTLY these env keys — an allowlist, so any new
# credential-shaped name is a visible edit, never a silent pass.
_ALLOWED_ENV = {"OTR_DAG_TARGET", "OTR_DAG_PROJECT"}


def _override_env_and_volumes() -> tuple[list[str], list[str]]:
    """The override airflow service's environment keys and volume entries, by a
    minimal indentation parse (the file has one `environment:` block then one
    `volumes:` block; no YAML dependency)."""
    lines = CLOUD.read_text().splitlines()
    env_i = next(i for i, ln in enumerate(lines) if ln.strip() == "environment:")
    vol_i = next(i for i, ln in enumerate(lines) if ln.strip() == "volumes:")
    assert env_i < vol_i, "parser assumes environment: precedes volumes:"
    env_keys = [
        ln.strip().split(":", 1)[0]
        for ln in lines[env_i + 1 : vol_i]
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    volumes = [
        ln.strip()[2:] for ln in lines[vol_i + 1 :] if ln.strip().startswith("- ")
    ]
    return env_keys, volumes


def test_base_compose_stays_offline() -> None:
    """The base file (what `make test-int-airflow` uses) sets no cloud target and
    mounts no host path — so the offline parity run never touches the cloud."""
    text = BASE.read_text()
    assert "OTR_DAG_TARGET" not in text
    assert "volumes:" not in text  # no ADC / host mount on the offline path


def test_cloud_override_targets_bigquery_and_requires_a_project() -> None:
    """The override points the ONE DAG at BigQuery (so the write-back writes
    Spanner) and refuses a rehearsal with no project (`:?` guard)."""
    env_keys, _ = _override_env_and_volumes()
    assert set(env_keys) == _ALLOWED_ENV  # exact allowlist
    text = CLOUD.read_text()
    assert "OTR_DAG_TARGET: bigquery" in text
    proj = next(
        ln for ln in text.splitlines() if ln.strip().startswith("OTR_DAG_PROJECT:")
    )
    assert ":?" in proj  # compose required-variable form — errors if unset/empty


def test_cloud_override_env_names_are_not_cloud_credentials() -> None:
    """round 1 #4: allowlist + the repo's own cloud-env predicate, NOT a hand
    denylist — no override env name is in the cloud-env refuse domain, so a
    GOOGLE_/CLOUDSDK_/… credential name could never be declared here unnoticed."""
    env_keys, _ = _override_env_and_volumes()
    for name in env_keys:
        assert not in_cloud_namespace(name), name


def test_cloud_override_mounts_adc_read_only_never_a_keyfile() -> None:
    """The impersonated-SA ADC is mounted READ-ONLY at the default gcloud
    discovery path; there is exactly one mount and it is not a random keyfile. The
    target-path assertion holds whether the mount is the gcloud dir or just the
    ADC json (round 1 #6 narrows source→file without changing this pin)."""
    _, volumes = _override_env_and_volumes()
    assert len(volumes) == 1
    (mount,) = volumes
    assert ":/home/airflow/.config/gcloud" in mount  # the ADC discovery location
    assert mount.endswith(":ro")  # read-only
    assert "-key.json" not in mount  # not a service-account key file
