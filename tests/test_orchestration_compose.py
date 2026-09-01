"""Phase 12 (specs/phase-12-live-run.md): the Docker-Airflow cloud REHEARSAL
override. `make test-int-airflow` runs on the BASE compose alone (offline DuckDB
parity); the demo-day rehearsal adds `docker-compose.cloud.yml` as a second `-f`
to run one DAG against real BigQuery + Spanner. This pins the security-critical
shape offline: the base file stays cloud-free, the override sets the DAG's cloud
target, requires a project, and mounts the ADC READ-ONLY — never a keyfile
(CLAUDE.md Credential standard)."""

from __future__ import annotations

from pathlib import Path

import yaml

ORCH = Path(__file__).resolve().parent.parent / "orchestration"
BASE = ORCH / "docker-compose.yml"
CLOUD = ORCH / "docker-compose.cloud.yml"

# Names the pipeline REFUSES if set in its environment (cloud-env / keyfile
# domain); none may appear in either compose file.
_FORBIDDEN_ENV = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CREDENTIALS",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
)


def _airflow_service(path: Path) -> dict:
    return yaml.safe_load(path.read_text())["services"]["airflow"]


def test_base_compose_stays_offline() -> None:
    """The base file (what `make test-int-airflow` uses) sets no cloud target and
    mounts no host path — so the offline parity run never touches the cloud."""
    svc = _airflow_service(BASE)
    env = svc.get("environment", {}) or {}
    assert "OTR_DAG_TARGET" not in env
    assert "volumes" not in svc  # no ADC / host mount on the offline path


def test_cloud_override_targets_bigquery_and_requires_a_project() -> None:
    """The override points the ONE DAG at BigQuery (so the write-back writes
    Spanner) and refuses a rehearsal with no project (`:?` guard)."""
    env = _airflow_service(CLOUD)["environment"]
    assert env["OTR_DAG_TARGET"] == "bigquery"
    assert ":?" in env["OTR_DAG_PROJECT"]  # required — errors if unset/empty


def test_cloud_override_mounts_adc_read_only_never_a_keyfile() -> None:
    """The impersonated-SA ADC is mounted READ-ONLY at the default gcloud path;
    no keyfile / credential-file-override env anywhere (Credential standard).
    Checks the PARSED config (env keys/values, volumes) — not comment prose."""
    svc = _airflow_service(CLOUD)
    vols = svc["volumes"]
    assert len(vols) == 1
    (mount,) = vols
    assert mount.endswith(":/home/airflow/.config/gcloud:ro")  # read-only ADC dir
    for path in (BASE, CLOUD):
        s = _airflow_service(path)
        env = s.get("environment", {}) or {}
        for name in _FORBIDDEN_ENV:
            assert name not in env, f"{name} must never be set in {path.name}"
        configured = list(env.values()) + list(s.get("volumes", []) or [])
        assert not any("-key.json" in v for v in configured), "no keyfile mount/env"
