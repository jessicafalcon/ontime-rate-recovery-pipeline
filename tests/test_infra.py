"""Phase 9a — the Terraform tree and infra/cli.py, pinned offline. The `.tf`
files are configuration no mutation operator addresses; these static checks +
`terraform validate` (DONE command) + the manual plan/apply/destroy Evidence are
their pins (the Phase 7 treatment of SQL). No terraform binary here, no network,
no cloud — pure file reads and Python.

Review round 2 re-implemented these against ONE invariant (cap invoked): every
property named in the Invariants table has a test that reddens when the property
is removed from the `.tf` by a hand-mutation. Pins are exact-string / scoped /
allowlist — never a substring or a resource-type denylist. Round 6 (Amendment
P, closed by round 7's R) made the whole tree ONE allowlist: `infra/MANIFEST.sha256`
pins every `.tf`/`.tf.json` and the provider lock byte-for-byte, so any
hand-mutation of any attribute is red until `make tf-freeze CONFIRM=yes` rewrites
it — the manifest catches EVERY edit; the property pins below say WHICH
properties a re-freeze must preserve (they stay red after a freeze that dropped
one). `infra/cli.py`'s
guards carry the mutation lines; `cli.tf` runs through a fake runner so no test
spawns terraform, and the argv it builds is asserted."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from infra import cli

ROOT = Path(__file__).parent.parent
INFRA = ROOT / "infra"
GATED_MODULE_DIRS = {INFRA / "modules" / "composer", INFRA / "modules" / "spanner"}


def _tf_files() -> list[Path]:
    """Every file Terraform loads — `.tf` and `.tf.json` (Amendment R) — pruning
    `.terraform/` (the gitignored provider / vendored-module cache `tf-validate`
    creates), the `BUILD_DIRS` precedent."""
    return sorted(
        p
        for p in INFRA.rglob("*")
        if p.is_file()
        and p.name.endswith(cli.PINNED_SUFFIXES)
        and ".terraform" not in p.parts
    )


def _strip_hcl_comments(text: str) -> str:
    """Drop `#`/`//` line comments and `/* */` blocks — a comment mentioning a
    property is not that property."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(#|//).*$", "", line) for line in text.splitlines())


def _stripped_files() -> dict[Path, str]:
    return {f: _strip_hcl_comments(f.read_text()) for f in _tf_files()}


def _brace_body(text: str, open_at: int) -> tuple[str, int]:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : i], i + 1
    raise AssertionError(f"unbalanced braces from {open_at}")


def _blocks(text: str, header_re: str) -> list[str]:
    out = []
    for m in re.finditer(header_re + r"\s*\{", text):
        body, _ = _brace_body(text, m.end() - 1)
        out.append(body)
    return out


def _blocks_with_headers(text: str, header_re: str) -> list[str]:
    """Header + body of every matching block — the header's labels
    (`resource "google_storage_bucket" "tfstate"`) are part of the block."""
    out = []
    for m in re.finditer(header_re + r"\s*\{", text):
        body, _ = _brace_body(text, m.end() - 1)
        out.append(m.group(0) + body)
    return out


def _block(text: str, header_re: str) -> str:
    blocks = _blocks(text, header_re)
    assert blocks, header_re
    return blocks[0]


def _has_arg(body: str, name: str) -> bool:
    """A top-of-line `name = …` HCL argument assignment (not the word in prose)."""
    return bool(re.search(rf"^\s*{name}\s*=", body, re.M))


def _read(*parts: str) -> str:
    return (INFRA.joinpath(*parts)).read_text()


def _stripped(*parts: str) -> str:
    return _strip_hcl_comments(_read(*parts))


# ---------------------------------------------------------------- invariant 1


def _variable_blocks(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'variable "([^"]+)"\s*\{', text):
        body, _ = _brace_body(text, m.end() - 1)
        out.append((m.group(1), body))
    return out


def test_project_id_is_the_only_required_var() -> None:
    """A variable without a `default =` assignment is required input; from a fresh
    clone only `project_id` may be one. Brace-matched (a nested `validation {}`
    can't hide a required var); keyed on the assignment (a description with the
    word "default" doesn't read as one)."""
    blocks = _variable_blocks(_read("variables.tf"))
    assert {n for n, _ in blocks} >= {"project_id", "enable_composer", "github_ref"}
    required = [name for name, body in blocks if not _has_arg(body, "default")]
    assert required == ["project_id"], required


MANAGED_BLOCK_RE = r"(?:resource|data|module|variable|output|locals)\b[^{]*"


def test_no_staging_bucket_variable_and_the_managed_bucket_is_derived() -> None:
    """Round 2 #1: the managed bucket is not caller-configurable, so it can never
    be pointed at the bootstrap `<project>-tfstate` state bucket. It is derived
    `${project_id}-ontime`, and no MANAGED resource references the state bucket
    (the only `tfstate` mentions are comments, stripped)."""
    assert not _blocks(_read("variables.tf"), r'variable "staging_bucket"')
    assert re.search(
        r'staging_bucket\s*=\s*"\$\{var\.project_id\}-ontime"', _read("main.tf")
    )
    # Scoped to MANAGED blocks: the `terraform {}` block (where the documented
    # `backend "gcs"` names the bootstrap bucket) is not a managed resource, so
    # uncommenting it does not redden (round 3 #7). Header labels count (round 4
    # #4): `resource "google_storage_bucket" "tfstate"` is a managed block too.
    for f, body in _stripped_files().items():
        for block in _blocks_with_headers(body, MANAGED_BLOCK_RE):
            assert "tfstate" not in block, f


def _validation_conditions(var_body: str) -> list[str]:
    return [
        re.search(r"condition\s*=\s*(.+)", b).group(1).strip()
        for b in _blocks(var_body, r"validation")
    ]


def test_project_id_validation_mirrors_the_cli_regex() -> None:
    """Round 3 #13: a tfvars / direct `terraform apply` bypasses infra/cli.py, so
    the HCL carries the same shape check — pinned equal to `cli.PROJECT_RE`
    (`\\Z` ↔ `$`; HCL has no trailing newline to reject)."""
    conds = _validation_conditions(
        _block(_read("variables.tf"), r'variable "project_id"')
    )
    assert len(conds) == 1, conds
    hcl = re.search(r'regex\("([^"]+)"', conds[0])
    assert hcl and hcl.group(1) == cli.PROJECT_RE.pattern.replace(r"\Z", "$"), conds


def test_input_shape_validations_exist() -> None:
    """Round 3 #3: the `validation {}` blocks Amendment F added (CEL-injection
    shape checks on the two WIF vars, positive thresholds) and #13's project_id
    exist as assignments — deleting any one reddens."""
    text = _read("variables.tf")
    expect = {
        "project_id": "regex(",
        "github_repository": 'regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"',
        "github_ref": 'regex("^refs/(heads|tags)/',
        "budget_alert_thresholds": "alltrue(",
        "operator_principal": 'regex("^(user|serviceAccount):',
        # Phase 10 round 1 #9: interpolated into the spanner view's SQL literal
        "region": 'regex("^[a-z]+-[a-z]+[0-9]{1,2}$"',
    }
    for var, needle in expect.items():
        conds = _validation_conditions(_block(text, rf'variable "{var}"'))
        assert conds and any(needle in c for c in conds), (var, conds)


# ---------------------------------------------------------------- invariant 2


# Every resource type the tree is allowed to declare OUTSIDE the count-gated
# composer/spanner modules. An allowlist, not a denylist: any new type — billable
# or not — must be added here consciously, so a `google_spanner_instance` (or any
# other cost) dropped at root is caught exactly.
ALLOWED_RESOURCE_TYPES = {
    "google_project_service",
    "google_bigquery_dataset",
    "google_storage_bucket",
    "google_service_account",
    "google_project_iam_member",
    "google_bigquery_dataset_iam_member",
    "google_storage_bucket_iam_member",
    "google_iam_workload_identity_pool",
    "google_iam_workload_identity_pool_provider",
    "google_service_account_iam_member",
    "google_billing_budget",
}


# The count-gated modules are NOT exempt (Phase 10 round 1 #11): each has its
# own exact allowlist, so a `null_resource` + local-exec (runs on the
# operator's machine during the ask-first apply) or a second billable type
# dropped into a module is caught the same way. composer is still a stub.
GATED_ALLOWED_RESOURCE_TYPES = {
    INFRA / "modules" / "composer": set(),
    INFRA / "modules" / "spanner": {
        "google_project_service",
        "google_project_iam_custom_role",
        "google_spanner_instance",
        "google_spanner_database",
        "google_bigquery_connection",
        "google_spanner_database_iam_member",
        "google_bigquery_connection_iam_member",
        "google_bigquery_table",
    },
}
assert set(GATED_ALLOWED_RESOURCE_TYPES) == GATED_MODULE_DIRS


