"""`make tf-validate | tf-plan | tf-apply | tf-destroy PROJECT=<id> [CONFIRM=yes]`.

One entry point validates PROJECT (a GCP project-id shape) before deriving the
`-var`, then runs terraform with `-chdir=infra` (a fixed dir, never user input):
tf-validate — offline: `init -backend=false` + `validate` + `fmt -check`. No auth.
tf-plan     — reads GCP APIs (ADC/WIF); shows the diff. Non-destructive.
tf-apply    — creates cloud resources. CONFIRM=yes from the command line only.
tf-destroy  — deletes them. CONFIRM=yes from the command line only.

Auth is ADC (local `gcloud auth application-default login`) or WIF (CI) — no
keyfile, no secret. tf-apply/tf-destroy are cloud-cost/destructive: ask first."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).parent.parent
INFRA_DIR = ROOT / "infra"
# GCP project id: 6–30 chars, a lowercase letter first, then lowercase letters /
# digits / hyphens, not ending in a hyphen.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
DESTRUCTIVE = ("apply", "destroy")
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


def tf(cmd: str, project: str = "", confirm: str = "", origin: str = "") -> int:
    if cmd in DESTRUCTIVE:
        require_confirm(cmd, confirm, origin)
    if cmd == "validate":
        steps = [
            _TF + ["init", "-backend=false", "-input=false"],
            _TF + ["validate"],
            _TF + ["fmt", "-check", "-recursive"],
        ]
        for step in steps:
            if subprocess.run(step).returncode != 0:
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
    if subprocess.run(argv).returncode != 0:
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
        if name in DESTRUCTIVE:
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
