"""Phase 9a — the Terraform tree and infra/cli.py, pinned offline. The `.tf`
files are configuration no mutation operator addresses; these static checks +
`terraform validate` (DONE command) + the manual plan/apply/destroy Evidence are
their pins (the Phase 7 treatment of SQL). No terraform binary here, no network,
no cloud — pure file reads and Python.

Review round 1 hardened these: the checks are CONTENT-based and WHOLE-TREE, not
location/substring greps (a role granted from root `main.tf`, a required var with
a nested `validation {}`, or a `default` in a description no longer slips past),
and `cli.tf` is exercised against a FAKE runner so a mutated-away guard can never
spawn a real terraform. infra/cli.py's guards carry the mutation lines."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from infra import cli

ROOT = Path(__file__).parent.parent
INFRA = ROOT / "infra"


def _tf_files() -> list[Path]:
    return sorted(INFRA.rglob("*.tf"))


def _strip_hcl_comments(text: str) -> str:
    """Drop `#`/`//` line comments and `/* */` blocks — a comment that mentions
    `credentials`/`keyfile` (to say there are none) is not configuration."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(#|//).*$", "", line) for line in text.splitlines())


def _brace_body(text: str, open_at: int) -> tuple[str, int]:
    """The text inside the braces whose `{` is at index `open_at`, and the index
    just past the matching `}`."""
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
    """Every brace-matched block whose opening matches `header_re` (ending just
    before the `{`). Brace-matched, so a nested block cannot truncate it."""
    out = []
    for m in re.finditer(header_re + r"\s*\{", text):
        body, _ = _brace_body(text, m.end() - 1)
        out.append(body)
    return out


def _block(text: str, header_re: str) -> str:
    blocks = _blocks(text, header_re)
    assert blocks, header_re
    return blocks[0]


def _variable_blocks(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'variable "([^"]+)"\s*\{', text):
        body, _ = _brace_body(text, m.end() - 1)
        out.append((m.group(1), body))
    return out


def _has_arg(body: str, name: str) -> bool:
    """A top-of-line `name = …` HCL argument assignment (not the word in prose)."""
    return bool(re.search(rf"^\s*{name}\s*=", body, re.M))


# ---------------------------------------------------------------- invariant 1


def test_project_id_is_the_only_required_var() -> None:
    """A variable without a `default =` assignment is required input; from a fresh
    clone only `project_id` may be one (Done-when 1). Brace-matched so a nested
    `validation {}` cannot hide it; keyed on the assignment so a description
    containing the word "default" does not read as one."""
    text = (INFRA / "variables.tf").read_text()
    blocks = _variable_blocks(text)
    assert {n for n, _ in blocks} >= {"project_id", "enable_composer"}, blocks
    required = [name for name, body in blocks if not _has_arg(body, "default")]
    assert required == ["project_id"], required


# ---------------------------------------------------------------- invariant 2


def test_enable_toggles_default_false() -> None:
    text = (INFRA / "variables.tf").read_text()
    for toggle in ("enable_composer", "enable_spanner"):
        body = _block(text, rf'variable "{toggle}"')
        assert re.search(r"^\s*default\s*=\s*false", body, re.M), toggle


def test_optional_modules_are_count_gated() -> None:
    """composer/spanner are created only when their toggle is true; the free-tier
    modules are unconditional (no `count =` gate — a bare substring would trip on
    `billing_account`)."""
    text = (INFRA / "main.tf").read_text()
    for mod in ("composer", "spanner"):
        body = _block(text, rf'module "{mod}"')
        assert re.search(rf"count\s*=\s*var\.enable_{mod} \? 1 : 0", body), mod
    for mod in ("bigquery", "gcs", "iam", "budget"):
        body = _block(text, rf'module "{mod}"')
        assert not re.search(r"^\s*count\s*=", body, re.M), f"{mod} unconditional"


# A resource type that costs money if created. The count-gated composer/spanner
# modules may hold these (they are off by default); nothing ELSE may.
BILLABLE_RESOURCE_TYPES = (
    "google_spanner_instance",
    "google_composer_environment",
    "google_sql_database_instance",
    "google_container_cluster",
    "google_redis_instance",
    "google_dataproc_cluster",
    "google_bigtable_instance",
    "google_compute_instance",
)