# Every `data` source type the tree may read (round 4 #7): a data source is a
# live API call at plan/apply, so a new one is a conscious addition too. The
# gated modules read none — the root passes them what they need (#11).
ALLOWED_DATA_SOURCE_TYPES = {"google_project", "google_billing_account"}


def _declared_types(f: Path, kind: str) -> set[str]:
    return set(
        re.findall(
            rf'\b{kind}\s+"([a-z0-9_]+)"\s+"', _strip_hcl_comments(f.read_text())
        )
    )


def test_every_data_source_type_is_on_the_allowlist() -> None:
    declared: set[str] = set()
    for f in _tf_files():
        if f.parent in GATED_MODULE_DIRS:
            assert _declared_types(f, "data") == set(), f
            continue
        declared |= _declared_types(f, "data")
    assert declared == ALLOWED_DATA_SOURCE_TYPES, declared ^ ALLOWED_DATA_SOURCE_TYPES


def test_enable_toggles_default_false() -> None:
    text = _read("variables.tf")
    for toggle in ("enable_composer", "enable_spanner"):
        assert re.search(
            r"^\s*default\s*=\s*false", _block(text, rf'variable "{toggle}"'), re.M
        )


def test_optional_modules_are_count_gated() -> None:
    text = _read("main.tf")
    for mod in ("composer", "spanner"):
        assert re.search(
            rf"count\s*=\s*var\.enable_{mod} \? 1 : 0", _block(text, rf'module "{mod}"')
        ), mod
    for mod in ("bigquery", "gcs", "iam", "budget"):
        assert not re.search(
            r"^\s*count\s*=", _block(text, rf'module "{mod}"'), re.M
        ), mod


def test_spanner_module_is_count_gated_and_default_off() -> None:
    """Phase 10 (spec invariant 5): every Spanner-family resource sits inside
    the count-gated module (test_optional_modules_are_count_gated pins the gate,
    test_enable_toggles_default_false the default), the database carries
    deletion_protection = false (the toggle-flip re-apply is the sanctioned,
    CONFIRM-gated destroy path), the instance is the smallest size, and the two
    APIs are kept on at destroy like the root set."""
    module_dir = INFRA / "modules" / "spanner"
    for f in _tf_files():
        if f.parent == module_dir:
            continue
        text = _strip_hcl_comments(f.read_text())
        assert not re.search(r'resource\s+"google_spanner_', text), f
        assert not re.search(r'resource\s+"google_bigquery_connection', text), f
    text = _stripped("modules", "spanner", "main.tf")
    db = _block(text, r'resource "google_spanner_database" "this"')
    assert re.search(r"^\s*deletion_protection\s*=\s*false", db, re.M)
    inst = _block(text, r'resource "google_spanner_instance" "this"')
    assert re.search(r"^\s*processing_units\s*=\s*100", inst, re.M)
    api = _block(text, r'resource "google_project_service" "spanner"')
    assert re.search(r"^\s*disable_on_destroy\s*=\s*false", api, re.M)


SPANNER_INSTANCE_NAME_RE = r'^\s*name\s*=\s*"([a-z0-9-]+)"'


def test_spanner_names_pin_the_python_constants() -> None:
    """Round 1 #14: the instance/database names and the models dataset the
    Python clients open are literals in loader/spanner.py + serving/spanner.py;
    they are pinned to the `.tf` here so an infra rename reddens offline."""
    from loader import spanner as dims
    from serving import spanner as wb_sp

    text = _stripped("modules", "spanner", "main.tf")
    inst = _block(text, r'resource "google_spanner_instance" "this"')
    db = _block(text, r'resource "google_spanner_database" "this"')
    assert re.search(SPANNER_INSTANCE_NAME_RE, inst, re.M).group(1) == dims.INSTANCE
    assert re.search(SPANNER_INSTANCE_NAME_RE, db, re.M).group(1) == dims.DATABASE
    assert (wb_sp.INSTANCE, wb_sp.DATABASE) == (dims.INSTANCE, dims.DATABASE)
    models = _block(_read("variables.tf"), r'variable "models_dataset"')
    assert (
        re.search(r'default\s*=\s*"([a-z_]+)"', models).group(1) == wb_sp.MODELS_DATASET
    )
    assert (
        re.search(r'connection_id\s*=\s*"([a-z_]+)"', text).group(1) == "spanner_dims"
    )
    view = _block(text, r'resource "google_bigquery_table" "dim_user_spanner"')
    assert re.search(r'table_id\s*=\s*"dim_user_spanner"', view)


# Phase 10 round 1 #10: the member AND the scope of each grant — the resource
# type (database- / connection-level, never instance or project) and the
# argument that names the ONE database / connection.
SPANNER_GRANT_SCOPES = {
    '"pipeline_user"': (
        "google_spanner_database_iam_member",
        r"^\s*database\s*=\s*google_spanner_database\.this\.name",
    ),
    '"pipeline_connection_user"': (
        "google_bigquery_connection_iam_member",
        r"^\s*connection_id\s*=\s*google_bigquery_connection\.spanner_dims\.connection_id",
    ),
}


# The custom role's permission set — exact (round 2 #1, Amendment E): the
# data plane the write-back and the landing use, nothing that changes schema,
# IAM, instances or operations. A new permission is a conscious edit here.
SPANNER_DATA_PERMISSIONS = {
    "spanner.databases.beginOrRollbackReadWriteTransaction",
    "spanner.databases.beginReadOnlyTransaction",
    "spanner.databases.get",
    "spanner.databases.read",
    "spanner.databases.select",
    "spanner.databases.write",
    "spanner.instances.get",
    "spanner.sessions.create",
    "spanner.sessions.delete",
    "spanner.sessions.get",
    "spanner.sessions.list",
}
# Anything matching this is control-plane and must never appear in the role.
CONTROL_PLANE_RE = re.compile(
    r"(Ddl|updateDdl|\.create$|\.delete$|\.drop|IamPolicy|Operations|"
    r"backups|instances\.(update|create|delete)|databases\.(create|drop|update))"
)


def test_spanner_custom_role_is_the_exact_data_plane_set() -> None:
    """Amendment E: the pipeline SA's database grant is a custom role whose
    permissions are EXACTLY the data-plane set — no `updateDdl` (every
    predefined writing role carries it), no IAM, no operations. The role is
    in the count-gated module (its API must be enabled for the permissions to
    be valid), and no predefined Spanner role is granted anywhere."""
    text = _stripped("modules", "spanner", "main.tf")
    role = _block(text, r'resource "google_project_iam_custom_role" "data_user"')
    assert re.search(r'^\s*role_id\s*=\s*"ontimeSpannerDataUser"', role, re.M)
    assert re.search(r"^\s*project\s*=\s*var\.project_id", role, re.M)
    listed = re.search(r"permissions\s*=\s*\[([^\]]*)\]", role).group(1)
    perms = set(re.findall(r'"([a-zA-Z.]+)"', listed))
    assert perms == SPANNER_DATA_PERMISSIONS, perms ^ SPANNER_DATA_PERMISSIONS
    for perm in perms:
        assert not CONTROL_PLANE_RE.search(perm) or perm.startswith(
            "spanner.sessions."
        ), perm
    assert "google_project_service.spanner" in re.search(
        r"depends_on\s*=\s*\[([^\]]*)\]", role
    ).group(1)
    for f, body in _stripped_files().items():
        assert "roles/spanner." not in body, f  # no predefined Spanner role anywhere
        if f.parent != INFRA / "modules" / "spanner":
            assert 'resource "google_project_iam_custom_role"' not in body, f


def test_region_is_validated_wherever_it_is_declared() -> None:
    """Round 2 #6: every `variables.tf` that declares `region` carries the SAME
    validation regex as the root — the check sits at each interpolation
    site's own module, and a new module cannot declare `region` unvalidated."""
    root = _validation_conditions(_block(_read("variables.tf"), r'variable "region"'))
    assert len(root) == 1
    regex = re.search(r'regex\("([^"]+)"', root[0]).group(1)
    assert regex == "^[a-z]+-[a-z]+[0-9]{1,2}$"
    declared = 0
    for f in _tf_files():
        body = _strip_hcl_comments(f.read_text())
        if not re.search(r'variable "region"', body):
            continue
        declared += 1
        conds = _validation_conditions(_block(body, r'variable "region"'))
        assert conds and any(f'regex("{regex}"' in c for c in conds), f
    assert declared == 5, declared  # root + bigquery, gcs, composer, spanner


