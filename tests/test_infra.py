"""Phase 9a — the Terraform tree and infra/cli.py, pinned offline. The `.tf`
files are configuration no mutation operator addresses; these static checks +
`terraform validate` (DONE command) + the manual plan/apply/destroy Evidence are
their pins (the Phase 7 treatment of SQL). No terraform binary here, no network,
no cloud — pure file reads and Python. infra/cli.py's guards get the mutation
lines."""

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
    lines = []
    for line in text.splitlines():
        line = re.sub(r"(#|//).*$", "", line)
        lines.append(line)
    return "\n".join(lines)


def _block(text: str, header_re: str) -> str:
    """The brace-matched body of the first block whose opening line matches
    `header_re` (which must end just before the `{`)."""
    m = re.search(header_re + r"\s*\{", text)
    assert m, header_re
    depth, start = 0, m.end() - 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    raise AssertionError(f"unbalanced braces after {header_re}")


# ---------------------------------------------------------------- invariant 1


def test_project_id_is_the_only_required_var() -> None:
    """A variable without a `default` is required input; from a fresh clone only
    `project_id` may be one (Done-when 1)."""
    text = (INFRA / "variables.tf").read_text()
    blocks = re.findall(r'variable "([^"]+)"\s*\{([^{}]*)\}', text)
    assert blocks, "no variable blocks parsed"
    required = [name for name, body in blocks if "default" not in body]
    assert required == ["project_id"], required


# ---------------------------------------------------------------- invariant 2


def test_enable_toggles_default_false() -> None:
    text = (INFRA / "variables.tf").read_text()
    for toggle in ("enable_composer", "enable_spanner"):
        body = _block(text, rf'variable "{toggle}"')
        assert "default     = false" in body or "default = false" in body, toggle


def test_optional_modules_are_count_gated() -> None:
    """composer/spanner are created only when their toggle is true, so a default
    plan makes zero of them."""
    text = (INFRA / "main.tf").read_text()
    for mod in ("composer", "spanner"):
        body = _block(text, rf'module "{mod}"')
        assert re.search(rf"count\s*=\s*var\.enable_{mod} \? 1 : 0", body), mod
    # and the free-tier modules are unconditional (no `count =` gate; a bare
    # substring match would trip on `billing_account`)
    for mod in ("bigquery", "gcs", "iam", "budget"):
        body = _block(text, rf'module "{mod}"')
        assert not re.search(r"^\s*count\s*=", body, re.M), f"{mod} unconditional"


# ---------------------------------------------------------------- invariant 3


def test_no_tracked_secret_state_or_tfvars() -> None:
    """No key / tfstate / real tfvars is tracked (only *.tfvars.example)."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bad = [
        p
        for p in tracked
        if re.search(r"\.tfstate", p)
        or (p.endswith(".tfvars"))
        or re.search(r"(service[_-]?account|credentials|.*key)\.json$", p)
    ]
    assert bad == [], bad
    # the gitignore rule itself: a real terraform.tfvars is ignored, the
    # committed *.example is not (independent of what is currently staged).
    assert (INFRA / "terraform.tfvars.example").exists()
    assert _ignored("infra/terraform.tfvars")
    assert not _ignored("infra/terraform.tfvars.example")


def _ignored(relpath: str) -> bool:
    return (
        subprocess.run(["git", "check-ignore", "-q", relpath], cwd=ROOT).returncode == 0
    )


def test_auth_is_adc_or_wif_never_keyfile() -> None:
    """No .tf or profiles.yml names a keyfile/credentials path; the bigquery dbt
    target authenticates by oauth (ADC/WIF)."""
    for f in _tf_files():
        body = _strip_hcl_comments(f.read_text())
        assert "credentials" not in body, f
        assert "keyfile" not in body, f
        assert not re.search(r"key\s*=\s*.*\.json", body), f
    profiles = (ROOT / "dbt" / "profiles.yml").read_text()
    bq = _yaml_block(profiles, "bigquery")
    assert "method: oauth" in bq
    assert "keyfile" not in bq and "credentials" not in bq


def _yaml_block(text: str, key: str) -> str:
    """The indented body under `    <key>:` up to the next same-indent key."""
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
    text = (INFRA / "modules" / "iam" / "main.tf").read_text()
    roles = set(re.findall(r'role\s*=\s*"(roles/[^"]+)"', text))
    assert roles, "no IAM roles found"
    assert roles <= LEAST_PRIVILEGE_ROLES, roles - LEAST_PRIVILEGE_ROLES
    assert "roles/owner" not in roles and "roles/editor" not in roles


# ---------------------------------------------------------------- invariant 5


def test_every_module_resource_is_destroyable() -> None:
    """No `prevent_destroy` blocks a teardown; the GCS bucket force_destroys so
    tf-destroy removes it even with objects (Done-when 5)."""
    for f in _tf_files():
        assert "prevent_destroy" not in f.read_text(), f
    gcs = (INFRA / "modules" / "gcs" / "main.tf").read_text()
    assert "force_destroy               = true" in gcs or "force_destroy = true" in gcs


# ---------------------------------------------------------------- invariant 6


def test_cli_validates_project() -> None:
    for good in ("my-proj", "my-project-123", "abcdef"):
        assert cli.validate_project(good) == good
    for bad in ("", "../x", "A-Bad", "ab", "proj-", "-lead", "a b", "x" * 40):
        with pytest.raises(SystemExit) as e:
            cli.validate_project(bad)
        assert e.value.code == 2, bad


def test_cli_requires_confirm_origin() -> None:
    """tf-apply/tf-destroy refuse unless CONFIRM=yes has command-line origin —
    the guard runs before any terraform is spawned."""
    for cmd in ("apply", "destroy"):
        for confirm, origin in (
            ("yes", "environment"),
            ("yes", "file"),
            ("", "command line"),
            ("no", "command line"),
        ):
            with pytest.raises(SystemExit) as e:
                cli.tf(cmd, "my-proj", confirm, origin)
            assert e.value.code == 2, (cmd, confirm, origin)


def test_cli_module_runs() -> None:
    """`python -m infra.cli` with no subcommand exits non-zero (argparse), never
    a stack trace — the entry point is wired."""
    res = subprocess.run(
        [sys.executable, "-m", "infra.cli"], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "Traceback" not in res.stderr
