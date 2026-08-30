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
    "WRITE",
    "THROUGH",
    "FULL",
    "PROJECT",
    "VARS",
    "MAKEFLAGS",
    "MFLAGS",
)


def _make_n(target: str, cmdline: dict[str, str], env: dict[str, str]) -> str:
    base = {
        k: v
        for k, v in os.environ.items()
        if k not in SCRUB and not k.startswith(("TF_VAR_", "TF_CLI_ARGS"))
    }
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


# ------------------------------------------------- Phase 2: load, dbt-build, drop-db


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_load_and_dbt_build_pass_profile_and_target_as_one_literal(value: str):
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target in ("load", "dbt-build", "drop-db"):
        for origin in ("cmdline", "env"):
            kv = {"PROFILE": value, "TARGET": value}
            out = _make_n(
                target, kv if origin == "cmdline" else {}, kv if origin == "env" else {}
            )
            assert f"loader.cli {target} {quoted}" in out, (target, origin, out)
            if target == "dbt-build":
                assert f"--target {quoted}" in out
            assert "pwned" not in out.replace(value, "")
    out = _make_n("dbt-build", {"PROFILE": "tiny"}, {})
    assert "--target '' --confirm '' --confirm-origin 'file'" in out  # → duckdb
    out = _make_n(
        "dbt-build", {"PROFILE": "tiny", "TARGET": "bigquery"}, {"CONFIRM": "yes"}
    )
    assert "--target 'bigquery' --confirm 'yes' --confirm-origin 'environment'" in out
    out = _make_n(
        "dbt-build", {"PROFILE": "tiny", "TARGET": "bigquery", "CONFIRM": "yes"}, {}
    )
    assert "--confirm 'yes' --confirm-origin 'command line'" in out


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_load_passes_through_as_one_literal(value: str) -> None:
    """Phase 7: THROUGH reaches Python as one single-quoted token from either
    origin; Python validates it as an upload date and never derives a path."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        out = _make_n(
            "load",
            {"THROUGH": value} if origin == "cmdline" else {"PROFILE": "tiny"},
            {"THROUGH": value} if origin == "env" else {},
        )
        assert f"--through {quoted}" in out, (origin, out)
        assert "pwned" not in out.replace(value, "")


def test_dbt_build_full_refresh_from_command_line_only() -> None:
    """Phase 7: the recipe passes $(origin FULL) verbatim; Python adds
    --full-refresh only for FULL=yes from the command line."""
    out = _make_n("dbt-build", {"PROFILE": "tiny", "FULL": "yes"}, {})
    assert "--full 'yes' --full-origin 'command line'" in out
    out = _make_n("dbt-build", {"PROFILE": "tiny"}, {"FULL": "yes"})
    assert "--full 'yes' --full-origin 'environment'" in out
    out = _make_n("dbt-build", {"PROFILE": "tiny"}, {})
    assert "--full '' --full-origin 'file'" in out


def test_drop_db_requires_confirm_from_the_command_line() -> None:
    out = _make_n("drop-db", {"PROFILE": "tiny", "CONFIRM": "yes"}, {})
    assert "--confirm 'yes' --confirm-origin 'command line'" in out
    out = _make_n("drop-db", {"PROFILE": "tiny"}, {"CONFIRM": "yes"})
    assert "--confirm 'yes' --confirm-origin 'environment'" in out
    out = _make_n("drop-db", {"PROFILE": "tiny"}, {})
    assert "--confirm '' --confirm-origin 'file'" in out


# ------------------------------------------------- Phase 3: attribution-golden, eval


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_golden_and_eval_pass_profile_as_one_literal(value: str) -> None:
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target, cmd in (("attribution-golden", "golden"), ("eval", "score")):
        for origin in ("cmdline", "env"):
            kv = {"PROFILE": value, "WRITE": value}
            out = _make_n(
                target, kv if origin == "cmdline" else {}, kv if origin == "env" else {}
            )
            assert f"eval.cli {cmd} {quoted}" in out, (target, origin, out)
            if target == "attribution-golden":
                assert (
                    f"--write {quoted}" in out
                )  # env-exported WRITE reaches Python (stated residual)
            assert "pwned" not in out.replace(value, "")
    out = _make_n("attribution-golden", {"PROFILE": "tiny"}, {})
    assert "eval.cli golden 'tiny' --write ''" in out


# ------------------------------------------------- Phase 4: report


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_report_passes_profile_as_one_literal(value: str) -> None:
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        kv = {"PROFILE": value, "WRITE": value}
        out = _make_n(
            "report", kv if origin == "cmdline" else {}, kv if origin == "env" else {}
        )
        assert f"eval.cli report {quoted}" in out, (origin, out)
        assert (
            f"--write {quoted}" in out
        )  # env-exported WRITE reaches Python (stated residual)


# ------------------------------------------------- Phase 5: scores-golden


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_scores_golden_passes_profile_as_one_literal(value: str) -> None:
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        kv = {"PROFILE": value, "WRITE": value}
        out = _make_n(
            "scores-golden",
            kv if origin == "cmdline" else {},
            kv if origin == "env" else {},
        )
        assert f"eval.cli scores-golden {quoted}" in out, (origin, out)
        assert f"--write {quoted}" in out  # env-exported WRITE: stated residual
        assert "pwned" not in out.replace(value, "")


@pytest.mark.parametrize(
    "value",
    ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""],
)
def test_simulate_passes_profile_as_one_literal(value: str) -> None:
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        kv = {"PROFILE": value, "WRITE": value}
        out = _make_n(
            "simulate",
            kv if origin == "cmdline" else {},
            kv if origin == "env" else {},
        )
        assert f"eval.cli simulate {quoted}" in out, (origin, out)
        assert f"--write {quoted}" in out  # env-exported WRITE: stated residual
        assert "pwned" not in out.replace(value, "")


@pytest.mark.parametrize(
    "value",
    ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""],
)
def test_power_passes_write_as_one_literal(value: str) -> None:
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        kv = {"WRITE": value}
        out = _make_n(
            "power", kv if origin == "cmdline" else {}, kv if origin == "env" else {}
        )
        assert f"eval.cli power --write {quoted}" in out, (origin, out)
        assert "pwned" not in out.replace(value, "")


# ------------------------------------------------- Phase 8a: writeback, pipeline


@pytest.mark.parametrize(
    "value",
    ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""],
)
def test_writeback_and_pipeline_pass_profile_as_one_literal(value: str) -> None:
    """Phase 8a (amended Phase 10): PROFILE reaches Python as one single-quoted
    token from either origin. `pipeline` takes no CONFIRM (non-destructive on
    the local target); `writeback` grew TARGET/PROJECT/CONFIRM for
    TARGET=spanner — the default duckdb path still needs none."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target in ("writeback", "pipeline"):
        for origin in ("cmdline", "env"):
            out = _make_n(
                target,
                {"PROFILE": value} if origin == "cmdline" else {},
                {"PROFILE": value} if origin == "env" else {},
            )
            assert f"serving.cli {target} {quoted}" in out, (target, origin, out)
            assert "pwned" not in out.replace(value, "")
            if target == "pipeline":
                assert "--confirm" not in out  # no CONFIRM knob