def test_spanner_grants_are_scoped_to_the_one_database_and_connection() -> None:
    """Switching `pipeline_user` to an instance-wide
    `google_spanner_instance_iam_member` (or any grant to a project-level one)
    reddens: the type and the scoping argument are pinned per grant, and the
    module declares no other grant type."""
    text = _stripped("modules", "spanner", "main.tf")
    grants = _blocks_with_headers(
        text, r'resource "google_[a-z_]+_iam_member"\s+"[^"]+"'
    )
    # TWO grants, both to the pipeline SA — the federated read runs as the
    # querying principal; there is no service-agent identity on the Spanner
    # path (Amendment D, found live on the first apply)
    assert len(grants) == 2, [g.splitlines()[0] for g in grants]
    for g in grants:
        header = g.splitlines()[0]
        key = next(k for k in SPANNER_GRANT_SCOPES if k in header)
        rtype, scope = SPANNER_GRANT_SCOPES[key]
        assert header.startswith(f'resource "{rtype}"'), header
        assert re.search(scope, g, re.M), header
        assert re.search(
            r"^\s*instance\s*=\s*google_spanner_instance\.this\.name", g, re.M
        ) or (rtype == "google_bigquery_connection_iam_member"), header
    assert not re.search(r'resource\s+"google_spanner_instance_iam', text)
    assert not re.search(
        r'resource\s+"google_project_iam_(member|binding|policy)', text
    )
    assert not re.search(
        r'resource\s+"google_bigquery_connection_iam_(policy|binding)', text
    )
    assert not re.search(
        r'resource\s+"google_spanner_database_iam_(policy|binding)', text
    )

    # #13: the connection is ordered after its API enablement; no grant names
    # the connection service agent (Amendment D).
    def depends_on(block: str) -> str:
        return re.search(r"depends_on\s*=\s*\[([^\]]*)\]", block).group(1)

    conn = _block(text, r'resource "google_bigquery_connection" "spanner_dims"')
    assert "google_project_service.spanner" in depends_on(conn)
    assert "gcp-sa-bigqueryconnection" not in text
    assert "project_number" not in _stripped("modules", "spanner", "variables.tf")


def test_every_declared_resource_type_is_on_the_allowlist() -> None:
    """Invariant 2's teeth as an allowlist: no resource type outside the
    count-gated modules but the expected set — a `google_spanner_instance` /
    `google_cloud_run_v2_service` / `google_dataflow_job` at root is caught."""
    declared: set[str] = set()
    gated: dict[Path, set[str]] = {d: set() for d in GATED_MODULE_DIRS}
    for f in _tf_files():
        # ANY provider's resource type (round 3 #6): a `null_resource` running a
        # local-exec, or a `random_*`, is off-allowlist too.
        types = _declared_types(f, "resource")
        if f.parent in GATED_MODULE_DIRS:
            gated[f.parent] |= types
        else:
            declared |= types
    assert declared, "no resources declared"
    assert declared <= ALLOWED_RESOURCE_TYPES, declared - ALLOWED_RESOURCE_TYPES
    for d, types in gated.items():  # exact per module (#11): nothing extra
        assert types == GATED_ALLOWED_RESOURCE_TYPES[d], (d.name, types)


def test_module_sources_are_local_paths_only() -> None:
    """Round 8 #4: every `module` block's `source` is `./modules/<name>` — a
    registry/git/symlinked source would land in `.terraform/modules/`, outside
    the manifest, the allowlists and every scan."""
    sources = re.findall(r"^\s*source\s*=\s*\"([^\"]*)\"", _stripped("main.tf"), re.M)
    module_sources = [x for x in sources if x != "hashicorp/google"]
    assert len(module_sources) == 6, sources
    for src in module_sources:
        assert re.fullmatch(r"\./modules/[a-z]+", src), src
        assert (INFRA / src).is_dir() and not (INFRA / src).is_symlink(), src
    for f, body in _stripped_files().items():
        if f.name != "main.tf":
            assert not re.search(r"^\s*source\s*=", body, re.M), f


def test_required_providers_is_hashicorp_google_only() -> None:
    """Round 3 #6: the only provider is `hashicorp/google` at the pinned
    constraint — a second provider block or a loosened `>=` reddens."""
    tf = _block(_stripped("main.tf"), r"terraform")
    providers = _block(tf, r"required_providers")
    names = re.findall(r"^\s*([a-z0-9_-]+)\s*=\s*\{", providers, re.M)
    assert names == ["google"], names
    google = _block(providers, r"google\s*=")
    assert re.search(r'source\s*=\s*"hashicorp/google"', google)
    assert re.search(r'version\s*=\s*"~> 6\.0"', google)


# ---------------------------------------------------------------- CI WIF opt-in (H)


WIF_RESOURCES = (
    r'resource "google_iam_workload_identity_pool" "github"',
    r'resource "google_iam_workload_identity_pool_provider" "github"',
    r'resource "google_service_account_iam_member" "wif_impersonation"',
)


def test_ci_wif_is_opt_in_and_count_gated() -> None:
    """Amendment H (round 3 #9): `enable_ci_wif` defaults false and count-gates
    ALL THREE WIF resources (pool, provider, binding) — a default apply builds no
    cross-repo trust; dropping the count from any one, or flipping the default,
    reddens. The SA itself stays unconditional."""
    assert re.search(
        r"^\s*default\s*=\s*false",
        _block(_read("variables.tf"), r'variable "enable_ci_wif"'),
        re.M,
    )
    assert re.search(
        r"^\s*enable_ci_wif\s*=\s*var\.enable_ci_wif",
        _block(_read("main.tf"), r'module "iam"'),
        re.M,
    )
    iam = _stripped("modules", "iam", "main.tf")
    for header in WIF_RESOURCES:
        assert re.search(
            r"^\s*count\s*=\s*var\.enable_ci_wif \? 1 : 0", _block(iam, header), re.M
        ), header
    assert not _has_arg(
        _block(iam, r'resource "google_service_account" "pipeline"'), "count"
    )


def test_wif_output_is_null_guarded() -> None:
    """Amendment J (round 4 #2/#5, corrected round 5): the provider name is a
    ROOT output; the MODULE output is `enable_ci_wif ? …[0].name : null` and the
    root output is a bare passthrough of it — an unguarded `[0]` index is
    "Invalid index … empty tuple" on every default plan (the tester's surviving
    hand-mutation; `terraform validate` does not catch it)."""
    module_out = _block(
        _stripped("modules", "iam", "outputs.tf"),
        r'output "workload_identity_provider"',
    )
    assert re.search(
        r"value\s*=\s*var\.enable_ci_wif \? "
        r"google_iam_workload_identity_pool_provider\.github\[0\]\.name : null",
        module_out,
    )
    root_out = _block(_stripped("outputs.tf"), r'output "workload_identity_provider"')
    assert re.search(r"value\s*=\s*module\.iam\.workload_identity_provider", root_out)


# ---------------------------------------------------------------- invariant 3


# The only paths that may be tracked under `.claude/` — an ALLOWLIST (round 6
# #1: the round-5 key denylist could never be complete; Claude Code adds keys).
# Agents/commands/hooks are prose and a hook SCRIPT (wired only by the
# gitignored settings.local.json); everything else — any settings*.json,
# .mcp.json, lock/state files — would auto-configure whoever opens the checkout.
_TRACKED_CLAUDE_PATH_RE = re.compile(
    r"^\.claude/(agents|commands)/[^/]+\.md$|^\.claude/hooks/[^/]+\.py$"
)
# Local-only Claude Code files the repo's OWN .gitignore must cover (#2, #6, #21).
_LOCAL_ONLY_CLAUDE_FILES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".mcp.json",
    ".claude/scheduled_tasks.lock",
    ".claude/scheduled_tasks.json",
)


def _git_ls_files(pathspec: str) -> list[str]:
    """Tracked paths under `pathspec` (empty when nothing is tracked)."""
    return subprocess.run(
        ["git", "ls-files", pathspec],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


@pytest.fixture(scope="module")
def ignored(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], bool]:
    """`git check-ignore` in a scratch repo holding ONLY this repo's .gitignore:
    neither `core.excludesFile`, this clone's `.git/info/exclude`, nor a global
    `init.templateDir` planting one (`--template=<empty>`, round 8 #6) can stand
    in for a rule in .gitignore (round 6 #6, round 7 #2 — the local exclude
    file carried the `.claude/scheduled_tasks.*` rules)."""
    repo = tmp_path_factory.mktemp("ignore-scratch")
    empty_template = tmp_path_factory.mktemp("empty-template")
    subprocess.run(
        ["git", "init", "-q", f"--template={empty_template}", str(repo)], check=True
    )
    assert not (repo / ".git" / "info" / "exclude").exists()
    (repo / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())

    def _ignored(relpath: str) -> bool:
        return (
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.excludesFile=/dev/null",
                    "check-ignore",
                    "-q",
                    relpath,
                ],
                cwd=repo,
            ).returncode
            == 0
        )

    return _ignored


PRIVATE_KEY_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".p8")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".duckdb", ".pyc", ".ico"}


