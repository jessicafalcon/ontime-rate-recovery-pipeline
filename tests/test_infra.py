"""Phase 9a — the Terraform tree and infra/cli.py, pinned offline. The `.tf`
files are configuration no mutation operator addresses; these static checks +
`terraform validate` (DONE command) + the manual plan/apply/destroy Evidence are
their pins (the Phase 7 treatment of SQL). No terraform binary here, no network,
no cloud — pure file reads and Python.

Review round 2 re-implemented these against ONE invariant (cap invoked): every
property named in the Invariants table has a test that reddens when the property
is removed from the `.tf` by a hand-mutation. Pins are exact-string / scoped /
allowlist — never a substring or a resource-type denylist. `infra/cli.py`'s
guards carry the mutation lines; `cli.tf` runs through a fake runner so no test
spawns terraform, and the argv it builds is asserted."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from infra import cli

ROOT = Path(__file__).parent.parent
INFRA = ROOT / "infra"
GATED_MODULE_DIRS = {INFRA / "modules" / "composer", INFRA / "modules" / "spanner"}


def _tf_files() -> list[Path]:
    """Every committed `.tf` — pruning `.terraform/` (the gitignored provider /
    vendored-module cache `tf-validate` creates), the `BUILD_DIRS` precedent."""
    return sorted(p for p in INFRA.rglob("*.tf") if ".terraform" not in p.parts)


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


# Every `data` source type the tree may read (round 4 #7): a data source is a
# live API call at plan/apply, so a new one is a conscious addition too.
ALLOWED_DATA_SOURCE_TYPES = {"google_project", "google_billing_account"}


def test_every_data_source_type_is_on_the_allowlist() -> None:
    declared: set[str] = set()
    for f in _tf_files():
        if f.parent in GATED_MODULE_DIRS:
            continue
        declared |= set(
            re.findall(
                r'\bdata\s+"([a-z0-9_]+)"\s+"', _strip_hcl_comments(f.read_text())
            )
        )
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


def test_every_declared_resource_type_is_on_the_allowlist() -> None:
    """Invariant 2's teeth as an allowlist: no resource type outside the
    count-gated modules but the expected set — a `google_spanner_instance` /
    `google_cloud_run_v2_service` / `google_dataflow_job` at root is caught."""
    declared: set[str] = set()
    for f in _tf_files():
        if f.parent in GATED_MODULE_DIRS:
            continue
        # ANY provider's resource type (round 3 #6): a `null_resource` running a
        # local-exec, or a `random_*`, is off-allowlist too.
        declared |= set(
            re.findall(r'resource\s+"([a-z0-9_]+)"', _strip_hcl_comments(f.read_text()))
        )
    assert declared, "no resources declared"
    assert declared <= ALLOWED_RESOURCE_TYPES, declared - ALLOWED_RESOURCE_TYPES


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
    """Amendment J (round 4 #2/#5): the provider name is a ROOT output, and both
    the module and root outputs are `enable_ci_wif ? …[0].name : null` — an
    unguarded `[0]` index is "Invalid index … empty tuple" on every default plan
    (the tester's surviving hand-mutation; `terraform validate` does not catch
    it)."""
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


# Keys of a Claude Code settings file that configure behaviour for whoever opens
# the checkout (hooks run commands; permissions/env/MCP pre-authorise them).
_AUTO_CONFIGURING_KEYS = frozenset(
    {"hooks", "permissions", "env", "mcpServers", "enableAllProjectMcpServers"}
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


def _ignored(relpath: str) -> bool:
    return (
        subprocess.run(["git", "check-ignore", "-q", relpath], cwd=ROOT).returncode == 0
    )


PRIVATE_KEY_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".p8")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".duckdb", ".pyc", ".ico"}


def test_no_tracked_secret_state_or_tfvars() -> None:
    """No key / tfstate / real tfvars is tracked; the gitignore rule ignores a
    real terraform.tfvars but not the *.example. Private-key filetypes (.pem/.p12/
    …) are rejected by extension; any tracked JSON is content-scanned for a
    SA-key body."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
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
    assert _ignored("infra/terraform.tfvars")
    # Terraform auto-loads *.auto.tfvars and *.tfvars.json too (round 3 #12).
    for auto in ("terraform.tfvars.json", "x.auto.tfvars", "x.auto.tfvars.json"):
        assert _ignored(f"infra/{auto}"), auto
    assert not _ignored("infra/terraform.tfvars.example")


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

LEAST_PRIVILEGE_ROLES = {
    "roles/bigquery.jobUser",
    "roles/bigquery.dataEditor",
    "roles/storage.objectAdmin",
    "roles/iam.workloadIdentityUser",
}


def test_sa_roles_are_least_privilege() -> None:
    """Every role grant in ANY `.tf` ⊆ the allowlist; never owner/editor."""
    roles: set[str] = set()
    for f in _tf_files():
        roles |= set(re.findall(r'role\s*=\s*"(roles/[^"]+)"', f.read_text()))
    assert roles, "no IAM roles found"
    assert roles <= LEAST_PRIVILEGE_ROLES, roles - LEAST_PRIVILEGE_ROLES
    assert "roles/owner" not in roles and "roles/editor" not in roles


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


def test_no_tracked_auto_configuring_claude_settings() -> None:
    """Amendment M (round 4 #9): hook wiring lives only in the gitignored
    `.claude/settings.local.json` — a committed settings file would auto-execute
    an inbound branch's hook for anyone opening it (CLAUDE.md § Project tooling).
    `.claude/settings.json` is untracked and ignored; any tracked settings file
    carries no auto-configuring key — hooks, permissions, env, MCP servers
    (round 5 #12 widened this from `hooks` alone) — and no `.mcp.json` is
    tracked."""
    tracked = _git_ls_files(".claude")
    settings = [p for p in tracked if re.search(r"^\.claude/settings[^/]*\.json$", p)]
    for p in settings:
        keys = set(json.loads((ROOT / p).read_text()))
        assert not keys & _AUTO_CONFIGURING_KEYS, (p, keys)
    assert not _git_ls_files(".mcp.json")
    assert _ignored(".claude/settings.json")
    assert _ignored(".claude/settings.local.json")


# ---------------------------------------------------------------- invariant 6 (cli)


class _FakeRunner:
    """A subprocess.run stand-in: records argv, never spawns terraform."""

    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc)