def test_no_billable_resource_outside_a_toggled_module() -> None:
    """Invariant 2's teeth: no always-on billable resource anywhere but the
    count-gated composer/spanner modules — so a `google_spanner_instance` dropped
    into root `main.tf` (or a free module) is caught, not just an un-gated
    module call."""
    gated = {INFRA / "modules" / "composer", INFRA / "modules" / "spanner"}
    for f in _tf_files():
        if f.parent in gated:
            continue
        body = _strip_hcl_comments(f.read_text())
        for kind in BILLABLE_RESOURCE_TYPES:
            assert not re.search(rf'resource\s+"{kind}"', body), (f, kind)


# ---------------------------------------------------------------- invariant 3


def _ignored(relpath: str) -> bool:
    return (
        subprocess.run(["git", "check-ignore", "-q", relpath], cwd=ROOT).returncode == 0
    )


def test_no_tracked_secret_state_or_tfvars() -> None:
    """No key / tfstate / real tfvars is tracked; the gitignore rule ignores a
    real terraform.tfvars but not the committed *.example. The key match covers
    gcloud's `<project>-<keyid>.json` too, and any tracked JSON is content-scanned
    for a service-account key body."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bad = [
        p
        for p in tracked
        if re.search(r"\.tfstate", p)
        or p.endswith(".tfvars")
        or (
            p.endswith(".json")
            and re.search(r"(key|credential|service[_-]?account)", p, re.I)
        )
    ]
    assert bad == [], bad
    for p in tracked:
        if p.endswith(".json"):
            body = (ROOT / p).read_text(errors="ignore")
            assert '"type": "service_account"' not in body, p
            assert '"private_key"' not in body, p
    assert (INFRA / "terraform.tfvars.example").exists()
    assert _ignored("infra/terraform.tfvars")
    assert not _ignored("infra/terraform.tfvars.example")


def test_auth_is_adc_or_wif_never_keyfile() -> None:
    """No `.tf` sets a `credentials`/`keyfile` argument (the substring appears in
    the `iamcredentials.googleapis.com` service name — an argument assignment is
    what matters); no `= file(...json)`; the bigquery dbt target is oauth (ADC/WIF)."""
    for f in _tf_files():
        body = _strip_hcl_comments(f.read_text())
        assert not _has_arg(body, "credentials"), f
        assert not _has_arg(body, "keyfile"), f
        assert not re.search(r"=\s*file\([^)]*\.json", body), f
    profiles = (ROOT / "dbt" / "profiles.yml").read_text()
    bq = _yaml_block(profiles, "bigquery")
    assert "method: oauth" in bq
    assert "keyfile" not in bq and "credentials" not in bq


def _yaml_block(text: str, key: str) -> str:
    lines = text.splitlines()
    out, grab, indent = [], False, None
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


# ---------------------------------------------------------------- invariant 4


LEAST_PRIVILEGE_ROLES = {
    "roles/bigquery.jobUser",
    "roles/bigquery.dataEditor",
    "roles/storage.objectAdmin",
    "roles/iam.workloadIdentityUser",
}


def test_sa_roles_are_least_privilege() -> None:
    """Scan EVERY `.tf` (not just modules/iam) for a role grant — a `roles/owner`
    added at root `main.tf` is caught. All grants ⊆ the allowlist; never
    owner/editor."""
    roles: set[str] = set()
    for f in _tf_files():
        roles |= set(re.findall(r'role\s*=\s*"(roles/[^"]+)"', f.read_text()))
    assert roles, "no IAM roles found"
    assert roles <= LEAST_PRIVILEGE_ROLES, roles - LEAST_PRIVILEGE_ROLES
    assert "roles/owner" not in roles and "roles/editor" not in roles


# ---------------------------------------------------------------- invariant 5


def test_every_resource_is_destroyable() -> None:
    """No `prevent_destroy`; the datasets `delete_contents_on_destroy` (so destroy
    works once 9b lands tables) and the bucket `force_destroy` — nothing billable
    survives a teardown (Done-when 5)."""
    for f in _tf_files():
        assert "prevent_destroy" not in _strip_hcl_comments(f.read_text()), f
    bq = _strip_hcl_comments((INFRA / "modules" / "bigquery" / "main.tf").read_text())
    assert len(re.findall(r"delete_contents_on_destroy\s*=\s*true", bq)) == 2, (
        "both datasets"
    )
    gcs = _strip_hcl_comments((INFRA / "modules" / "gcs" / "main.tf").read_text())
    assert re.search(r"force_destroy\s*=\s*true", gcs)


# ---------------------------------------------------------------- WIF scoping (B)


def test_wif_provider_is_ref_scoped() -> None:
    """The WIF provider trusts a specific repo AND ref, not any branch — deleting
    either half of the attribute_condition goes red (review round 1 #2)."""
    body = _block(
        (INFRA / "modules" / "iam" / "main.tf").read_text(),
        r'resource "google_iam_workload_identity_pool_provider" "github"',
    )
    cond = re.search(r"attribute_condition\s*=\s*\"(.+)\"", body)
    assert cond, "no attribute_condition"
    assert "assertion.repository" in cond.group(1)
    assert "assertion.ref" in cond.group(1)


# ---------------------------------------------------------------- API enablement (D)


def test_required_apis_are_enabled() -> None:
    """A fresh project applies: the required services are enabled with
    disable_on_destroy = false (review round 1 #13)."""
    main = (INFRA / "main.tf").read_text()
    assert 'resource "google_project_service" "required"' in main
    assert "disable_on_destroy = false" in main
    for svc in ("bigquery", "storage", "iam", "sts", "billingbudgets"):
        assert f"{svc}.googleapis.com" in main, svc


# ---------------------------------------------------------------- pinned defaults


def test_stated_defaults_are_pinned() -> None:
    """DECISIONS/CLAUDE state $50/$150 and the repo slug as facts; pin the actual
    variable defaults so a silent change is a red test (review round 1 #20)."""
    text = (INFRA / "variables.tf").read_text()
    thresholds = _block(text, r'variable "budget_alert_thresholds_usd"')
    assert re.search(r"default\s*=\s*\[\s*50\s*,\s*150\s*\]", thresholds)
    repo = _block(text, r'variable "github_repository"')
    assert re.search(
        r'default\s*=\s*"jessicafalcon/ontime-rate-recovery-pipeline"', repo
    )
    ref = _block(text, r'variable "github_ref"')
    assert re.search(r'default\s*=\s*"refs/heads/main"', ref)


# ---------------------------------------------------------------- invariant 6 (cli)


class _FakeRunner:
    """A subprocess.run stand-in: records argv, never spawns terraform. Injected
    so a mutated-away guard cannot reach a real `terraform apply` under the sweep."""

    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc)