def test_no_tracked_secret_state_or_tfvars(ignored: Callable[[str], bool]) -> None:
    """No key / tfstate / real tfvars is tracked; the gitignore rule ignores a
    real terraform.tfvars but not the *.example. Private-key filetypes (.pem/.p12/
    …) are rejected by extension; any tracked JSON is content-scanned for a
    SA-key body."""
    tracked = _git_ls_files(".")
    bad = [
        p
        for p in tracked
        if re.search(r"\.tfstate", p)
        or p.endswith((".tfvars", ".tfvars.json"))
        or p.endswith(PRIVATE_KEY_SUFFIXES)
        or (
            p.endswith(".json")
            and re.search(r"(key|credential|service[_-]?account)", p, re.I)
        )
    ]
    assert bad == [], bad
    # Content scan over EVERY tracked text file, not just .json (round 4 #8): a
    # key pasted into a .md/.py/.yml, or a minified one, is caught. Needles are
    # assembled so this file does not match itself.
    sa_type = re.compile(r'"type"\s*:\s*"service' + r'_account"')
    private_key = re.compile(
        r'"private' + r'_key"\s*:|-----BEGIN [A-Z ]*PRIVATE' + r" KEY-----"
    )
    for p in tracked:
        if p.endswith(PRIVATE_KEY_SUFFIXES) or (ROOT / p).suffix in BINARY_SUFFIXES:
            continue
        body = (ROOT / p).read_text(errors="ignore")
        assert not sa_type.search(body), p
        assert not private_key.search(body), p
    assert (INFRA / "terraform.tfvars.example").exists()
    assert ignored("infra/terraform.tfvars")
    # Terraform auto-loads *.auto.tfvars and *.tfvars.json too (round 3 #12).
    for auto in ("terraform.tfvars.json", "x.auto.tfvars", "x.auto.tfvars.json"):
        assert ignored(f"infra/{auto}"), auto
    assert not ignored("infra/terraform.tfvars.example")


def test_auth_is_adc_or_wif_never_keyfile() -> None:
    """No `.tf` sets a `credentials`/`keyfile` argument (the substring appears in
    the `iamcredentials.googleapis.com` service name — an assignment is what
    matters); no `= file(...json)`; the bigquery dbt target is oauth (ADC/WIF)."""
    for f, body in _stripped_files().items():
        assert not _has_arg(body, "credentials"), f
        assert not _has_arg(body, "keyfile"), f
        assert not re.search(r"=\s*file\([^)]*\.json", body), f
    # User ADC needs a quota project for billingbudgets (§8 Gotchas): the
    # provider sends our own, so no per-machine set-quota-project step.
    prov = _block(_stripped("main.tf"), r'provider "google"')
    assert re.search(r"user_project_override\s*=\s*true", prov)
    assert re.search(r"billing_project\s*=\s*var\.project_id", prov)
    bq = _yaml_block((ROOT / "dbt" / "profiles.yml").read_text(), "bigquery")
    assert "method: oauth" in bq
    assert "keyfile" not in bq and "credentials" not in bq


def test_bigquery_profile_project_has_no_default(
    ignored: Callable[[str], bool],
) -> None:
    """Amendment S's profiles.yml half (round 8 #1): the bigquery target's
    `project` is `env_var('OTR_GCP_PROJECT')` with NO default argument, so a
    missing export is a dbt parse error — `env_var('OTR_GCP_PROJECT', '')`
    (the pre-S text) is red."""
    bq = _yaml_block((ROOT / "dbt" / "profiles.yml").read_text(), "bigquery")
    m = re.search(r"project:\s*\"\{\{\s*env_var\((.*?)\)\s*\}\}\"", bq)
    assert m, bq
    assert m.group(1) == "'OTR_GCP_PROJECT'", m.group(1)


def test_bigquery_profile_location_is_the_datasets() -> None:
    """Phase 9b (Amendment O of 9a): the bigquery output's `location` equals the
    Terraform `region` default the two datasets are created in — dbt-bigquery
    would otherwise default to the US multi-region and fail "Dataset … not
    found in location US"."""
    bq = _yaml_block((ROOT / "dbt" / "profiles.yml").read_text(), "bigquery")
    m = re.search(r"^\s*location:\s*(\S+)", bq, re.M)
    assert m, bq
    region = _block(_stripped("variables.tf"), r'variable "region"')
    assert re.search(r'default\s*=\s*"' + re.escape(m.group(1)) + '"', region)


def _yaml_block(text: str, key: str) -> str:
    lines, out, grab, indent = text.splitlines(), [], False, None
    for line in lines:
        m = re.match(r"^(\s*)" + re.escape(key) + r":\s*$", line)
        if m and not grab:
            grab, indent = True, len(m.group(1))
            continue
        if grab:
            if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                break
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- WIF scoping (7)


def test_wif_provider_condition_is_the_repo_and_ref_conjunction() -> None:
    """Round 2 #2: the condition is repo AND ref (a single `&&` expression) — a
    `&&` → `||` mutation drops the `&&` and reddens; repo-only or ref-only fails."""
    prov = _block(
        _read("modules", "iam", "main.tf"),
        r'resource "google_iam_workload_identity_pool_provider" "github"',
    )
    cond = re.search(r'attribute_condition\s*=\s*"(.+)"', prov)
    assert cond, "no attribute_condition"
    text = cond.group(1)
    assert "assertion.repository ==" in text
    assert "assertion.ref ==" in text
    assert "&&" in text and "||" not in text
    # Round 3 #2: the issuer is GitHub's OIDC endpoint, exactly — a mutation to
    # another issuer would let a foreign IdP mint a token that passes the
    # repo/ref condition.
    assert re.search(
        r'issuer_uri\s*=\s*"https://token\.actions\.githubusercontent\.com"',
        _block(prov, r"oidc"),
    )
    # Round 3 #4: the combined attribute is composed from BOTH claims.
    assert (
        '"attribute.repo_ref" = "assertion.repository + \\"@\\" + assertion.ref"'
        in _block(prov, r"attribute_mapping\s*=")
    )


def test_wif_impersonation_binds_on_combined_repo_and_ref() -> None:
    """Round 2 #3: the impersonation member scopes BOTH repo and ref (the combined
    `attribute.repo_ref/…@…` principalSet) — widening to `attribute.repository/*`
    or repo-only reddens."""
    member = re.search(
        r'member\s*=\s*"([^"]+)"',
        _block(
            _read("modules", "iam", "main.tf"),
            r'resource "google_service_account_iam_member" "wif_impersonation"',
        ),
    )
    assert member, "no member"
    # Round 3 #4: exact composition — the principalSet ends with the combined
    # attribute bound to BOTH vars; `attribute.repository/*` or repo-only reddens.
    assert member.group(1).endswith(
        "/attribute.repo_ref/${var.github_repository}@${var.github_ref}"
    ), member.group(1)
    assert "/attribute.repository/" not in member.group(1)


# ---------------------------------------------------------------- APIs (8)


REQUIRED_SERVICES = (
    "serviceusage",
    "cloudresourcemanager",
    "bigquery",
    "storage",
    "iam",
    "sts",
    "iamcredentials",
    "cloudbilling",
    "billingbudgets",
)


def test_required_apis_are_enabled_and_survive_destroy() -> None:
    """Round 2 #5/#6: `google_project_service` enables the full set (incl. the two
    bootstrap APIs) with `disable_on_destroy = false`, asserted on the STRIPPED
    resource (a comment can't satisfy it), and the modules depend on it."""
    main = _stripped("main.tf")
    svc = _block(main, r'resource "google_project_service" "required"')
    assert re.search(r"disable_on_destroy\s*=\s*false", svc)
    assert re.search(r"for_each\s*=\s*toset\(local\.required_services\)", svc)
    # Scoped to the list the resource iterates (round 3 #11), as an exact set.
    lst = re.search(r"required_services\s*=\s*\[(.*?)\]", _block(main, r"locals"), re.S)
    assert lst, "no local.required_services"
    assert set(re.findall(r'"([^"]+)"', lst.group(1))) == {
        f"{s}.googleapis.com" for s in REQUIRED_SERVICES
    }


def test_modules_depend_on_the_service_enablement() -> None:
    """Round 2 #7: each free-tier module `depends_on` the API enablement, so a
    fresh apply doesn't race the not-yet-enabled API."""
    main = _read("main.tf")
    for mod in ("bigquery", "gcs", "iam", "budget"):
        assert "google_project_service.required" in _block(main, rf'module "{mod}"'), (
            mod
        )


# ---------------------------------------------------------------- invariant 4


DATASET_CREATING_ROLES = {
    "roles/bigquery.dataOwner",
    "roles/bigquery.admin",
    "roles/bigquery.user",
    "roles/owner",
    "roles/editor",
}