def test_cli_validates_project() -> None:
    for good in ("my-proj", "my-project-123", "abcdef"):
        assert cli.validate_project(good) == good
    bad = ("", "../x", "A-Bad", "ab", "proj-", "-lead", "a b", "x" * 40, "my-proj\n")
    for value in bad:
        with pytest.raises(SystemExit) as e:
            cli.validate_project(value)
        assert e.value.code == 2, value


def test_cli_requires_confirm_origin() -> None:
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
    assert len(fake.calls) == 1 and "apply" in fake.calls[0]


def test_cli_validates_before_running() -> None:
    """Invariant 6's ordering half: a bad PROJECT dies before the runner runs."""
    for cmd in ("plan", "apply", "destroy"):
        fake = _FakeRunner()
        with pytest.raises(SystemExit) as e:
            cli.tf(cmd, "../x", "yes", "command line", runner=fake)
        assert e.value.code == 2, cmd
        assert fake.calls == [], cmd


def test_cli_builds_the_expected_argv() -> None:
    """Round 2 #11: the argv reaching the runner carries the validated
    `-var project_id=…` and `-input=false` (no interactive prompt), and the
    mutating commands `-auto-approve` — dropping any reddens."""
    fake = _FakeRunner()
    assert cli.tf("plan", "my-proj", runner=fake) == 0
    plan = fake.calls[0]
    assert "-var" in plan and "project_id=my-proj" in plan and "-input=false" in plan
    assert "-auto-approve" not in plan
    for cmd in ("apply", "destroy"):
        fake = _FakeRunner()
        assert cli.tf(cmd, "my-proj", "yes", "command line", runner=fake) == 0
        argv = fake.calls[0]
        assert (
            "project_id=my-proj" in argv
            and "-input=false" in argv
            and "-auto-approve" in argv
        )


def test_cli_validate_argv_is_offline() -> None:
    """Round 3 #5 (round 2 #9's live survivor): `tf-validate` runs init with
    `-backend=false` (no state backend touched, no auth) then validate, then
    fmt -check — dropping the flag or a step reddens."""
    fake = _FakeRunner()
    assert cli.tf("validate", runner=fake) == 0
    steps = [argv[2:] for argv in fake.calls]
    assert steps == [
        ["init", "-backend=false", "-input=false"],
        ["validate"],
        ["fmt", "-check", "-recursive"],
    ], steps


def test_cli_missing_terraform_is_a_clean_fail(capsys: pytest.CaptureFixture) -> None:
    """No traceback when terraform is not on PATH; a real exit 127 still FAILs
    (None sentinel, not 127) (review round 1 #22 / round 2 #16)."""

    def missing(argv: list[str]) -> subprocess.CompletedProcess:
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
