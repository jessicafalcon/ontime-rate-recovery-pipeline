"""Session-wide guards.

1. Integration tests run ONLY under the `make test-int-*` targets (Phase 8
   `test-int-airflow`, Phase 9 `test-int-bigquery`), which export OTR_INT=1.
   A bare `pytest` (the run-tests hook makes it routine) must never touch a
   live target. Without the
   marker every `tests/integration` test is SKIPPED, loudly (Phase 8b added the
   directory — `test_int_airflow.py`; the guard predates it so adding it could
   not forget the rule).
2. CONFIRM / MAKEFLAGS / PROFILE / TARGET are scrubbed so the Makefile-invoking
   tests (tests/test_makefile.py) see a clean env.
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
    # Keep in step with tests/test_makefile.py::SCRUB (round 3 #12).
    for var in (
        "CONFIRM",
        "MAKEFLAGS",
        "PROFILE",
        "TARGET",
        "THROUGH",
        "WRITE",
        "FULL",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