# Roles granted TO the SA (what it can do) …
LEAST_PRIVILEGE_ROLES = {
    "roles/bigquery.jobUser",
    "roles/bigquery.dataEditor",
    "roles/storage.objectAdmin",
    # Phase 10 (spanner module, count-gated): connectionUser on the ONE
    # connection; the database grant is the custom data-plane role
    # (SPANNER_DATA_PERMISSIONS below — round 2 #1, Amendment E), never a
    # predefined Spanner role: every writing one carries updateDdl.
    "roles/bigquery.connectionUser",
}
# … and ON the SA (who may act as it): CI's WIF binding, the operator's
# impersonation (Amendment Q). Both are `google_service_account_iam_member`.
ON_SA_ROLES = {
    "roles/iam.workloadIdentityUser",
    "roles/iam.serviceAccountTokenCreator",
}
SA_MEMBER = "serviceAccount:${google_service_account.pipeline.email}"
# Phase 10: the spanner module's grants, pinned member-for-member — the
# pipeline SA (a var from module.iam) reads/writes the one database and may
# use the one connection; nothing else is granted anything (Amendment D).
SPANNER_MEMBERS = {
    '"pipeline_user"': "serviceAccount:${var.sa_email}",
    '"pipeline_connection_user"': "serviceAccount:${var.sa_email}",
}


def _grant_blocks() -> list[str]:
    """Every IAM grant block, header included — RAW text (the comment stripper
    would truncate `principalSet://…` at its `//`); the member/role/count
    regexes are line-anchored so a comment cannot stand in for the argument."""
    out: list[str] = []
    for f in _tf_files():
        out += _blocks_with_headers(
            f.read_text(), r'resource "google_[a-z_]+_iam_member"\s+"[^"]+"'
        )
    return out


def test_sa_roles_are_least_privilege() -> None:
    """Every role grant in ANY `.tf` ⊆ the allowlist; never owner/editor. The
    roles ON the SA appear only in `google_service_account_iam_member` blocks."""
    roles: set[str] = set()
    for f in _tf_files():
        roles |= set(re.findall(r'role\s*=\s*"(roles/[^"]+)"', f.read_text()))
    assert roles, "no IAM roles found"
    assert roles <= LEAST_PRIVILEGE_ROLES | ON_SA_ROLES, (
        roles - LEAST_PRIVILEGE_ROLES - ON_SA_ROLES
    )
    assert "roles/owner" not in roles and "roles/editor" not in roles
    for b in _grant_blocks():
        m = re.search(r'^\s*role\s*=\s*"(roles/[^"]+)"', b, re.M)
        if m is None:  # the ONE custom-role grant (round 2 #1), by reference
            assert b.startswith(
                'resource "google_spanner_database_iam_member" "pipeline_user"'
            ), b.splitlines()[0]
            assert re.search(
                r"^\s*role\s*=\s*google_project_iam_custom_role\.data_user\.name\s*$",
                b,
                re.M,
            ), b
            continue
        role = m.group(1)
        on_sa = b.startswith('resource "google_service_account_iam_member"')
        assert (role in ON_SA_ROLES) == on_sa, b.splitlines()[0]


def test_every_grant_member_is_pinned() -> None:
    """Round 6 #5: invariant 4 is about WHO gets what. Every grant TO the SA has
    `member = serviceAccount:<the SA>` — never allUsers / allAuthenticatedUsers /
    a literal email; the grants ON the SA bind the exact WIF principalSet or the
    validated `operator_principal` var, and the latter is count-gated on it."""
    seen = 0
    for b in _grant_blocks():
        member = re.search(r'^\s*member\s*=\s*"?([^"\n]+?)"?\s*$', b, re.M).group(1)
        if b.startswith(
            'resource "google_service_account_iam_member" "operator_token_creator"'
        ):
            assert member == "var.operator_principal", member
            assert re.search(
                r"^\s*count\s*=\s*var\.operator_principal == null \? 0 : 1", b, re.M
            )
        elif b.startswith('resource "google_service_account_iam_member"'):
            assert member.startswith("principalSet://iam.googleapis.com/"), member
        elif any(key in b.splitlines()[0] for key in SPANNER_MEMBERS):
            key = next(k for k in SPANNER_MEMBERS if k in b.splitlines()[0])
            assert member == SPANNER_MEMBERS[key], member
            assert b.startswith(f'resource "{SPANNER_GRANT_SCOPES[key][0]}"'), key
        else:
            assert member == SA_MEMBER, member
        seen += 1
    assert seen == 8, seen


def test_no_role_can_create_a_dataset() -> None:
    """Amendment I (round 4 #1): Terraform creates exactly the two datasets and
    nothing else can — no role in the tree carries `bigquery.datasets.create`
    (dataOwner/admin/owner/editor), so 9b's dbt build must land inside them."""
    roles: set[str] = set()
    for f in _tf_files():
        roles |= set(re.findall(r'role\s*=\s*"(roles/[^"]+)"', f.read_text()))
    assert not roles & DATASET_CREATING_ROLES, roles & DATASET_CREATING_ROLES
    datasets = _blocks(
        _stripped("modules", "bigquery", "main.tf"),
        r'resource "google_bigquery_dataset"[^{]*',
    )
    assert len(datasets) == 2


def test_project_level_grant_is_only_bigquery_jobuser() -> None:
    """Round 2 #9: scope, not just role name. The only PROJECT-level grant is
    `bigquery.jobUser` (which must be project-level); `dataEditor` is
    dataset-scoped and `objectAdmin` bucket-scoped — moving `objectAdmin` to a
    project-wide `google_project_iam_member` reddens."""
    project_roles: set[str] = set()
    for body in _stripped_files().values():  # whole tree (round 3 #1)
        project_roles |= {
            re.search(r'role\s*=\s*"(roles/[^"]+)"', b).group(1)
            for b in _blocks(body, r'resource "google_project_iam_member"\s+"[^"]+"')
        }
    assert project_roles == {"roles/bigquery.jobUser"}, project_roles
    iam = _stripped("modules", "iam", "main.tf")
    # objectAdmin is granted on the bucket, dataEditor on the datasets
    assert _blocks(iam, r'resource "google_storage_bucket_iam_member"\s+"[^"]+"')
    assert (
        len(_blocks(iam, r'resource "google_bigquery_dataset_iam_member"\s+"[^"]+"'))
        == 2
    )


# ---------------------------------------------------------------- invariant 5


def test_every_resource_is_destroyable() -> None:
    """No `prevent_destroy`; both datasets `delete_contents_on_destroy`; the bucket
    `force_destroy` — nothing billable survives a teardown (Done-when 5)."""
    for f, body in _stripped_files().items():
        assert "prevent_destroy" not in body, f
    bq = _stripped("modules", "bigquery", "main.tf")
    assert len(re.findall(r"delete_contents_on_destroy\s*=\s*true", bq)) == 2
    assert re.search(
        r"force_destroy\s*=\s*true", _stripped("modules", "gcs", "main.tf")
    )


def test_budget_currency_is_the_billing_accounts() -> None:
    """§8 Gotchas: a literal currency (USD) is a 400 on a non-USD billing account;
    the currency is read from `data.google_billing_account` inside the module
    (apply-time via the module's depends_on), never a literal."""
    budget = _stripped("modules", "budget", "main.tf")
    assert _blocks(budget, r'data "google_billing_account" "this"')
    assert re.search(
        r"currency_code\s*=\s*data\.google_billing_account\.this\.currency_code",
        budget,
    )
    assert not re.search(r'currency_code\s*=\s*"', budget), "literal currency"


def test_budget_scope_and_threshold_denominator_are_pinned() -> None:
    """Round 6 #3/#4: the budget filters on THIS project (never billing-account-
    wide) and every threshold_percent divides by the budget amount (`/ 100`
    would alert at 25/75)."""
    budget = _stripped("modules", "budget", "main.tf")
    res = _block(budget, r'resource "google_billing_budget"[^{]*')
    assert re.search(
        r'projects\s*=\s*\["projects/\$\{var\.project_number\}"\]',
        _block(res, r"budget_filter"),
    )
    assert re.search(
        r"threshold_percent\s*=\s*threshold_rules\.value / local\.budget_amount",
        _block(res, r'dynamic "threshold_rules"'),
    )


def test_budget_amount_is_the_smallest_threshold() -> None:
    """Round 4 #3: the amount is `min(...)` of the thresholds so every
    threshold_percent is a whole multiple (50 → 1.0, 150 → 3.0) and the plan
    never drifts on a repeating decimal; `max` → red."""
    budget = _stripped("modules", "budget", "main.tf")
    assert re.search(
        r"budget_amount\s*=\s*min\(var\.alert_thresholds\.\.\.\)",
        _block(budget, r"locals"),
    )


