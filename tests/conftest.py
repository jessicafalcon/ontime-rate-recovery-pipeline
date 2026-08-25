"""Session-wide guards.

1. Integration tests (Phase 8 Airflow, Phase 9 BigQuery) run ONLY under the
   `make test-int*` targets, which export OTR_INT=1. A bare `pytest` (the
   run-tests hook makes it routine) must never touch a live target. Without the
   marker every `tests/integration` test is SKIPPED, loudly. The directory does
   not exist yet; the guard is here so adding it cannot forget the rule.
2. CONFIRM / MAKEFLAGS are scrubbed so Makefile guard tests see a clean env.
"""

import os

import pytest

INTEGRATION_MARKER = "OTR_INT"


def pytest_collection_modifyitems(config, items):
    if os.environ.get(INTEGRATION_MARKER) == "1":
        return
    skip = pytest.mark.skip(
        reason=f"integration tests run under `make test-int*` ({INTEGRATION_MARKER}=1)"
    )
    for item in items:
        if "tests/integration/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for var in ("CONFIRM", "MAKEFLAGS", "PROFILE", "TARGET"):
        monkeypatch.delenv(var, raising=False)
    yield
