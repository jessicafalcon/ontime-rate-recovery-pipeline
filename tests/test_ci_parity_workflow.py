"""fix/ci-bigquery-parity — the bigquery-parity CI workflow, pinned offline.

The workflow is yaml no mutation operator addresses; these static checks are its
pins (the Phase 9a treatment of the `.tf` tree). Each property in the spec's
Invariants table has a test here that reddens when the property is removed from
the workflow. No network, no cloud, no GitHub — pure file read.

Central property: CI authenticates by short-lived Workload Identity Federation
(no key at rest) and hands the credential to the pipeline as ADC via
CLOUDSDK_CONFIG, so the pipeline's existing cloud-env allowlist
(`infra.cli.CLOUD_ENV_ALLOW`) passes UNWIDENED — the make step carries no
GOOGLE_* credential var the gate refuses.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from infra.cli import CLOUD_ENV_ALLOW, in_cloud_namespace, unlisted_cloud_env

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bigquery-parity.yml"

# YAML 1.1 (PyYAML) parses the bare key `on` as the boolean True; GitHub reads it
# as the string "on". Look it up under both so the test pins the real key.
_ON_KEYS = ("on", True)

_SHA_PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _doc() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text())


def _on(doc: dict[str, Any]) -> Any:
    for k in _ON_KEYS:
        if k in doc:
            return doc[k]
    raise AssertionError("workflow has no `on:` trigger mapping")


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in doc["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def _auth_step(doc: dict[str, Any]) -> dict[str, Any]:
    for s in _steps(doc):
        if str(s.get("uses", "")).startswith("google-github-actions/auth@"):
            return s
    raise AssertionError("no google-github-actions/auth step found")


def _make_step(doc: dict[str, Any]) -> dict[str, Any]:
    for s in _steps(doc):
        if "test-int-bigquery" in str(s.get("run", "")):
            return s
    raise AssertionError("no step running `make test-int-bigquery` found")


def test_dispatch_only() -> None:
    """For all triggers, the job runs on workflow_dispatch alone — no fork PR or
    branch push can trigger it or reach the cloud credential."""
    on = _on(_doc())
    # `on:` may parse as {"workflow_dispatch": None} or the bare list/string form;
    # normalise to the set of trigger names.
    if isinstance(on, dict):
        triggers = set(on)
    elif isinstance(on, list):
        triggers = set(on)
    else:
        triggers = {on}
    assert triggers == {"workflow_dispatch"}, triggers


def test_permissions_are_minimal() -> None:
    """The token can mint an OIDC token and read the repo, nothing more."""
    assert _doc()["permissions"] == {"id-token": "write", "contents": "read"}


def test_every_action_is_sha_pinned() -> None:
    """Every `uses:` is pinned to a 40-hex commit SHA — never a floating tag a
    compromised action could move under the token."""
    used = [str(s["uses"]) for s in _steps(_doc()) if "uses" in s]
    assert used, "workflow uses no actions"
    for u in used:
        assert _SHA_PINNED.match(u), f"not SHA-pinned: {u}"


def test_auth_uses_wif_and_no_literal_identity() -> None:
    """Auth is WIF (no key); provider/SA come from repo variables, not literals;
    the env-var export is disabled so nothing the gate refuses leaks job-wide."""
    step = _auth_step(_doc())
    with_ = step["with"]
    var_ref = re.compile(r"\$\{\{\s*vars\.\w+\s*\}\}")
    assert var_ref.fullmatch(with_["workload_identity_provider"])
    assert var_ref.fullmatch(with_["service_account"])
    # export_environment_variables must be OFF (default true would export
    # GOOGLE_APPLICATION_CREDENTIALS et al. — all refused by CLOUD_ENV_ALLOW).
    assert with_["export_environment_variables"] is False
    assert with_["create_credentials_file"] is True
    # No committed key, no credentials_json inline secret.
    assert "credentials_json" not in with_

    # No literal identity anywhere in the tracked file (check-docs check 5 is the
    # repo-wide gate; this pins the workflow specifically).
    text = WORKFLOW.read_text()
    assert ".iam.gserviceaccount.com" not in text
    assert not re.search(r"principalSet://", text)
    assert not re.search(r"projects/\d", text)


def test_make_step_carries_no_refused_cloud_env() -> None:
    """The make step's environment carries no cloud-env name the gate refuses,
    and does set CLOUDSDK_CONFIG (where ADC is placed). The allowlist is unwidened
    — refuse_cloud_env runs first inside test-int-bigquery and must pass."""
    step = _make_step(_doc())
    env = step.get("env", {}) or {}
    # keys only — values are `${{ runner.temp }}/...` expressions, not names.
    refused = unlisted_cloud_env({k: "" for k in env})
    assert refused == [], f"refused cloud-env names in the make step: {refused}"
    assert "CLOUDSDK_CONFIG" in env
    assert in_cloud_namespace("CLOUDSDK_CONFIG")  # it IS in the domain …
    assert "CLOUDSDK_CONFIG" in CLOUD_ENV_ALLOW  # … and admitted (the one seam)

    # The run script must not re-introduce a refused var (e.g. export
    # GOOGLE_APPLICATION_CREDENTIALS=…) — it copies ADC into CLOUDSDK_CONFIG.
    run = str(step["run"])
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in run
    assert "application_default_credentials.json" in run
    assert "CONFIRM=yes" in run