def test_bucket_is_hardened() -> None:
    """Round 2 #8: public-access prevention enforced, uniform access, versioning
    on, and a lifecycle rule so versioning doesn't accrete cost — each pinned, so
    dropping any one reddens."""
    gcs = _stripped("modules", "gcs", "main.tf")
    assert re.search(r'public_access_prevention\s*=\s*"enforced"', gcs)
    assert re.search(r"uniform_bucket_level_access\s*=\s*true", gcs)
    assert re.search(r"enabled\s*=\s*true", _block(gcs, r"versioning"))
    assert _blocks(gcs, r"lifecycle_rule")


# ---------------------------------------------------------------- region (17)


def test_region_and_dataset_location_are_us_central1() -> None:
    """Round 2 #17: the region default and the dataset/bucket locations are
    pinned — an `eu-west1` default or a literal `location = "US"` reddens while
    the records say us-central1."""
    assert re.search(
        r'default\s*=\s*"us-central1"',
        _block(_read("variables.tf"), r'variable "region"'),
    )
    bq = _stripped("modules", "bigquery", "main.tf")
    assert len(re.findall(r"location\s*=\s*var\.region", bq)) == 2
    assert not re.search(r'location\s*=\s*"', bq), "dataset location must be var.region"
    gcs = _stripped("modules", "gcs", "main.tf")
    assert re.search(r"location\s*=\s*var\.region", gcs)


# ---------------------------------------------------------------- pinned defaults


def test_stated_defaults_are_pinned() -> None:
    """DECISIONS/CLAUDE state 50/150 (the billing account's currency — Amendment
    L), NO default repository (Amendment K), and refs/heads/main as facts; pin
    the actual variable defaults (review round 1 #20 / round 2 / round 4)."""
    text = _read("variables.tf")
    assert re.search(
        r"default\s*=\s*\[\s*50\s*,\s*150\s*\]",
        _block(text, r'variable "budget_alert_thresholds"'),
    )
    assert not _blocks(text, r'variable "budget_alert_thresholds_usd"')
    assert re.search(
        r"^\s*default\s*=\s*null", _block(text, r'variable "github_repository"'), re.M
    )
    # The pool refuses the toggle without a repo — a named plan-time error, not
    # trust in a baked-in slug.
    pool = _block(_stripped("modules", "iam", "main.tf"), WIF_RESOURCES[0])
    assert re.search(
        r"condition\s*=\s*var\.github_repository != null",
        _block(_block(pool, r"lifecycle"), r"precondition"),
    )
    assert re.search(
        r'default\s*=\s*"refs/heads/main"', _block(text, r'variable "github_ref"')
    )


def test_tracked_claude_config_is_prose_and_hook_scripts_only(
    ignored: Callable[[str], bool],
) -> None:
    """Amendment M (round 4 #9), re-implemented ONCE in round 6 as an allowlist:
    the only tracked paths under `.claude/` are agent/command prose and hook
    SCRIPTS (wired only by the gitignored settings.local.json); any tracked
    settings*.json / .mcp.json / lock file — whatever its content — is red, and
    each local-only file is ignored by the repo's own .gitignore (not a global
    excludes rule)."""
    off_list = [
        p for p in _git_ls_files(".claude") if not _TRACKED_CLAUDE_PATH_RE.match(p)
    ]
    assert not off_list, off_list
    assert not _git_ls_files(".mcp.json")
    for relpath in _LOCAL_ONLY_CLAUDE_FILES:
        assert ignored(relpath), relpath


# ---------------------------------------------------------------- Amendment P


def test_tf_tree_matches_manifest() -> None:
    """Amendments P/R: every `.tf`/`.tf.json` + the provider lock hash to the
    committed `infra/MANIFEST.sha256` — ANY hand-mutation of any attribute is
    red until `make tf-freeze CONFIRM=yes` rewrites it (the one allowlist that
    replaces property-by-property pinning as the mutation gate). Asserted on
    the diff LIST, and the pinned set is asserted against an independent walk,
    so a neutered `manifest_diff`/`is_pinned` is red (round 7 #1; round 8 #3:
    the pinned set is `compute_manifest`'s own keys — the freeze's — not a
    test-only helper)."""
    assert cli.MANIFEST.is_file(), "infra/MANIFEST.sha256 missing"
    assert cli.manifest_diff() == []
    pinned = sorted(cli.compute_manifest())
    assert pinned == sorted(
        p.relative_to(INFRA).as_posix()
        for p in _tf_files() + [INFRA / ".terraform.lock.hcl"]
    )
    assert len(pinned) >= 8
    from generator import manifest

    assert set(manifest.parse(cli.MANIFEST.read_text())) == set(pinned)


def test_manifest_gate_reads_tf_json_and_vanished_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 7 #3/#5 on a scratch tree: a `.tf.json` outside the manifest is
    `extra` (Terraform loads it, so the allowlist must see it); a pinned file
    deleted from disk is `missing`, and `freeze` refuses rather than silently
    narrowing the pin; `.terraform/` and `*.tfvars` stay outside."""
    tree = tmp_path / "infra"
    (tree / "modules" / "m").mkdir(parents=True)
    (tree / ".terraform" / "providers").mkdir(parents=True)
    (tree / "main.tf").write_text('resource "a" "b" {}\n')
    (tree / "modules" / "m" / "x.tf").write_text("# m\n")
    (tree / ".terraform.lock.hcl").write_text("provider {}\n")
    (tree / ".terraform" / "providers" / "y.tf").write_text("# cache\n")
    (tree / "terraform.tfvars").write_text('project_id = "p"\n')
    manifest = tree / "MANIFEST.sha256"
    monkeypatch.setattr(cli, "INFRA_DIR", tree)
    monkeypatch.setattr(cli, "MANIFEST", manifest)
    assert cli.manifest_diff() == [
        ".terraform.lock.hcl: extra",
        "main.tf: extra",
        "modules/m/x.tf: extra",
    ]
    assert cli.freeze("yes", "command line") == 0
    assert cli.manifest_diff() == []
    (tree / "modules" / "m" / "owner.tf.json").write_text('{"resource": {}}\n')
    assert cli.manifest_diff() == ["modules/m/owner.tf.json: extra"]
    (tree / "main.tf").write_text('resource "a" "b" { x = 1 }\n')
    assert "main.tf: changed" in cli.manifest_diff()
    (tree / "modules" / "m" / "x.tf").unlink()
    assert "modules/m/x.tf: missing" in cli.manifest_diff()
    before = manifest.read_text()
    with pytest.raises(SystemExit) as e:
        cli.freeze("yes", "command line")
    assert e.value.code == 2
    assert manifest.read_text() == before


def test_tf_freeze_requires_confirm_origin_and_writes_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`freeze` is CONFIRM-gated like apply/destroy (an env CONFIRM=yes is refused
    with nothing written); on the command line it renders the manifest in the
    fixtures format (`<hex>  <path>`, sorted)."""
    target = tmp_path / "MANIFEST.sha256"
    monkeypatch.setattr(cli, "MANIFEST", target)
    for confirm, origin in (
        ("yes", "environment"),
        ("", "command line"),
        ("no", "command line"),
    ):
        with pytest.raises(SystemExit) as e:
            cli.freeze(confirm, origin)
        assert e.value.code == 2
        assert not target.exists()
    assert cli.freeze("yes", "command line") == 0
    from generator import manifest

    assert manifest.parse(target.read_text()) == cli.compute_manifest()
    assert target.read_text() == manifest.render(cli.compute_manifest())


# ---------------------------------------------------------------- invariant 6 (cli)


class _FakeRunner:
    """A subprocess.run stand-in: records argv and env, never spawns terraform.
    `show -json` answers with `plan_json` (default: a plan with no destroys),
    so the plan-first apply can be exercised end to end offline."""

    NO_DESTROY = (
        '{"resource_changes": [{"address": "a.b", "change": {"actions": ["create"]}}]}'
    )

    def __init__(self, rc: int = 0, plan_json: str = NO_DESTROY) -> None:
        self.rc = rc
        self.plan_json = plan_json
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(
        self, argv: list[str], env: dict[str, str] | None = None, **kw: object
    ) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        self.envs.append(env or {})
        stdout = self.plan_json if "show" in argv else ""
        return subprocess.CompletedProcess(argv, self.rc, stdout=stdout, stderr="")


