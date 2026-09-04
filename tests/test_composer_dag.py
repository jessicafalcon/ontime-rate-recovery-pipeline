"""fix/composer-cosmos (specs/fix-composer-cosmos-runtime.md): the cloud runtime
DAG's SHAPE, asserted offline under stubbed cosmos / airflow / k8s — those
packages are Composer-only, never in the venv / uv.lock (invariant 5). The Phase
8b/12 pattern: exec the DAG module with stub modules that record their kwargs and
`>>` edges, then assert the real construction — the KPO steps run the serving
image with no credential (invariant 1), Cosmos gets the UNCHANGED project +
profiles.yml (invariant 2), source freshness is enabled upstream of the models
and its verdict is never a pin (invariant 3), and `build_kpo_command` is an
allowlist (Boundary contract). Whether Cosmos actually renders one task per model
and the pods run is 7b's LIVE proof — 7a pins the shape."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from orchestration import composer_tasks as ct

ROOT = Path(__file__).resolve().parent.parent
DAG_FILE = ROOT / "orchestration" / "dags" / "composer_dag.py"

PROJECT = "ontime-demo-proj"
IMAGE = "us-central1-docker.pkg.dev/ontime-demo-proj/ontime/serving:deadbeef"


class _Node:
    """A task/group stand-in recording kwargs and `>>` edges (both KPO and the
    Cosmos group need `>>`)."""

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.task_id = kw.get("task_id") or kw.get("group_id")
        self.downstream: list[_Node] = []
        self.upstream: list[_Node] = []

    def __rshift__(self, other: _Node) -> _Node:
        self.downstream.append(other)
        other.upstream.append(self)
        return other


class _KPO(_Node):
    instances: list[_KPO] = []

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        _KPO.instances.append(self)


class _Group(_Node):
    instances: list[_Group] = []

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        _Group.instances.append(self)


class _DAG:
    instances: list[_DAG] = []

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        _DAG.instances.append(self)

    def __enter__(self) -> _DAG:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _EnumMember:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self.name}>"


def _config_class(record: dict[str, Any], key: str) -> type:
    """A Cosmos ProjectConfig/ProfileConfig/… stand-in that stores its kwargs
    under `record[key]` (last instance wins — one each in the DAG)."""

    class _Config:
        def __init__(self, **kw: Any) -> None:
            record[key] = kw

    return _Config


def _stub_modules() -> tuple[dict[str, types.ModuleType], dict[str, Any]]:
    """Build the airflow + k8s-provider + cosmos stub modules and the record dict
    the config classes write into. Only the names the DAG imports exist, so any
    other import fails the load (a positive pin on 'no logic beyond wiring')."""
    record: dict[str, Any] = {}

    airflow = types.ModuleType("airflow")
    airflow.DAG = _DAG  # type: ignore[attr-defined]

    # airflow.providers.cncf.kubernetes.operators.pod.KubernetesPodOperator
    providers = types.ModuleType("airflow.providers")
    cncf = types.ModuleType("airflow.providers.cncf")
    k8s = types.ModuleType("airflow.providers.cncf.kubernetes")
    ops = types.ModuleType("airflow.providers.cncf.kubernetes.operators")
    pod = types.ModuleType("airflow.providers.cncf.kubernetes.operators.pod")
    pod.KubernetesPodOperator = _KPO  # type: ignore[attr-defined]

    # cosmos
    cosmos = types.ModuleType("cosmos")
    cosmos.DbtTaskGroup = _Group  # type: ignore[attr-defined]
    cosmos.ProjectConfig = _config_class(record, "project")  # type: ignore[attr-defined]
    cosmos.ProfileConfig = _config_class(record, "profile")  # type: ignore[attr-defined]
    cosmos.ExecutionConfig = _config_class(record, "execution")  # type: ignore[attr-defined]
    cosmos.RenderConfig = _config_class(record, "render")  # type: ignore[attr-defined]
    constants = types.ModuleType("cosmos.constants")
    constants.ExecutionMode = types.SimpleNamespace(  # type: ignore[attr-defined]
        VIRTUALENV=_EnumMember("VIRTUALENV")
    )
    constants.LoadMode = types.SimpleNamespace(  # type: ignore[attr-defined]
        DBT_MANIFEST=_EnumMember("DBT_MANIFEST")
    )
    constants.SourceRenderingBehavior = types.SimpleNamespace(  # type: ignore[attr-defined]
        ALL=_EnumMember("ALL")
    )

    mods = {
        "airflow": airflow,
        "airflow.providers": providers,
        "airflow.providers.cncf": cncf,
        "airflow.providers.cncf.kubernetes": k8s,
        "airflow.providers.cncf.kubernetes.operators": ops,
        "airflow.providers.cncf.kubernetes.operators.pod": pod,
        "cosmos": cosmos,
        "cosmos.constants": constants,
    }
    return mods, record


def _load_dag(monkeypatch: Any) -> tuple[_DAG, dict[str, Any]]:
    """Exec composer_dag.py with the stubs + the env the module reads at import;
    return the DAG stub and the Cosmos-config record."""
    monkeypatch.setenv("OTR_DAG_PROJECT", PROJECT)
    monkeypatch.setenv("OTR_SERVING_IMAGE", IMAGE)
    mods, record = _stub_modules()
    _KPO.instances = []
    _Group.instances = []
    _DAG.instances = []
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(
            "_composer_dag_undertest", DAG_FILE
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.dag, record
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


def test_dag_shape_loads_under_stubs(monkeypatch: Any) -> None:
    """Invariant 5: the DAG loads with cosmos/airflow/k8s STUBBED, giving the safe
    config, three KPO steps + one Cosmos group, and the wiring
    bq_load/spanner_load → dbt → writeback. A live cosmos/airflow import in the
    offline path would fail here."""
    dag, _ = _load_dag(monkeypatch)
    assert dag.kw["dag_id"] == "ontime_cloud"
    assert dag.kw["schedule"] == "@daily"
    assert dag.kw["catchup"] is False
    assert dag.kw["max_active_runs"] == 1
    assert dag.kw["default_args"]["retries"] == 0

    assert [op.task_id for op in _KPO.instances] == list(ct.KPO_STEPS)
    assert len(_Group.instances) == 1
    group = _Group.instances[0]
    by_id = {op.task_id: op for op in _KPO.instances}
    # both landings feed the dbt group; the group feeds the write-back
    for step in ct.PRE_DBT_STEPS:
        assert group in by_id[step].downstream
    assert by_id["writeback"] in group.downstream
    assert by_id["bq_load"].upstream == []  # a source
    assert by_id["writeback"].downstream == []  # the sink


def test_on_failure_callback_is_wired(monkeypatch: Any) -> None:
    """Invariant 3: the DAG's default_args carry the email callback, so any failed
    task fires it."""
    from orchestration.failure_email import pipeline_failure_email

    dag, _ = _load_dag(monkeypatch)
    assert dag.kw["default_args"]["on_failure_callback"] is pipeline_failure_email


def test_kpo_steps_run_the_serving_image(monkeypatch: Any) -> None:
    """Invariant 1: each non-dbt step is a KubernetesPodOperator over the serving
    image with the fixed Composer-3 namespace + config_file, the committed module
    CLI as its command, and NO credential in the pod spec (no
    GOOGLE_APPLICATION_CREDENTIALS env, no mounted secret / volume)."""
    _load_dag(monkeypatch)
    assert {op.task_id for op in _KPO.instances} == set(ct.KPO_STEPS)
    for op in _KPO.instances:
        assert op.kw["image"] == IMAGE
        assert op.kw["namespace"] == "composer-user-workloads"
        assert op.kw["config_file"] == "/home/airflow/composer_kube_config"
        assert op.kw["cmds"] == ct.build_kpo_command(op.task_id, PROJECT)
        # no credential anywhere in the pod spec
        env_vars = op.kw.get("env_vars") or {}
        keys = env_vars.keys() if isinstance(env_vars, dict) else set()
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in keys
        assert "secrets" not in op.kw and "volumes" not in op.kw
        assert "volume_mounts" not in op.kw


def test_cosmos_group_renders_the_unchanged_project(monkeypatch: Any) -> None:
    """Invariant 2: the builder hands Cosmos the committed dbt project + the
    committed profiles.yml bigquery target, VIRTUALENV + DBT_MANIFEST — a runner,
    not new logic. (The one-task-per-model render is 7b's live proof.)"""
    _, record = _load_dag(monkeypatch)
    assert record["project"]["dbt_project_path"] == ct.DBT_PROJECT_DIR
    assert record["project"]["manifest_path"] == ct.DBT_MANIFEST_PATH
    assert record["profile"]["profiles_yml_filepath"] == ct.DBT_PROFILES_YML
    assert record["profile"]["profile_name"] == ct.DBT_PROFILE_NAME
    assert record["profile"]["target_name"] == ct.DBT_TARGET_NAME
    assert record["execution"]["execution_mode"].name == ct.EXECUTION_MODE
    # the venv is persisted + reused across tasks (fix/composer-cosmos-liverun):
    # install dbt-bigquery once per worker, not a fresh ~10-min venv per task.
    assert str(record["execution"]["virtualenv_dir"]) == ct.DBT_VENV_DIR
    assert record["render"]["load_method"].name == ct.LOAD_MODE


def test_freshness_is_upstream_of_models(monkeypatch: Any) -> None:
    """Invariant 3: source rendering is ALL, so the source-freshness gate renders
    at the head of the dbt group (Cosmos wires source → model), and the whole dbt
    group sits downstream of the landings — 'freshness first'."""
    _, record = _load_dag(monkeypatch)
    assert (
        record["render"]["source_rendering_behavior"].name
        == ct.SOURCE_RENDERING_BEHAVIOR
    )
    group = _Group.instances[0]
    assert [u.task_id for u in group.upstream] == list(ct.PRE_DBT_STEPS)


def test_freshness_verdict_is_never_a_pin() -> None:
    """Invariant 3: source freshness reads the wall clock — a determinism
    carve-out — so no pin and no model reads a freshness result (it is never
    materialised into the data path)."""
    assert "freshness" not in (ROOT / "tests" / "pins.py").read_text().lower()
    for sql in (ROOT / "dbt" / "models").rglob("*.sql"):
        assert "freshness" not in sql.read_text().lower(), sql


def test_kpo_command_refuses_bad_project() -> None:
    """Boundary contract: build_kpo_command is an ALLOWLIST — an unknown step or a
    malformed project REFUSES, so no unvalidated value reaches a rendered pod
    command; the recognised steps still render."""
    with pytest.raises(ValueError):
        ct.build_kpo_command("nope", PROJECT)  # unknown step
    with pytest.raises(ValueError):
        ct.build_kpo_command("bq_load", "bad'; rm -rf /")  # not a project id
    with pytest.raises(ValueError):
        ct.build_kpo_command("writeback", "")  # empty project
    for step in ct.KPO_STEPS:
        assert ct.build_kpo_command(step, PROJECT)[0] == "python"


def test_project_re_matches_the_infra_source_of_truth() -> None:
    """composer_tasks.py inlines the project-id shape (it cannot import infra.cli
    in a flat Composer bucket), so pin the copy EQUAL to the source of truth."""
    from infra.cli import PROJECT_RE as INFRA_PROJECT_RE

    assert ct.PROJECT_RE.pattern == INFRA_PROJECT_RE.pattern


def test_kpo_command_bakes_command_line_confirm() -> None:
    """The pod runs the committed module CLI with CONFIRM baked as command-line
    origin (the pod argv IS the reviewed command; no `make`/shell there). Each
    cloud step carries --confirm yes and the validated project single-token."""
    for step in ct.KPO_STEPS:
        cmd = ct.build_kpo_command(step, PROJECT)
        assert cmd[:3] == ["python", "-m"] or cmd[:2] == ["python", "-m"]
        assert "--confirm" in cmd and cmd[cmd.index("--confirm") + 1] == "yes"
        oi = cmd.index("--confirm-origin")
        assert cmd[oi + 1] == "command line"
        assert cmd[cmd.index("--project") + 1] == PROJECT
    # the write-back targets spanner; bq_load carries the THROUGH template
    assert "spanner" in ct.build_kpo_command("writeback", PROJECT)
    assert ct.THROUGH_TEMPLATE in ct.build_kpo_command("bq_load", PROJECT)
