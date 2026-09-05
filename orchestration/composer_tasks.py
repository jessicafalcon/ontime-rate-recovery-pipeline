"""The Cloud-Composer runtime's task manifest — the one definition the Cosmos +
KubernetesPodOperator DAG and its offline structure test share (fix/composer-cosmos,
7a).

stdlib-only, by design: a flat Composer `dags/` bucket has no repo packages on
`sys.path` (the Phase 12 `tasks.py` lesson), so this imports nothing from the
project — it cannot pull `infra.cli` (which imports `generator.manifest`, absent
from the bucket). The GCP project-id shape is therefore INLINED and pinned equal
to `infra.cli.PROJECT_RE` by a test (the full repo is present offline).

What lives here:

- `build_kpo_command(step, project)` — the container argv for each NON-dbt step
  (the two landings and the write-back), an ALLOWLIST over the step name (an
  unrecognised step or a malformed project REFUSES; Boundary contract). The pod
  runs the committed module CLI directly (`python -m landing.cli …` /
  `python -m serving.cli …`) — there is no `make` in the image. The make-level
  `$(origin CONFIRM)` gate is a SHELL-origin guard with no analog inside a
  single-purpose pod whose argv is baked into the reviewed DAG, so the argv bakes
  `--confirm yes --confirm-origin "command line"`: the pod's whole input is the
  reviewed command, equivalent to a human typing it — not a stray env var, which
  is what `$(origin)` exists to exclude.
- the Cosmos config CONSTANTS (project dir, precompiled manifest path, the
  reused `profiles.yml`, the execution/load-mode NAMES) the DAG hands to
  `DbtTaskGroup` — data the offline stub test asserts without importing Cosmos.

Determinism: `THROUGH` reaches the BigQuery landing only as a literal Jinja token
Airflow renders at run (`{{ data_interval_end | ds }}`) — we compute nothing
(CLAUDE.md "Airflow contains no logic"); `build_kpo_command` selects an argv from
an allowlist, never a path.
"""

from __future__ import annotations

import re

PROFILE = "tiny"

# The dbt build target (BigQuery) and the write-back target (Spanner) the pods
# and Cosmos run against — the cloud pair Phase 9b/10 proved live.
BUILD_TARGET = "bigquery"
WRITE_TARGET = "spanner"

# GCP project-id shape — pinned EQUAL to infra.cli.PROJECT_RE by
# tests/test_composer_dag.py (inlined so this module stays stdlib-only for a
# Composer parse). A malformed project never reaches the rendered pod command.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")

# Rendered to YYYY-MM-DD by Airflow, never by us (the tasks.py contract): a
# per-interval build lands only files uploaded on or before this date.
THROUGH_TEMPLATE = "{{ data_interval_end | ds }}"

# The NON-dbt steps that run as KubernetesPodOperator pods over the serving+landing
# image. The two landings precede dbt (raw.events for the models, the Spanner dim
# seed for the federation view); the write-back follows it.
PRE_DBT_STEPS: tuple[str, ...] = ("bq_load", "spanner_load")
POST_DBT_STEPS: tuple[str, ...] = ("writeback",)
KPO_STEPS: tuple[str, ...] = PRE_DBT_STEPS + POST_DBT_STEPS

# The Composer DAG-bucket mount (`/home/airflow/gcs/dags`) holds the uploaded dbt
# project and its precompiled manifest (decision 3). Cosmos reads the project from
# here and the manifest so the SCHEDULER never runs dbt at parse.
DBT_PROJECT_DIR = "/home/airflow/gcs/dags/dbt"
DBT_MANIFEST_PATH = f"{DBT_PROJECT_DIR}/target/manifest.json"
DBT_PROFILES_YML = f"{DBT_PROJECT_DIR}/profiles.yml"
# The committed profiles.yml keys (dbt/profiles.yml): the `ontime` profile, the
# `bigquery` output — reused verbatim so macros/location/OTR_GCP_PROJECT are
# unchanged (a runner, not logic).
DBT_PROFILE_NAME = "ontime"
DBT_TARGET_NAME = "bigquery"

# Cosmos execution/load mode NAMES (the enum members the DAG imports from
# cosmos.constants). Held here as strings so the offline test pins the choice
# without importing Cosmos: VIRTUALENV (Google's documented Composer mode —
# dbt-bigquery in an isolated per-run venv, never the Composer image / uv.lock),
# DBT_MANIFEST (load from the precompiled manifest — no parse-time adapter).
EXECUTION_MODE = "VIRTUALENV"
LOAD_MODE = "DBT_MANIFEST"
# Render source nodes AS tasks so a source with freshness configured runs a
# `dbt source freshness` gate upstream of every model (ROADMAP item 7's
# "freshness first"); the models depend on it (Cosmos wires source → model).
SOURCE_RENDERING_BEHAVIOR = "ALL"
# The dbt adapter Cosmos installs in the virtualenv (never in uv.lock).
DBT_ADAPTER_REQUIREMENT = "dbt-bigquery==1.9.1"

# A PERSISTENT venv path so Cosmos installs dbt-bigquery ONCE per worker and
# reuses it across every model/test/source task, instead of building a fresh
# ~10-minute venv per task (fix/composer-cosmos-liverun: the live run showed the
# per-task rebuild is too slow/fragile on the SMALL environment — ARCHITECTURE
# §8). A worker-local writable path (not the GCSfuse `data/` mount, which is too
# slow to run a venv from); reuse is per-worker, so a handful of installs, not
# ~13. Cosmos locks the dir so concurrent tasks do not race the first build.
DBT_VENV_DIR = "/home/airflow/dbt-venv"

# The Airflow Variable holding the alert recipient (never a hardcoded address —
# no PII in the tree; failure_email.py reads it). SMTP is a Composer airflow.cfg
# override, documented, no secret here.
ALERT_EMAIL_VARIABLE = "otr_alert_email"


def _module_command(step: str, project: str) -> list[str]:
    """The committed module-CLI argv for one non-dbt step (no `make` in the pod).
    `--confirm-origin "command line"` is baked because the pod argv IS the
    reviewed command (see the module docstring)."""
    confirm = ["--confirm", "yes", "--confirm-origin", "command line"]
    if step == "bq_load":
        return [
            "python", "-m", "landing.cli", "bq-load", PROFILE,
            "--project", project, *confirm,
            "--through", THROUGH_TEMPLATE,
        ]  # fmt: skip
    if step == "spanner_load":
        return [
            "python", "-m", "landing.cli", "spanner-load", PROFILE,
            "--project", project, *confirm,
        ]  # fmt: skip
    if step == "writeback":
        return [
            "python", "-m", "serving.cli", "writeback", PROFILE,
            "--target", WRITE_TARGET, "--project", project, *confirm,
        ]  # fmt: skip
    raise ValueError(f"unrecognised KPO step: {step!r} (expected one of {KPO_STEPS})")


def build_kpo_command(step: str, project: str) -> list[str]:
    """The pod's container command (`cmds`) for a non-dbt step — an ALLOWLIST over
    the step name AND a validated project (Boundary contract): an unrecognised
    step or a project that is not a valid GCP id REFUSES (raises), so no
    unvalidated value is ever interpolated into a rendered pod command. Each
    command is the committed module CLI, which re-validates the project itself."""
    if step not in KPO_STEPS:
        raise ValueError(
            f"unrecognised KPO step: {step!r} (expected one of {KPO_STEPS})"
        )
    if not PROJECT_RE.match(project):
        raise ValueError(f"OTR_GCP_PROJECT is not a valid GCP project id: {project!r}")
    return _module_command(step, project)
