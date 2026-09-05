"""The cloud runtime DAG (fix/composer-cosmos, ROADMAP item 7): the batch path as
a real managed-Airflow DAG that EXECUTES on a Cloud Composer worker — superseding
Phase 12's make-shelling Option A, which could only parse there.

Shape (edges): the two landings run as KubernetesPodOperator pods over the
serving+landing image, upstream of the dbt work; Cosmos (`DbtTaskGroup`) renders
every dbt model as its own task with a `dbt source freshness` gate at its head;
the Spanner write-back is a final pod.

    bq_load ┐
            ├─▶ dbt (source freshness ▶ staging ▶ … ▶ scores) ─▶ writeback
    spanner_load ┘

The three non-dbt steps run the committed module CLIs inside the image (no `make`
on the worker — the toolchain the Option-A DAG lacked); dbt runs UNCHANGED via
Cosmos (`ExecutionMode.VIRTUALENV`, `LoadMode.DBT_MANIFEST`, the committed
profiles.yml) — a new runner, not new logic, so every golden holds.

This module imports cosmos / airflow / the k8s provider, so it loads only where
they are installed (Composer, the Docker rehearsal). The offline suite loads it
under STUBS (`tests/test_composer_dag.py`, the Phase 8b/12 pattern) — those
packages are NEVER in the venv / uv.lock. The DAG target project comes from the
`OTR_DAG_PROJECT` env (Composer `software_config.env_variables`), validated at
render by `build_kpo_command`.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from cosmos import (
    DbtTaskGroup,
    ExecutionConfig,
    ProfileConfig,
    ProjectConfig,
    RenderConfig,
)
from cosmos.constants import ExecutionMode, LoadMode, SourceRenderingBehavior

# Dual-path import so the module loads in both layouts (the pipeline_dag.py
# pattern): the package path resolves in the Docker rehearsal (`orchestration` on
# sys.path); the flat `import composer_tasks` resolves in the Composer DAG bucket,
# where only `dags/` is on sys.path (ModuleNotFoundError ⊂ ImportError).
try:
    from orchestration import composer_tasks as ct
    from orchestration.failure_email import pipeline_failure_email
except ImportError:  # flat Composer dags/ bucket — the files side by side
    import composer_tasks as ct  # type: ignore[no-redef]
    from failure_email import pipeline_failure_email  # type: ignore[no-redef]

# The Composer-3 KPO contract (docs/DEPLOYMENT.md): the fixed user-workloads
# namespace and the in-cluster kube config the environment writes for its workers.
KPO_NAMESPACE = "composer-user-workloads"
KPO_KUBE_CONFIG = "/home/airflow/composer_kube_config"

# The target project reaches the DAG only through the environment (set by
# Terraform's software_config.env_variables); validated at render by
# build_kpo_command. Empty offline — the stub test passes a valid one.
PROJECT = os.environ.get("OTR_DAG_PROJECT", "")

# The serving+landing image the pods run (Artifact Registry). The tag is the
# environment's — Terraform sets OTR_SERVING_IMAGE; the DAG never invents a
# registry path.
SERVING_IMAGE = os.environ.get("OTR_SERVING_IMAGE", "")


def _pod(step: str) -> KubernetesPodOperator:
    """A KubernetesPodOperator running one non-dbt step's module CLI in the
    serving+landing image. No credential: the pod authenticates by the
    environment's Workload-Identity SA (Composer-3 pods inherit it) — no
    GOOGLE_APPLICATION_CREDENTIALS, no keyfile, no mounted secret."""
    return KubernetesPodOperator(
        task_id=step,
        name=step.replace("_", "-"),
        namespace=KPO_NAMESPACE,
        image=SERVING_IMAGE,
        cmds=ct.build_kpo_command(step, PROJECT),
        config_file=KPO_KUBE_CONFIG,
        kubernetes_conn_id="kubernetes_default",
    )


with DAG(
    dag_id="ontime_cloud",
    schedule="@daily",
    start_date=datetime(2026, 1, 6),
    catchup=False,  # backfill is explicit; never auto-catch-up-to-now
    is_paused_upon_creation=True,
    max_active_runs=1,
    # Serialize task execution so exactly ONE task builds the shared Cosmos venv
    # (virtualenv_dir) and the rest reuse it — concurrent first-builds race the
    # venv dir (Cosmos lock does not serialize them; fix/composer-cosmos-liverun,
    # §8). With reuse each task after the first ~6-min build runs in ~1.5 min.
    max_active_tasks=1,
    default_args={"retries": 0, "on_failure_callback": pipeline_failure_email},
    tags=["ontime", "cosmos", "composer"],
) as dag:
    landings = [_pod(step) for step in ct.PRE_DBT_STEPS]

    # dbt as one task per model (ExecutionMode.VIRTUALENV — dbt-bigquery in an
    # isolated per-run venv; LoadMode.DBT_MANIFEST — load from the precompiled
    # manifest so the scheduler runs no dbt at parse), reusing the committed
    # project + profiles.yml. SourceRenderingBehavior.ALL renders the source
    # freshness gate at the head — a stale source blocks every model.
    dbt = DbtTaskGroup(
        group_id="dbt",
        project_config=ProjectConfig(
            dbt_project_path=ct.DBT_PROJECT_DIR,
            manifest_path=ct.DBT_MANIFEST_PATH,
        ),
        profile_config=ProfileConfig(
            profile_name=ct.DBT_PROFILE_NAME,
            target_name=ct.DBT_TARGET_NAME,
            profiles_yml_filepath=ct.DBT_PROFILES_YML,
        ),
        execution_config=ExecutionConfig(
            execution_mode=ExecutionMode.VIRTUALENV,
            # Persist + reuse the venv across tasks — install dbt-bigquery once
            # per worker, not per task (fix/composer-cosmos-liverun, §8).
            virtualenv_dir=Path(ct.DBT_VENV_DIR),
        ),
        render_config=RenderConfig(
            load_method=LoadMode.DBT_MANIFEST,
            source_rendering_behavior=SourceRenderingBehavior.ALL,
        ),
        operator_args={
            "py_requirements": [ct.DBT_ADAPTER_REQUIREMENT],
            "install_deps": True,
        },
    )

    writeback = _pod("writeback")

    for landing in landings:
        landing >> dbt
    dbt >> writeback
