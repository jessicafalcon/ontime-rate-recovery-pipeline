"""`make tf-validate | tf-plan | tf-apply | tf-destroy PROJECT=<id> [CONFIRM=yes]`.

One entry point validates PROJECT (a GCP project-id shape) before deriving the
`-var`, then runs terraform with `-chdir=infra` (a fixed dir, never user input):
tf-validate — offline: `init -backend=false` + `validate` + `fmt -check`. No auth.
tf-plan     — reads GCP APIs (ADC/WIF); shows the diff. Non-destructive.
tf-apply    — creates cloud resources. CONFIRM=yes from the command line only.
tf-destroy  — deletes them. CONFIRM=yes from the command line only.
tf-freeze   — rewrites infra/MANIFEST.sha256 (the content pin over every .tf +
              the provider lock — Amendment P). CONFIRM=yes from the command
              line only: it is the ONLY writer of the manifest.

Auth is ADC (local `gcloud auth application-default login`) or WIF (CI) — no
keyfile, no secret. tf-apply/tf-destroy are cloud-cost/destructive: ask first.

`tf()` takes an injectable `runner` (default `subprocess.run`) so the offline
tests exercise the guards against a fake — a real terraform is never spawned by
`make test`/`make mutate` even if a guard is mutated away (review round 1)."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
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


def require_confirm(cmd: str, confirm: str, origin: str) -> None:
    """Cloud-cost / destructive: CONFIRM=yes must have command-line origin
    ($(origin CONFIRM)); an env-set CONFIRM=yes is refused (CLAUDE.md: ask
    first, every time)."""
    if origin != "command line" or confirm != "yes":
        die(f"tf-{cmd}: refused — pass CONFIRM=yes on the command line")


def pinned_files() -> list[Path]:
    """The files the manifest covers: every `.tf` under infra/ plus the provider
    lock, pruning `.terraform/` (the gitignored provider cache). `*.tfvars`,
    `*.tfstate*` and `*.example` are outside the glob by construction."""
    files = [p for p in INFRA_DIR.rglob("*.tf") if ".terraform" not in p.parts]
    lock = INFRA_DIR / ".terraform.lock.hcl"
    if lock.is_file():
        files.append(lock)
    return sorted(files)


def compute_manifest() -> dict[str, str]:
    return {
        p.relative_to(INFRA_DIR).as_posix(): _manifest.compute_file(p)
        for p in pinned_files()
    }


def manifest_diff() -> list[str]:
    """`<path>: missing|extra|changed` per drifted entry; empty when the tree
    matches the committed manifest (the offline test's assertion)."""
    have = compute_manifest()
    want = _manifest.parse(MANIFEST.read_text()) if MANIFEST.is_file() else {}
    out = []
    for k in sorted(set(have) | set(want)):
        if have.get(k) != want.get(k):
            state = (
                "missing" if k not in have else "extra" if k not in want else "changed"
            )
            out.append(f"{k}: {state}")
    return out


def freeze(confirm: str, origin: str) -> int:
    require_confirm("freeze", confirm, origin)
    m = compute_manifest()
    MANIFEST.write_text(_manifest.render(m))
    print(f"tf-freeze OK: {len(m)} files pinned in {MANIFEST.name}")
    return 0


Runner = Callable[..., subprocess.CompletedProcess]


def _run(runner: Runner, argv: list[str], label: str) -> int | None:
    """Run one terraform argv through the injected runner; returns the exit code,
    or None when the binary is missing (a clean FAIL, already reported — None is a
    distinct sentinel so a real terraform exit 127 still prints its FAIL line)."""
    try:
        return runner(argv).returncode
    except FileNotFoundError:
        print(f"{label} FAIL: terraform not on PATH")
        return None


def tf(
    cmd: str,
    project: str = "",
    confirm: str = "",
    origin: str = "",
    runner: Runner = subprocess.run,
) -> int:
    if cmd in CLOUD_MUTATING:
        require_confirm(cmd, confirm, origin)
    if cmd == "validate":
        for step in (
            _TF + ["init", "-backend=false", "-input=false"],
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
    var = ["-var", f"project_id={project}"]
    argv = {
        "plan": _TF + ["plan", "-input=false", *var],
        "apply": _TF + ["apply", "-input=false", "-auto-approve", *var],
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
        if name in CONFIRM_GATED:
            p.add_argument("--confirm", default="")
            p.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        return tf("validate")
    if a.cmd == "freeze":
        return freeze(a.confirm, a.confirm_origin)
    if a.cmd == "plan":
        return tf("plan", a.project)
    return tf(a.cmd, a.project, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