@pytest.fixture
def scratch_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty `INFRA_DIR` for the runner tests: a developer's own gitignored
    `infra/terraform.tfvars` must not decide the suite (Amendment T refuses it
    live — round 8 #5 was reproduced by exactly such a file)."""
    tree = tmp_path / "infra"
    tree.mkdir()
    monkeypatch.setattr(cli, "INFRA_DIR", tree)
    return tree


def test_cli_validates_project() -> None:
    for good in ("my-proj", "my-project-123", "abcdef"):
        assert cli.validate_project(good) == good
    bad = ("", "../x", "A-Bad", "ab", "proj-", "-lead", "a b", "x" * 40, "my-proj\n")
    for value in bad:
        with pytest.raises(SystemExit) as e:
            cli.validate_project(value)
        assert e.value.code == 2, value


def test_cli_requires_confirm_origin(scratch_infra: Path) -> None:
    """tf-apply/tf-destroy refuse unless CONFIRM=yes has command-line origin — the
    guard runs BEFORE the runner, so the fake is never called on a refusal. (The
    fake is what lets `require_confirm delete-call` be a safe mutation line.)"""
    for cmd in ("apply", "destroy"):
        for confirm, origin in (
            ("yes", "environment"),
            ("yes", "file"),
            ("", "command line"),
            ("no", "command line"),
        ):
            fake = _FakeRunner()
            with pytest.raises(SystemExit) as e:
                cli.tf(cmd, "my-proj", confirm, origin, runner=fake)
            assert e.value.code == 2, (cmd, confirm, origin)
            assert fake.calls == [], "runner spawned on a refusal"
    fake = _FakeRunner()
    assert cli.tf("apply", "my-proj", "yes", "command line", runner=fake) == 0
    assert [c[2] for c in fake.calls] == ["plan", "show", "apply"]  # plan-first


def test_cli_validates_before_running(scratch_infra: Path) -> None:
    """Invariant 6's ordering half: a bad PROJECT dies before the runner runs."""
    for cmd in ("plan", "apply", "destroy"):
        fake = _FakeRunner()
        with pytest.raises(SystemExit) as e:
            cli.tf(cmd, "../x", "yes", "command line", runner=fake)
        assert e.value.code == 2, cmd
        assert fake.calls == [], cmd


def test_cli_builds_the_expected_argv(scratch_infra: Path) -> None:
    """Round 2 #11: the argv reaching the runner carries the validated
    `-var project_id=…` and `-input=false` (no interactive prompt); `destroy`
    carries `-auto-approve` and `apply` does NOT (it applies the saved plan,
    Amendment F) — dropping any reddens."""
    fake = _FakeRunner()
    assert cli.tf("plan", "my-proj", runner=fake) == 0
    plan = fake.calls[0]
    assert "-var" in plan and "project_id=my-proj" in plan and "-input=false" in plan
    assert "-auto-approve" not in plan
    fake = _FakeRunner()
    assert cli.tf("destroy", "my-proj", "yes", "command line", runner=fake) == 0
    argv = fake.calls[0]
    assert "project_id=my-proj" in argv and "-input=false" in argv
    assert "-auto-approve" in argv
    # apply (round 2 #3): the vars go to the PLAN; the apply takes the saved
    # plan file and nothing else — no -auto-approve, no -var (the plan you
    # were shown is the apply you get)
    fake = _FakeRunner()
    assert cli.tf("apply", "my-proj", "yes", "command line", runner=fake) == 0
    plan_call, show_call, apply_call = fake.calls
    assert "project_id=my-proj" in plan_call and "-input=false" in plan_call
    out = next(a for a in plan_call if a.startswith("-out="))[len("-out=") :]
    assert show_call[2:] == ["show", "-json", out]
    assert apply_call[2:] == ["apply", "-input=false", out]
    assert "-auto-approve" not in apply_call and "-var" not in apply_call
    assert not Path(out).exists()  # the plan file (holds var values) is removed


def test_cli_vars_are_the_only_toggle_path(
    scratch_infra: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """fix/tf-vars-argv (BACKLOG "TF_VAR_* from the environment bypasses
    Amendment T"): a toggle reaches Terraform ONLY as `VARS='name=value,…'`
    from the COMMAND LINE → argv `-var` items (a bracketed numeric list is one
    item — `budget_alert_thresholds`); `project_id` in VARS, a malformed item,
    whitespace, a metacharacter, or an env-origin VARS is refused; any
    `TF_VAR_*` / `TF_CLI_ARGS*` in the environment refuses EVERY command
    (validate evaluates variable validations) before the runner."""
    fake = _FakeRunner()
    assert cli.tf("plan", "my-proj", runner=fake, vars_="") == 0
    assert fake.calls[0].count("-var") == 1
    fake = _FakeRunner()
    vars_ = (
        "enable_ci_wif=true,github_repository=o/r,"
        "operator_principal=user:a@b.c,budget_alert_thresholds=[50,150]"
    )
    assert cli.tf("apply", "my-proj", "yes", "command line", fake, vars_) == 0
    argv = fake.calls[0]
    assert argv[argv.index("project_id=my-proj") + 1 :] == [
        "-var",
        "enable_ci_wif=true",
        "-var",
        "github_repository=o/r",
        "-var",
        "operator_principal=user:a@b.c",
        "-var",
        "budget_alert_thresholds=[50,150]",
    ]
    bad = (
        "enable_ci_wif",
        "x=1 2",
        "Enable=1",
        'x="; rm',
        "x=1,,",
        "project_id=p",
        "x=[1,a]",
        "x=[1",
    )
    for item in bad:
        fake = _FakeRunner()
        with pytest.raises(SystemExit) as e:
            cli.tf("plan", "my-proj", runner=fake, vars_=item)
        assert e.value.code == 2 and fake.calls == [], item
        assert "VARS: refused" in capsys.readouterr().out
    fake = _FakeRunner()  # an exported VARS is refused, like an exported CONFIRM
    with pytest.raises(SystemExit) as e:
        cli.tf("plan", "my-proj", runner=fake, vars_="x=1", vars_origin="environment")
    assert e.value.code == 2 and fake.calls == []
    assert "VARS: refused — set on the command line" in capsys.readouterr().out
    for name in ("TF_VAR_enable_composer", "TF_CLI_ARGS_apply", "TF_CLI_ARGS"):
        monkeypatch.setenv(name, "x")
        assert cli.env_tf_vars() == [name]
        for cmd in ("plan", "apply", "destroy", "validate"):
            fake = _FakeRunner()
            with pytest.raises(SystemExit) as e:
                cli.tf(cmd, "my-proj", "yes", "command line", runner=fake)
            assert e.value.code == 2 and fake.calls == [], (cmd, name)
            assert f"tf-{cmd}: refused — {name}" in capsys.readouterr().out
        monkeypatch.delenv(name)
    assert cli.tf("plan", "my-proj", runner=_FakeRunner()) == 0


def test_cli_child_env_is_an_allowlist(
    scratch_infra: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terraform child sees ONLY `ENV_ALLOW` (fix/tf-vars-argv):
    TF_WORKSPACE, TF_DATA_DIR, TF_LOG_PATH never reach it — the argv is the
    whole input by construction, not by a denylist."""
    for k, v in {
        "TF_WORKSPACE": "other",
        "TF_DATA_DIR": "/tmp/x",
        "TF_LOG": "TRACE",
        "TF_LOG_PATH": "/tmp/l",
        "HOME": "/tmp/h",
    }.items():
        monkeypatch.setenv(k, v)
    fake = _FakeRunner()
    assert cli.tf("plan", "my-proj", runner=fake) == 0
    env = fake.envs[0]
    assert set(env) <= set(cli.ENV_ALLOW)
    assert env["HOME"] == "/tmp/h" and "PATH" in env
    assert not any(k.startswith(("GOOGLE_", "TF_")) for k in env)


def test_cli_refuses_a_credential_in_the_env_loudly(
    scratch_infra: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Round 2 #2: a keyfile / inline key / bearer token in the environment is
    a loud refusal (exit 2, no terraform child) on every project-taking
    command — the allowlist alone would have DROPPED it silently, applying as
    whoever ADC is while the operator believed the key was in use. The shape
    match catches a new `GOOGLE_*CREDENTIALS*` spelling too."""
    for name in (
        "GOOGLE_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_BACKUP_CREDENTIALS_JSON",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "CLOUDSDK_AUTH_ACCESS_TOKEN",
        "GOOGLE_CLOUD_KEYFILE_JSON",  # Amendment L (round 3 #2): the keyfile family
        "GCLOUD_KEYFILE_JSON",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
    ):
        monkeypatch.setenv(name, "x")
        assert cli.keyfile_env() == [name]
        for cmd in ("plan", "apply", "destroy"):
            fake = _FakeRunner()
            with pytest.raises(SystemExit) as e:
                cli.tf(cmd, "my-proj", "yes", "command line", runner=fake)
            assert e.value.code == 2 and fake.calls == [], (cmd, name)
            assert f"tf-{cmd}: refused — {name} in the environment" in (
                capsys.readouterr().out
            )
        monkeypatch.delenv(name)
    assert cli.tf("plan", "my-proj", runner=_FakeRunner()) == 0


DESTROYING_PLAN = (
    '{"resource_changes": ['
    '{"address": "module.spanner[0].google_spanner_instance.this", '
    '"change": {"actions": ["delete"]}}, '
    '{"address": "module.bigquery.google_bigquery_dataset.raw", '
    '"change": {"actions": ["no-op"]}}, '
    '{"address": "module.gcs.google_storage_bucket.this", '
    '"change": {"actions": ["delete", "create"]}}]}'
)


def test_apply_plans_first_and_refuses_destroys_without_allow_destroy(
    scratch_infra: Path, capsys: pytest.CaptureFixture
) -> None:
    """Round 2 #3 (Amendment F): tf-apply reads its own saved plan back and
    refuses to apply one that deletes (or replaces) anything unless
    ALLOW_DESTROY=yes came from the command line — so an apply that omitted
    `enable_spanner=true` while Spanner is up cannot tear it down; the
    toggle-flip teardown passes the flag and proceeds."""
    assert cli.planned_deletes(DESTROYING_PLAN) == [
        "module.gcs.google_storage_bucket.this",
        "module.spanner[0].google_spanner_instance.this",
    ]
    assert cli.planned_deletes(_FakeRunner.NO_DESTROY) == []
    assert cli.planned_deletes('{"resource_changes": []}') == []
    # Amendment K (round 3 #1): a plan that cannot be read back is a refusal,
    # never "no deletes" — the gate runs on evidence, not on absence.
    for bad in (
        "",
        "not json",
        "[]",
        '{"format_version": "1.2"}',
        '{"resource_changes": {}}',
    ):
        with pytest.raises(SystemExit) as e:
            cli.planned_deletes(bad)
        assert e.value.code == 2
        assert "could not be read back" in capsys.readouterr().out
    fake = _FakeRunner(plan_json="")
    with pytest.raises(SystemExit) as e:
        cli.tf("apply", "my-proj", "yes", "command line", runner=fake)
    assert e.value.code == 2
    assert len(fake.calls) == 2 and "show" in fake.calls[1]  # plan, show
    assert not any("apply" in c for c in fake.calls)  # never apply
    for allow, origin in (("", "file"), ("yes", "environment"), ("no", "command line")):
        fake = _FakeRunner(plan_json=DESTROYING_PLAN)
        with pytest.raises(SystemExit) as e:
            cli.tf(
                "apply",
                "my-proj",
                "yes",
                "command line",
                runner=fake,
                allow_destroy=allow,
                allow_destroy_origin=origin,
            )
        assert e.value.code == 2, (allow, origin)
        assert [c[2] for c in fake.calls] == ["plan", "show"]  # never `apply`
        out = capsys.readouterr().out
        assert "tf-apply: refused — the plan destroys" in out
        assert "google_spanner_instance.this" in out and "ALLOW_DESTROY=yes" in out
    fake = _FakeRunner(plan_json=DESTROYING_PLAN)
    rc = cli.tf(
        "apply",
        "my-proj",
        "yes",
        "command line",
        runner=fake,
        allow_destroy="yes",
        allow_destroy_origin="command line",
    )
    assert rc == 0 and [c[2] for c in fake.calls] == ["plan", "show", "apply"]
    # a failing plan / show never reaches apply
    fake = _FakeRunner(rc=1)
    assert cli.tf("apply", "my-proj", "yes", "command line", runner=fake) == 1
    assert [c[2] for c in fake.calls] == ["plan"]


def test_cli_main_forwards_vars_and_origin_to_tf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The argv → tf() seam (the tester's three surviving mutations): main()
    forwards --vars and --vars-origin for plan, apply and destroy — so the plan
    you read is the apply you get."""
    seen: list[dict[str, object]] = []

    def spy(
        cmd: str,
        project: str = "",
        confirm: str = "",
        origin: str = "",
        runner=None,
        vars_: str = "",
        vars_origin: str = "command line",
        **kw: str,
    ) -> int:  # noqa: E501
        seen.append({"cmd": cmd, "vars_": vars_, "vars_origin": vars_origin, **kw})
        return 0

    monkeypatch.setattr(cli, "tf", spy)
    gate = ["--confirm", "yes", "--confirm-origin", "command line"]
    v = ["--vars", "enable_composer=true", "--vars-origin", "environment"]
    assert cli.main(["plan", "--project", "my-proj", *v]) == 0
    assert cli.main(["apply", "--project", "my-proj", *gate, *v]) == 0
    assert cli.main(["destroy", "--project", "my-proj", *gate, *v]) == 0
    assert [s["cmd"] for s in seen] == ["plan", "apply", "destroy"]
    assert all(s["vars_"] == "enable_composer=true" for s in seen)
    assert all(s["vars_origin"] == "environment" for s in seen)
    # round 2 #3: apply alone forwards --allow-destroy + its origin
    seen.clear()
    ad = ["--allow-destroy", "yes", "--allow-destroy-origin", "environment"]
    assert cli.main(["apply", "--project", "my-proj", *gate, *ad]) == 0
    assert seen[0]["allow_destroy"] == "yes"
    assert seen[0]["allow_destroy_origin"] == "environment"


def test_cli_validate_argv_is_offline(scratch_infra: Path) -> None:
    """Round 3 #5 (round 2 #9's live survivor): `tf-validate` runs init with
    `-backend=false` (no state backend touched, no auth) and `-lockfile=readonly`
    (Amendment R: init may never rewrite the pinned provider lock) then
    validate, then fmt -check — dropping a flag or a step reddens."""
    fake = _FakeRunner()
    assert cli.tf("validate", runner=fake) == 0
    steps = [argv[2:] for argv in fake.calls]
    assert steps == [
        ["init", "-backend=false", "-input=false", "-lockfile=readonly"],
        ["validate"],
        ["fmt", "-check", "-recursive"],
    ], steps


def test_cli_nonzero_step_is_a_fail(
    scratch_infra: Path, capsys: pytest.CaptureFixture
) -> None:
    """Round 8 #2: a nonzero terraform exit on any tf-validate step (an init
    refused by `-lockfile=readonly` on a platform the lock lacks, a validate
    error, an unformatted file) or on plan/apply/destroy is exit 1 with a FAIL
    line — `return 1 → return 0` is red."""
    fake = _FakeRunner(rc=1)
    assert cli.tf("validate", runner=fake) == 1
    assert len(fake.calls) == 1, "later steps ran after a FAIL"
    assert "tf-validate FAIL: init" in capsys.readouterr().out
    fake = _FakeRunner(rc=1)
    assert cli.tf("plan", "my-proj", runner=fake) == 1
    assert "tf-plan FAIL: my-proj" in capsys.readouterr().out


def test_cli_refuses_auto_loaded_tfvars(
    scratch_infra: Path, capsys: pytest.CaptureFixture
) -> None:
    """Amendment T: `terraform.tfvars` / `*.auto.tfvars{,.json}` under infra/ —
    gitignored, unpinned, auto-loaded — refuse plan/apply/destroy before the
    runner; `tf-validate` (offline) is not gated; the example file and a plain
    `x.tfvars` (not auto-loaded) do not trigger."""
    tree = scratch_infra
    (tree / "terraform.tfvars.example").write_text("# example\n")
    (tree / "x.tfvars").write_text("enable_spanner = true\n")
    assert cli.auto_tfvars() == []
    assert cli.tf("plan", "my-proj", runner=_FakeRunner()) == 0
    for name in ("terraform.tfvars", "toggles.auto.tfvars", "t.auto.tfvars.json"):
        (tree / name).write_text("enable_spanner = true\n")
        assert cli.auto_tfvars() == [name]
        for cmd in ("plan", "apply", "destroy"):
            fake = _FakeRunner()
            with pytest.raises(SystemExit) as e:
                cli.tf(cmd, "my-proj", "yes", "command line", runner=fake)
            assert e.value.code == 2
            assert fake.calls == [], (cmd, name)
            assert (
                f"tf-{cmd}: refused — infra/{name} auto-loads"
                in capsys.readouterr().out
            )
        assert cli.tf("validate", runner=_FakeRunner()) == 0
        (tree / name).unlink()
    assert cli.tf("plan", "my-proj", runner=_FakeRunner()) == 0


def test_cli_missing_terraform_is_a_clean_fail(
    scratch_infra: Path, capsys: pytest.CaptureFixture
) -> None:
    """No traceback when terraform is not on PATH; a real exit 127 still FAILs
    (None sentinel, not 127) (review round 1 #22 / round 2 #16)."""

    def missing(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError(2, "No such file or directory", "terraform")

    assert cli.tf("validate", runner=missing) == 1
    assert "tf-validate FAIL: terraform not on PATH" in capsys.readouterr().out
    assert cli.tf("plan", "my-proj", runner=missing) == 1
    assert "tf-plan FAIL: terraform not on PATH" in capsys.readouterr().out
    # A real exit 127 is reported as the command's own FAIL line (round 3 #10).
    assert cli.tf("plan", "my-proj", runner=_FakeRunner(rc=127)) == 1
    assert "tf-plan FAIL: my-proj" in capsys.readouterr().out


def test_cli_module_runs() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "infra.cli"], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "Traceback" not in res.stderr
