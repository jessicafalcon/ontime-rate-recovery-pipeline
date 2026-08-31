"""`make tf-<cmd> [PROJECT=<id>] [CONFIRM=yes]`, cmd one of validate | plan | apply |
destroy | freeze.

One entry point validates PROJECT (a GCP project-id shape) before deriving the
`-var`, then runs terraform with `-chdir=infra` (a fixed dir, never user input):
tf-validate — offline: `init -backend=false -lockfile=readonly` + `validate` +
              `fmt -check`. No auth; the pinned lock is never rewritten.
tf-plan     — reads GCP APIs (ADC/WIF); shows the diff. Non-destructive.
tf-apply    — creates cloud resources. CONFIRM=yes from the command line only.
              Plans FIRST (`plan -out`), reads the plan back (`show -json`) and
              REFUSES a plan that destroys anything unless ALLOW_DESTROY=yes
              also has command-line origin — an apply that omits a toggle
              (`enable_spanner=true` while Spanner is up) would otherwise be a
              silent teardown (Phase 10 review round 2 #3). The saved plan is
              what gets applied: the plan you were shown is the apply you get.
tf-destroy  — deletes them. CONFIRM=yes from the command line only.
tf-freeze   — rewrites infra/MANIFEST.sha256 (the content pin over every file
              Terraform loads — `*.tf`, `*.tf.json` — plus the provider lock;
              Amendments P/R). CONFIRM=yes from the command line only: it is
              the ONLY writer of the manifest.
plan/apply/destroy refuse while an auto-loaded `terraform.tfvars` /
`*.auto.tfvars{,.json}` sits under infra/ (Amendment T): a toggle reaches
Terraform only as a command-line `-var`, never from a gitignored file.

Auth is ADC (local `gcloud auth application-default login`) or WIF (CI) — no
keyfile, no secret. tf-apply/tf-destroy are cloud-cost/destructive: ask first.

`tf()` takes an injectable `runner` (default `subprocess.run`) so the offline
tests exercise the guards against a fake — a real terraform is never spawned by
`make test`/`make mutate` even if a guard is mutated away (review round 1)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from generator import manifest as _manifest

INFRA_DIR = Path(__file__).parent
MANIFEST = INFRA_DIR / _manifest.NAME
# GCP project id: 6–30 chars, a lowercase letter first, then lowercase letters /
# digits / hyphens, not ending in a hyphen. `\Z` (not `$`) so a trailing newline
# is rejected.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
# apply/destroy touch the cloud (apply is cost, destroy is destructive); both are
# gated on CONFIRM=yes from the command line.
CLOUD_MUTATING = ("apply", "destroy")
# tf-freeze overwrites the committed manifest: same gate, same origin rule.
CONFIRM_GATED = CLOUD_MUTATING + ("freeze",)
_TF = ["terraform", f"-chdir={INFRA_DIR}"]


def die(msg: str, code: int = 2) -> NoReturn:
    print(msg)
    sys.exit(code)


def validate_project(project: str) -> str:
    if not PROJECT_RE.match(project):
        die(
            "PROJECT: refused — want a GCP project id "
            f"[a-z][a-z0-9-]{{4,28}}[a-z0-9], got {project!r}"
        )
    return project


def confirmed(confirm: str, origin: str) -> bool:
    """THE rule: CONFIRM=yes with command-line origin (`$(origin CONFIRM)`).
    ONE predicate for every gate — the tf-* targets, loader.cli's cloud
    commands and drop-db, and the integration fixtures' carried gate (round 2
    #7, round 3 #4) — so none can drift from it. It lives beside the keyfile
    policy because loader.cli imports from here (the reverse would cycle)."""
    return origin == "command line" and confirm == "yes"


def require_confirm(cmd: str, confirm: str, origin: str) -> None:
    """Cloud-cost / destructive: CONFIRM=yes must have command-line origin
    ($(origin CONFIRM)); an env-set CONFIRM=yes is refused (CLAUDE.md: ask
    first, every time)."""
    if not confirmed(confirm, origin):
        die(f"tf-{cmd}: refused — pass CONFIRM=yes on the command line")


PINNED_SUFFIXES = (".tf", ".tf.json")


def is_pinned(p: Path) -> bool:
    """The manifest's predicate: every file Terraform loads — `.tf` AND
    `.tf.json` (round 7 #3) — plus the provider lock, pruning `.terraform/`
    (the gitignored provider cache). `*.tfvars`, `*.tfstate*` and `*.example`
    are outside by construction."""
    if ".terraform" in p.relative_to(INFRA_DIR).parts:
        return False
    return p.name.endswith(PINNED_SUFFIXES) or p.name == ".terraform.lock.hcl"


def compute_manifest() -> dict[str, str]:
    return _manifest.compute(INFRA_DIR, is_pinned)


def manifest_diff() -> list[str]:
    """`<path>: missing|extra|changed` per drifted entry via the fixtures'
    `generator.manifest.diff` (one implementation, round 7 #6); empty when the
    tree matches the committed manifest (the offline test's assertion). A
    missing manifest reads as every pinned file `extra`."""
    if not MANIFEST.is_file():
        return [f"{k}: extra" for k in sorted(compute_manifest())]
    return _manifest.diff(INFRA_DIR, MANIFEST, is_pinned)


def freeze(confirm: str, origin: str) -> int:
    """Rewrite the pin from disk — refusing, like `make freeze`, when a path the
    committed manifest lists has vanished (a deleted `.tf` must be an explicit
    delete, never a silent narrowing of the allowlist — round 7 #5)."""
    require_confirm("freeze", confirm, origin)
    m = compute_manifest()
    if MANIFEST.is_file():
        gone = sorted(set(_manifest.parse(MANIFEST.read_text())) - set(m))
        if gone:
            die(
                "tf-freeze: refused — pinned files missing on disk: "
                + ", ".join(gone)
                + " (delete them from the manifest by hand in the same commit)"
            )
    MANIFEST.write_text(_manifest.render(m))
    print(f"tf-freeze OK: {len(m)} files pinned in {MANIFEST.name}")
    return 0


AUTO_TFVARS = re.compile(r"\A(terraform\.tfvars|.*\.auto\.tfvars)(\.json)?\Z")


def auto_tfvars() -> list[str]:
    """Files Terraform loads without being asked (`terraform.tfvars`,
    `*.auto.tfvars`, `*.auto.tfvars.json`) directly under the chdir — gitignored
    and unpinned, so outside every other control (Amendment T)."""
    return sorted(p.name for p in INFRA_DIR.iterdir() if AUTO_TFVARS.match(p.name))


# One item: `name=scalar` (no whitespace, no comma — one item is one `-var`) or
# `name=[n,n,…]` (a bracketed numeric list, the shape of `budget_alert_thresholds`).
VAR_ITEM_RE = re.compile(
    r"^([a-z][a-z0-9_]*)=(?:[^,\s\[\]]+|\[[0-9.]+(?:,[0-9.]+)*\])\Z"
)
# Items are split on a comma that is NOT inside brackets.
_ITEM_SPLIT = re.compile(r",(?![^\[]*\])")


def parse_vars(vars_: str, origin: str = "command line") -> list[str]:
    """`VARS='k=v,k2=[50,150]'` → `['-var', 'k=v', '-var', 'k2=[50,150]']`
    (fix/tf-vars-argv). The ONLY way a toggle reaches Terraform, and only from
    the command line (`$(origin VARS)`, like CONFIRM — an exported VARS would
    toggle a typed apply with nothing in the typed line); `project_id` is
    PROJECT's alone. Empty → no `-var` beyond project_id."""
    if not vars_:
        return []
    if origin != "command line":
        die(f"VARS: refused — set on the command line, not the {origin}")
    out: list[str] = []
    for item in _ITEM_SPLIT.split(vars_):
        m = VAR_ITEM_RE.match(item)
        if not m:
            die(f"VARS: refused — want name=value items joined by ',', got {item!r}")
        if m.group(1) == "project_id":
            die("VARS: refused — project_id comes from PROJECT, not VARS")
        out += ["-var", item]
    return out


# What the terraform child may see (fix/tf-vars-argv): the argv is the whole
# input BY CONSTRUCTION — no TF_VAR_*, TF_CLI_ARGS*, GOOGLE_*CREDENTIALS*,
# TF_WORKSPACE, TF_DATA_DIR or TF_LOG* can reach it. Auth is the ADC file in
# CLOUDSDK_CONFIG / HOME (never a keyfile env var); the rest is process hygiene.
ENV_ALLOW = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "CLOUDSDK_CONFIG",
    "CLOUDSDK_CORE_PROJECT",
    "SSL_CERT_FILE",
    "NO_PROXY",
    "HTTPS_PROXY",
)
ENV_REFUSE_PREFIXES = ("TF_VAR_", "TF_CLI_ARGS")
# A credential in the environment (a key file path, an inline key, a bearer
# token) would silently become the identity of ANY google client — the
# allowlist already keeps it from terraform; every cloud command refuses it
# LOUDLY instead (Phase 10 review round 2 #2): auth is the ADC file, never a
# key. Matched by name FAMILY so a new spelling is caught without a list
# edit: the google provider's `GOOGLE_*CREDENTIALS*`, `GOOGLE_CLOUD_KEYFILE_JSON`
# and `GCLOUD_KEYFILE_JSON`, the two bearer tokens, and gcloud's credential-
# file override (round 3, Amendment L). Not `CLOUDSDK_AUTH_*` wholesale:
# `…_IMPERSONATE_SERVICE_ACCOUNT` is a setting, not a credential.
KEYFILE_ENV_RE = re.compile(
    r"^(GOOGLE_.*CREDENTIALS.*|(GOOGLE|GCLOUD)_.*KEYFILE.*|GOOGLE_OAUTH_ACCESS_TOKEN"
    r"|CLOUDSDK_AUTH_(ACCESS_TOKEN|CREDENTIAL_FILE_OVERRIDE))$"
)


def tf_env() -> dict[str, str]:
    """The allowlisted environment every terraform child gets."""
    return {k: v for k, v in os.environ.items() if k in ENV_ALLOW}


def env_tf_vars() -> list[str]:
    """`TF_VAR_*` / `TF_CLI_ARGS*` names in this process's environment —
    Terraform would read them unasked with nothing in the argv showing it. The
    allowlist already drops them; this names them so the refusal is loud."""
    return sorted(k for k in os.environ if k.startswith(ENV_REFUSE_PREFIXES))


def keyfile_env() -> list[str]:
    return sorted(k for k in os.environ if KEYFILE_ENV_RE.match(k))


def refuse_keyfile_env(what: str) -> None:
    """The ONE keyfile policy for every cloud command (tf-plan/apply/destroy,
    bq-load, spanner-load, dbt-build on a cloud target, the write-back's
    Spanner target, test-int-*): a credential-bearing variable in the
    environment is a refusal, before any client or child exists."""
    found = keyfile_env()
    if found:
        die(
            f"{what}: refused — {', '.join(found)} in the environment would become "
            "the identity of every google client; unset it (auth is ADC, never a key)"
        )


def refuse_env_tf_vars(cmd: str) -> None:
    found = env_tf_vars()
    if found:
        die(
            f"tf-{cmd}: refused — {', '.join(found)} in the environment would reach "
            "Terraform unseen; unset it and pass VARS='name=value,…' instead"
        )


def refuse_auto_tfvars(cmd: str) -> None:
    found = auto_tfvars()
    if found:
        die(
            f"tf-{cmd}: refused — infra/{', infra/'.join(found)} auto-loads; "
            "delete it and pass VARS='name=value,…' on the command line (Amendment T)"
        )


Runner = Callable[..., subprocess.CompletedProcess]


def planned_deletes(show_json: str) -> list[str]:
    """Resource addresses a saved plan would delete (`terraform show -json`'s
    `resource_changes[].change.actions` containing `delete` — a replace
    counts: it is a delete). Empty ONLY for a parsed plan whose list has no
    delete; a body that cannot be read — empty, not JSON, not an object, no
    `resource_changes` list — is a refusal, never "no deletes" (round 3,
    Amendment K: the gate runs on evidence, not on absence)."""
    try:
        plan = json.loads(show_json)
    except ValueError as e:  # JSONDecodeError; an empty body lands here too
        die(
            "tf-apply: refused — the saved plan could not be read back "
            f"({e}); nothing applied"
        )
    changes = plan.get("resource_changes") if isinstance(plan, dict) else None
    if not isinstance(changes, list):
        die(
            "tf-apply: refused — the saved plan could not be read back "
            "(show -json has no resource_changes list); nothing applied"
        )
    return sorted(
        rc["address"]
        for rc in changes
        if "delete" in rc.get("change", {}).get("actions", [])
    )


def require_allow_destroy(deletes: list[str], allow: str, origin: str) -> None:
    """A plan with destroys applies only with ALLOW_DESTROY=yes from the
    command line ($(origin ALLOW_DESTROY), like CONFIRM): the toggle-flip
    teardown says so explicitly; an apply that merely forgot a toggle does not."""
    if not deletes:
        return
    if origin == "command line" and allow == "yes":
        return
    die(
        "tf-apply: refused — the plan destroys "
        + ", ".join(deletes)
        + "; if that is intended (the toggle-flip teardown), re-run with "
        "ALLOW_DESTROY=yes on the command line; if not, the VARS you passed "
        "omit a toggle that is currently applied"
    )


def _run(runner: Runner, argv: list[str], label: str) -> int | None:
    """Run one terraform argv through the injected runner; returns the exit code,
    or None when the binary is missing (a clean FAIL, already reported — None is a
    distinct sentinel so a real terraform exit 127 still prints its FAIL line)."""
    try:
        return runner(argv, env=tf_env()).returncode
    except FileNotFoundError:
        print(f"{label} FAIL: terraform not on PATH")
        return None


def _apply(
    runner: Runner, var: list[str], project: str, allow: str, allow_origin: str
) -> int:
    """plan -out → show -json → (refuse on destroys unless allowed) → apply
    <planfile>. The plan file holds variable values, so it lives in TMPDIR
    and is removed on every path."""
    fd, plan_path = tempfile.mkstemp(prefix="tfplan-", suffix=".bin")
    os.close(fd)
    try:
        rc = _run(
            runner,
            _TF + ["plan", "-input=false", f"-out={plan_path}", *var],
            "tf-apply",
        )
        if rc is None:
            return 1
        if rc != 0:
            print(f"tf-apply FAIL: {project} (plan)")
            return 1
        try:
            shown = runner(
                _TF + ["show", "-json", plan_path],
                env=tf_env(),
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            print("tf-apply FAIL: terraform not on PATH")
            return 1
        if shown.returncode != 0:
            print(f"tf-apply FAIL: {project} (show)")
            return 1
        require_allow_destroy(planned_deletes(shown.stdout), allow, allow_origin)
        rc = _run(runner, _TF + ["apply", "-input=false", plan_path], "tf-apply")
        if rc is None:
            return 1
        if rc != 0:
            print(f"tf-apply FAIL: {project}")
            return 1
        print(f"tf-apply OK: {project}")
        return 0
    finally:
        if os.path.exists(plan_path):
            os.unlink(plan_path)


def tf(
    cmd: str,
    project: str = "",
    confirm: str = "",
    origin: str = "",
    runner: Runner = subprocess.run,
    vars_: str = "",
    vars_origin: str = "command line",
    allow_destroy: str = "",
    allow_destroy_origin: str = "",
) -> int:
    if cmd in CLOUD_MUTATING:
        require_confirm(cmd, confirm, origin)
    refuse_env_tf_vars(cmd)  # every command, validate too (it evaluates validations)
    if cmd == "validate":
        for step in (
            _TF + ["init", "-backend=false", "-input=false", "-lockfile=readonly"],
            _TF + ["validate"],
            _TF + ["fmt", "-check", "-recursive"],
        ):
            rc = _run(runner, step, "tf-validate")
            if rc is None:
                return 1  # missing binary — already reported
            if rc != 0:
                print(f"tf-validate FAIL: {' '.join(step[2:])}")
                return 1
        print("tf-validate OK")
        return 0
    validate_project(project)
    refuse_auto_tfvars(cmd)
    refuse_keyfile_env(f"tf-{cmd}")
    var = ["-var", f"project_id={project}", *parse_vars(vars_, vars_origin)]
    if cmd == "apply":
        return _apply(runner, var, project, allow_destroy, allow_destroy_origin)
    argv = {
        "plan": _TF + ["plan", "-input=false", *var],
        "destroy": _TF + ["destroy", "-input=false", "-auto-approve", *var],
    }[cmd]
    rc = _run(runner, argv, f"tf-{cmd}")
    if rc is None:
        return 1  # missing binary — already reported
    if rc != 0:
        print(f"tf-{cmd} FAIL: {project}")
        return 1
    print(f"tf-{cmd} OK: {project}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    for name in ("plan", "apply", "destroy", "freeze"):
        p = sub.add_parser(name)
        if name != "freeze":
            p.add_argument("--project", default="")
            p.add_argument("--vars", default="")
            p.add_argument("--vars-origin", default="")
        if name in CONFIRM_GATED:
            p.add_argument("--confirm", default="")
            p.add_argument("--confirm-origin", default="")
        if name == "apply":
            p.add_argument("--allow-destroy", default="")
            p.add_argument("--allow-destroy-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        return tf("validate")
    if a.cmd == "freeze":
        return freeze(a.confirm, a.confirm_origin)
    if a.cmd == "plan":
        return tf("plan", a.project, vars_=a.vars, vars_origin=a.vars_origin)
    if a.cmd == "apply":
        return tf(
            "apply",
            a.project,
            a.confirm,
            a.confirm_origin,
            vars_=a.vars,
            vars_origin=a.vars_origin,
            allow_destroy=a.allow_destroy,
            allow_destroy_origin=a.allow_destroy_origin,
        )
    return tf(
        "destroy",
        a.project,
        a.confirm,
        a.confirm_origin,
        vars_=a.vars,
        vars_origin=a.vars_origin,
    )


if __name__ == "__main__":
    sys.exit(main())
