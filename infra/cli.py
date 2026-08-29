"""`make tf-validate | tf-plan | tf-apply | tf-destroy PROJECT=<id> [CONFIRM=yes]`.

One entry point validates PROJECT (a GCP project-id shape) before deriving the
`-var`, then runs terraform with `-chdir=infra` (a fixed dir, never user input):
tf-validate — offline: `init -backend=false` + `validate` + `fmt -check`. No auth.
tf-plan     — reads GCP APIs (ADC/WIF); shows the diff. Non-destructive.
tf-apply    — creates cloud resources. CONFIRM=yes from the command line only.
tf-destroy  — deletes them. CONFIRM=yes from the command line only.

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

INFRA_DIR = Path(__file__).parent
# GCP project id: 6–30 chars, a lowercase letter first, then lowercase letters /
# digits / hyphens, not ending in a hyphen. `\Z` (not `$`) so a trailing newline
# is rejected.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
# apply/destroy touch the cloud (apply is cost, destroy is destructive); both are
# gated on CONFIRM=yes from the command line.
CLOUD_MUTATING = ("apply", "destroy")
_TF = ["terraform", f"-chdir={INFRA_DIR}"]
_NO_BINARY = 127


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


Runner = Callable[..., subprocess.CompletedProcess]


def _run(runner: Runner, argv: list[str], label: str) -> int:
    """Run one terraform argv through the injected runner; a missing binary is a
    clean FAIL, not a traceback (returns _NO_BINARY, already reported)."""
    try:
        return runner(argv).returncode
    except FileNotFoundError:
        print(f"{label} FAIL: terraform not on PATH")
        return _NO_BINARY


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
            if rc != 0:
                if rc != _NO_BINARY:
                    print(f"tf-validate FAIL: {' '.join(step[2:])}")
                return 1
        print("tf-validate OK")
        return 0
    validate_project(project)
    var = ["-var", f"project_id={project}"]
    argv = {
        "plan": _TF + ["plan", *var],
        "apply": _TF + ["apply", *var, "-auto-approve"],
        "destroy": _TF + ["destroy", *var, "-auto-approve"],
    }[cmd]
    rc = _run(runner, argv, f"tf-{cmd}")
    if rc != 0:
        if rc != _NO_BINARY:
            print(f"tf-{cmd} FAIL: {project}")
        return 1
    print(f"tf-{cmd} OK: {project}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    for name in ("plan", "apply", "destroy"):
        p = sub.add_parser(name)
        p.add_argument("--project", default="")
        if name in CLOUD_MUTATING:
            p.add_argument("--confirm", default="")
            p.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        return tf("validate")
    if a.cmd == "plan":
        return tf("plan", a.project)
    return tf(a.cmd, a.project, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
