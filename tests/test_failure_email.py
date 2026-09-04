"""fix/composer-cosmos: the on_failure_callback sends exactly one email on a
failure context (invariant 3). The Airflow send + Variable lookup are injected
(Adapter contract), so this runs offline with fakes — airflow is Composer-only."""

from __future__ import annotations

import types
from typing import Any

from orchestration.failure_email import pipeline_failure_email


def _context() -> dict[str, Any]:
    return {
        "dag": types.SimpleNamespace(dag_id="ontime_cloud"),
        "task": types.SimpleNamespace(task_id="bq_load"),
        "run_id": "scheduled__2026-01-13",
        "logical_date": "2026-01-13T00:00:00+00:00",
    }


def test_callback_sends_one_email_on_failure() -> None:
    sent: list[dict[str, Any]] = []
    subject = pipeline_failure_email(
        _context(),
        get_recipient=lambda: "oncall@example.test",
        send=lambda **kw: sent.append(kw),
    )
    assert len(sent) == 1
    call = sent[0]
    assert call["to"] == "oncall@example.test"
    assert call["subject"] == subject == "[ontime] DAG ontime_cloud task bq_load failed"
    assert "bq_load" in call["html_content"] and "ontime_cloud" in call["html_content"]
    assert "scheduled__2026-01-13" in call["html_content"]


def test_recipient_comes_from_a_variable_never_hardcoded() -> None:
    """No email address is hardcoded — the recipient is an injected lookup (the
    Airflow Variable in production; no PII in the tree)."""
    from orchestration import failure_email

    src = (failure_email.__file__ and open(failure_email.__file__).read()) or ""
    assert "@" not in src.replace("oncall", "")  # no literal address in the module
    seen: list[str] = []
    pipeline_failure_email(
        _context(),
        get_recipient=lambda: seen.append("looked-up") or "x@y.test",
        send=lambda **kw: None,
    )
    assert seen == ["looked-up"]


def test_callback_tolerates_a_sparse_context() -> None:
    """A failure context missing dag/task still sends one email (defensive)."""
    sent: list[dict[str, Any]] = []
    pipeline_failure_email(
        {"run_id": "manual__x"},
        get_recipient=lambda: "a@b.test",
        send=lambda **kw: sent.append(kw),
    )
    assert len(sent) == 1
    assert "pipeline" in sent[0]["subject"] or "unknown" in sent[0]["subject"]