def test_cli_validates_project() -> None:
    for good in ("my-proj", "my-project-123", "abcdef"):
        assert cli.validate_project(good) == good
    bad_projects = (
        "",
        "../x",
        "A-Bad",
        "ab",
        "proj-",
        "-lead",
        "a b",
        "x" * 40,
        "my-proj\n",
    )
    for bad in bad_projects:
        with pytest.raises(SystemExit) as e:
            cli.validate_project(bad)
        assert e.value.code == 2, bad


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
    # a good confirm passes the guard and reaches the runner (proves it is not
    # over-blocking) — the fake stands in for terraform, so nothing is created
    fake = _FakeRunner()
    assert cli.tf("apply", "my-proj", "yes", "command line", runner=fake) == 0
    assert len(fake.calls) == 1 and "apply" in fake.calls[0]


def test_cli_validates_before_running() -> None:
    """Invariant 6's ordering half: a bad PROJECT dies before the runner is
    called, for every non-validate command (review round 1 #12)."""
    for cmd in ("plan", "apply", "destroy"):
        fake = _FakeRunner()
        with pytest.raises(SystemExit) as e:
            cli.tf(cmd, "../x", "yes", "command line", runner=fake)
        assert e.value.code == 2, cmd
        assert fake.calls == [], cmd


def test_cli_missing_terraform_is_a_clean_fail() -> None:
    """No traceback when terraform is not on PATH (review round 1 #22)."""

    def missing(argv: list[str]) -> subprocess.CompletedProcess:
        raise FileNotFoundError(2, "No such file or directory", "terraform")

    assert cli.tf("validate", runner=missing) == 1
    assert cli.tf("plan", "my-proj", runner=missing) == 1


def test_cli_module_runs() -> None:
    """`python -m infra.cli` with no subcommand exits non-zero (argparse), never a
    stack trace — the entry point is wired."""
    res = subprocess.run(
        [sys.executable, "-m", "infra.cli"], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "Traceback" not in res.stderr
