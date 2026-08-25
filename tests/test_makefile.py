"""Pins for the Makefile's variable handling (spec Threat model; invariant
"trusted origin"). `make -n` prints the recipe without running it, so these
tests exercise `$(value)` + `_Q` + `unexport` on the REAL Makefile, from both
origins, without spawning the gate. Offline, no services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRUB = (
    "SPEC",
    "BASE",
    "DELETED",
    "CONFIRM",
    "PROFILE",
    "TARGET",
    "MAKEFLAGS",
    "MFLAGS",
)


def _make_n(target: str, cmdline: dict[str, str], env: dict[str, str]) -> str:
    base = {k: v for k, v in os.environ.items() if k not in SCRUB}
    res = subprocess.run(
        ["make", "-n", target, *(f"{k}={v}" for k, v in cmdline.items())],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**base, **env},
        check=True,
    )
    return res.stdout


@pytest.mark.parametrize(
    "value",
    ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""],
)
def test_user_variable_reaches_python_as_one_literal_from_both_origins(value: str):
    """Whatever the origin, the recipe carries the UNEXPANDED value as one
    single-quoted token (`'` → `'\\''`) — no shell, no make function runs."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        out = _make_n(
            "mutate",
            {"SPEC": value} if origin == "cmdline" else {},
            {"SPEC": value} if origin == "env" else {},
        )
        assert f"--spec {quoted}" in out, (origin, out)
        assert "pwned" not in out.replace(value, "")  # nothing expanded or ran


def test_env_exported_spec_reaches_the_recipe_and_is_validated_in_python():
    """`unexport` does NOT keep an environment value out of the recipe (that
    was the falsified claim); it only strips the child env. Python is the guard."""
    out = _make_n("review-gate", {}, {"SPEC": "../x"})
    assert "--spec '../x'" in out
    # and the child environment does not carry it: `$(value)` is the only path
    probe = "include Makefile\nprobe:\n\t@echo SPEC_IN_ENV=$${SPEC-unset}\n"
    res = subprocess.run(
        ["make", "-n", "-f", "-", "probe"],
        cwd=ROOT,
        input=probe,
        capture_output=True,
        text=True,
        env={**{k: v for k, v in os.environ.items() if k not in SCRUB}, "SPEC": "x"},
        check=True,
    )
    assert "SPEC_IN_ENV=" in res.stdout  # recipe exists; the runtime value is
    # verified by running it for real (no gate spawned: the probe only echoes)
    res = subprocess.run(
        ["make", "-s", "-f", "-", "probe"],
        cwd=ROOT,
        input=probe,
        capture_output=True,
        text=True,
        env={**{k: v for k, v in os.environ.items() if k not in SCRUB}, "SPEC": "x"},
        check=True,
    )
    assert res.stdout.strip() == "SPEC_IN_ENV=unset"


def test_base_defaults_to_main_and_deleted_is_optional():
    out = _make_n("review-gate", {}, {})
    assert "--base 'main'" in out
    assert "--deleted" not in out and "--spec" not in out


# ------------------------------------------------------- Phase 1: seed, freeze


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_profile_reaches_python_as_one_literal_from_both_origins(value: str):
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target in ("seed", "freeze"):
        for origin in ("cmdline", "env"):
            out = _make_n(
                target,
                {"PROFILE": value} if origin == "cmdline" else {},
                {"PROFILE": value} if origin == "env" else {},
            )
            assert f"generator.cli {target} {quoted}" in out, (target, origin, out)
            assert "pwned" not in out.replace(value, "")


def test_freeze_requires_confirm_from_the_command_line() -> None:
    """The recipe passes `$(origin CONFIRM)` verbatim; Python accepts only
    `command line` + `yes`. An exported CONFIRM=yes is refused, no write."""
    from generator import cli

    out = _make_n("freeze", {"PROFILE": "tiny", "CONFIRM": "yes"}, {})
    assert "--confirm 'yes' --confirm-origin 'command line'" in out
    out = _make_n("freeze", {"PROFILE": "tiny"}, {"CONFIRM": "yes"})
    assert "--confirm 'yes' --confirm-origin 'environment'" in out
    out = _make_n("freeze", {"PROFILE": "tiny"}, {})
    # Gotcha (ARCHITECTURE §8): `unexport CONFIRM` counts as a file definition,
    # so an unset CONFIRM has origin `file`, not `undefined`. Refused either way.
    assert "--confirm '' --confirm-origin 'file'" in out
    for confirm, origin in (
        ("yes", "environment"),
        ("", "undefined"),
        ("no", "command line"),
    ):
        with pytest.raises(SystemExit) as e:
            cli.freeze("tiny", confirm, origin)
        assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.freeze("../x", "yes", "command line")
    assert e.value.code == 2