# --------------------------- Phase 10: writeback TARGET=spanner, spanner targets


def test_writeback_target_confirm_from_command_line_only() -> None:
    """Phase 10: the writeback recipe carries `--confirm-origin '$(origin
    CONFIRM)'` and forwards TARGET/PROJECT unexpanded, so an env-exported
    CONFIRM reads `environment` and Python refuses the spanner target."""
    out = _make_n("writeback", {"PROFILE": "tiny", "TARGET": "spanner"}, {})
    assert "--confirm-origin 'command line'" not in out
    assert "--target 'spanner'" in out
    out = _make_n(
        "writeback",
        {"PROFILE": "tiny", "TARGET": "spanner", "CONFIRM": "yes"},
        {},
    )
    assert "--confirm 'yes' --confirm-origin 'command line'" in out
    out = _make_n(
        "writeback",
        {"PROFILE": "tiny", "TARGET": "spanner"},
        {"CONFIRM": "yes"},
    )
    assert "--confirm 'yes' --confirm-origin 'environment'" in out


@pytest.mark.parametrize(
    "value",
    ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""],
)
def test_spanner_targets_pass_variables_as_one_literal(value: str) -> None:
    """Phase 10: spanner-load and test-int-spanner forward PROFILE/PROJECT as
    one single-quoted token from either origin, with the CONFIRM origin word
    beside them; test-int-spanner defaults PROFILE to tiny."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target, cli in (
        ("spanner-load", "loader.cli spanner-load"),
        ("test-int-spanner", "loader.cli test-int-spanner"),
    ):
        for origin in ("cmdline", "env"):
            out = _make_n(
                target,
                {"PROFILE": "tiny", "PROJECT": value} if origin == "cmdline" else {},
                {"PROFILE": "tiny", "PROJECT": value} if origin == "env" else {},
            )
            assert f"{cli} 'tiny' --project {quoted}" in out, (target, origin, out)
            assert "pwned" not in out.replace(value, "")
            assert "--confirm-origin '" in out
    out = _make_n("test-int-spanner", {}, {})
    assert "loader.cli test-int-spanner 'tiny'" in out  # PROFILE defaults to tiny


# ----------------------------------- Phase 8b: dbt-build THROUGH, test-int-airflow


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_dbt_build_passes_through_as_one_literal(value: str) -> None:
    """Phase 8b: `make dbt-build` forwards THROUGH as one single-quoted token from
    either origin (the DAG's per-interval landing); Python validates it as an
    upload date and never derives a path."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for origin in ("cmdline", "env"):
        out = _make_n(
            "dbt-build",
            {"PROFILE": "tiny", "THROUGH": value}
            if origin == "cmdline"
            else {"PROFILE": "tiny"},
            {"THROUGH": value} if origin == "env" else {},
        )
        assert f"--through {quoted}" in out, (origin, out)
        assert "pwned" not in out.replace(value, "")
    # THROUGH unset ⇒ empty (loads all — the default build is unchanged)
    out = _make_n("dbt-build", {"PROFILE": "tiny"}, {})
    assert "--through ''" in out


def test_test_int_airflow_takes_no_variable_and_exports_otr_int() -> None:
    """Phase 8b: the integration target is the fixed command — it exports OTR_INT=1
    in-recipe (so tests/integration/ collects) and takes NO user variable (tiny by
    definition; the DAG's PROFILE=tiny is a manifest literal). A PROFILE from either
    origin cannot steer it."""
    expected = "OTR_INT=1 uv run pytest tests/integration/test_int_airflow.py"
    for cmdline, env in (
        ({}, {}),
        ({"PROFILE": "../x"}, {}),
        ({}, {"PROFILE": "../x"}),
    ):
        out = _make_n("test-int-airflow", cmdline, env)
        assert expected in out, out
        assert "../x" not in out  # no variable interpolation at all


# ------------------------------------------------- Phase 9a: tf-plan/apply/destroy


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_tf_targets_pass_project_as_one_literal(value: str) -> None:
    """PROJECT reaches infra.cli as one single-quoted token from either origin;
    Python validates it as a GCP project-id and never derives a path."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target in ("tf-plan", "tf-apply", "tf-destroy"):
        for origin in ("cmdline", "env"):
            out = _make_n(
                target,
                {"PROJECT": value} if origin == "cmdline" else {},
                {"PROJECT": value} if origin == "env" else {},
            )
            assert f"infra.cli {target[len('tf-') :]} --project {quoted}" in out, (
                target,
                origin,
                out,
            )
            assert "pwned" not in out.replace(value, "")


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", ""]
)
def test_bq_targets_pass_project_as_one_literal(value: str) -> None:
    """Phase 9b threat model: PROJECT (and PROFILE) reach loader.cli as one
    single-quoted token from either origin on bq-load, dbt-build and
    test-int-bigquery; Python validates the GCP project-id shape and never
    derives a path from it."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target in ("bq-load", "dbt-build", "test-int-bigquery"):
        for origin in ("cmdline", "env"):
            kv = {"PROJECT": value, "PROFILE": value}
            out = _make_n(
                target, kv if origin == "cmdline" else {}, kv if origin == "env" else {}
            )
            prof = "'tiny'" if target == "test-int-bigquery" and not value else quoted
            assert f"loader.cli {target} {prof}" in out, (target, origin, out)
            assert f"--project {quoted}" in out, (target, origin, out)
            assert "pwned" not in out.replace(value, "")
    out = _make_n("test-int-bigquery", {"PROJECT": "p"}, {})
    assert "loader.cli test-int-bigquery 'tiny' --project 'p'" in out  # PROFILE default


def test_bq_targets_confirm_from_command_line_only() -> None:
    """Phase 9b: the two new cloud targets carry $(origin CONFIRM) — an
    environment CONFIRM=yes reaches Python as 'environment' and is refused."""
    for target in ("bq-load", "test-int-bigquery"):
        out = _make_n(target, {"PROJECT": "p"}, {"CONFIRM": "yes"})
        assert "--confirm 'yes' --confirm-origin 'environment'" in out, target
        out = _make_n(target, {"PROJECT": "p", "CONFIRM": "yes"}, {})
        assert "--confirm 'yes' --confirm-origin 'command line'" in out, target
        out = _make_n(target, {"PROJECT": "p"}, {})
        assert "--confirm '' --confirm-origin 'file'" in out, target


@pytest.mark.parametrize(
    "value", ['"; echo pwned; "', "$(shell echo pwned)", "../x", "a'b", "", "k=v,k2=v2"]
)
def test_tf_targets_pass_vars_as_one_literal(value: str) -> None:
    """fix/tf-vars-argv: VARS reaches infra.cli as one single-quoted token from
    either origin; Python parses it into `-var` items or refuses."""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    for target in ("tf-plan", "tf-apply", "tf-destroy"):
        for origin in ("cmdline", "env"):
            kv = {"PROJECT": "p", "VARS": value}
            out = _make_n(
                target, kv if origin == "cmdline" else {}, kv if origin == "env" else {}
            )
            assert f"--vars {quoted}" in out, (target, origin, out)
            expected = "command line" if origin == "cmdline" else "environment"
            assert f"--vars-origin '{expected}'" in out, (target, origin, out)
            assert "pwned" not in out.replace(value, "")
    assert "--vars" not in _make_n("tf-validate", {}, {})
    out = _make_n("tf-plan", {"PROJECT": "p"}, {})
    assert "--vars '' --vars-origin 'file'" in out  # unset: unexport → `file`


def test_tf_validate_takes_no_project() -> None:
    out = _make_n("tf-validate", {}, {})
    assert "infra.cli validate" in out
    assert "--project" not in out


def test_tf_freeze_confirm_from_command_line_only() -> None:
    """Amendment P: tf-freeze (the manifest's only writer) passes $(origin
    CONFIRM) verbatim and takes no PROJECT."""
    out = _make_n("tf-freeze", {"CONFIRM": "yes"}, {})
    assert "infra.cli freeze --confirm 'yes' --confirm-origin 'command line'" in out
    assert "--project" not in out
    out = _make_n("tf-freeze", {}, {"CONFIRM": "yes"})
    assert "--confirm 'yes' --confirm-origin 'environment'" in out


def test_tf_apply_and_destroy_confirm_from_command_line_only() -> None:
    """tf-apply/tf-destroy pass $(origin CONFIRM) verbatim; Python accepts only
    `command line` + `yes`. An exported CONFIRM=yes is refused, nothing created."""
    from infra import cli

    for target in ("tf-apply", "tf-destroy"):
        out = _make_n(target, {"PROJECT": "my-proj", "CONFIRM": "yes"}, {})
        assert "--confirm 'yes' --confirm-origin 'command line'" in out
        out = _make_n(target, {"PROJECT": "my-proj"}, {"CONFIRM": "yes"})
        assert "--confirm 'yes' --confirm-origin 'environment'" in out
        out = _make_n(target, {"PROJECT": "my-proj"}, {})
        # Gotcha (ARCHITECTURE §8): `unexport CONFIRM` makes an unset CONFIRM
        # origin `file`, not `undefined`. Refused either way.
        assert "--confirm '' --confirm-origin 'file'" in out

    # Inject a fake runner so a mutated-away guard (require_confirm delete-call)
    # can never spawn a real `terraform apply` from the suite.
    def _fake(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0)

    for confirm, origin in (
        ("yes", "environment"),
        ("", "undefined"),
        ("no", "command line"),
    ):
        for cmd in ("apply", "destroy"):
            with pytest.raises(SystemExit) as e:
                cli.tf(cmd, "my-proj", confirm, origin, runner=_fake)
            assert e.value.code == 2
