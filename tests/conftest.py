"""Session-wide guards.

1. Integration tests run ONLY under the `make test-int-*` targets (Phase 8
   `test-int-airflow`, Phase 9 `test-int-bigquery`, Phase 10
   `test-int-spanner`), which export OTR_INT=1.
   A bare `pytest` (the run-tests hook makes it routine) must never touch a
   live target. Without the
   marker every `tests/integration` test is SKIPPED, loudly (Phase 8b added the
   directory — `test_int_airflow.py`; the guard predates it so adding it could
   not forget the rule).
2. The make user-variables (CONFIRM, PROFILE, TARGET, THROUGH, WRITE, FULL,
   PROJECT, VARS, ALLOW_DESTROY), MAKEFLAGS, every TF_VAR_*/TF_CLI_ARGS* and
   every Google-namespace variable the cloud-env allowlist does not admit
   (infra.cli.unlisted_cloud_env — the gate's own function, Amendment N2) are
   scrubbed so the Makefile-invoking tests (tests/test_makefile.py) and the
   cloud-command refusal tests see a clean env.
"""

import os
from collections.abc import Iterator

import pytest

INTEGRATION_MARKER = "OTR_INT"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get(INTEGRATION_MARKER) == "1":
        return
    skip = pytest.mark.skip(
        reason=f"integration tests run under `make test-int-*` ({INTEGRATION_MARKER}=1)"
    )
    for item in items:
        if "tests/integration/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The make user-variables + MAKEFLAGS. tests/test_makefile.py::SCRUB is a
    # superset (it also drops the gate vars SPEC/BASE/DELETED and MFLAGS).
    for var in (
        "CONFIRM",
        "MAKEFLAGS",
        "PROFILE",
        "TARGET",
        "THROUGH",
        "WRITE",
        "FULL",
        "PROJECT",
        "VARS",
        "ALLOW_DESTROY",
    ):
        monkeypatch.delenv(var, raising=False)
    from infra.cli import env_tf_vars, unlisted_cloud_env

    # env_tf_vars() is the gate's own TF_VAR_*/TF_CLI_ARGS* scan — called, not
    # re-implemented, so the scrub cannot drift from it (round 6 #12).
    for var in env_tf_vars() + unlisted_cloud_env():
        # the cloud commands refuse them; a developer's export must not redden the suite
        monkeypatch.delenv(var)
    yield
