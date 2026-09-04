"""The pipeline DAG's `on_failure_callback` — email on any task failure
(fix/composer-cosmos, ROADMAP item 7's second freshness clause: "an
on_failure_callback that emails"). Any failed task (a stale source freshness
gate, a failed model, a failed pod) fires it.

The Airflow imports are LAZY and INJECTABLE (Adapter contract): the recipient
lookup and the send are parameters that default to Airflow's own
`Variable.get` / `send_email`, so the callback is unit-tested offline with fakes
— the airflow package is Docker/Composer-only, never in the venv (uv.lock). The
recipient is an Airflow Variable, never a hardcoded address (no PII in the tree);
SMTP is a Composer airflow.cfg override, documented, no secret here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# Dual-path (the composer_dag.py pattern): the package path resolves in the
# Docker rehearsal / offline suite; the flat `import composer_tasks` resolves in
# the Composer DAG bucket, where only `dags/` is on sys.path.
try:
    from orchestration.composer_tasks import ALERT_EMAIL_VARIABLE
except ImportError:  # flat Composer dags/ bucket
    from composer_tasks import ALERT_EMAIL_VARIABLE  # type: ignore[no-redef]


def _airflow_recipient() -> str:
    from airflow.models import Variable

    return Variable.get(ALERT_EMAIL_VARIABLE)


def _airflow_send(*, to: str, subject: str, html_content: str) -> None:
    from airflow.utils.email import send_email

    send_email(to=to, subject=subject, html_content=html_content)


def pipeline_failure_email(
    context: Mapping[str, Any],
    *,
    get_recipient: Callable[[], str] = _airflow_recipient,
    send: Callable[..., None] = _airflow_send,
) -> str:
    """Build and send ONE failure email naming the DAG, task and run, from the
    Airflow task context. Returns the subject (for logging / tests). The lookup
    and the send are injected in tests; in Airflow they default to the real ones.
    """
    dag_id = _context_get(context, "dag") or "pipeline"
    task_id = _context_get(context, "task") or "unknown"
    run_id = context.get("run_id", "unknown")
    exec_date = context.get("logical_date") or context.get("execution_date") or ""
    subject = f"[ontime] DAG {dag_id} task {task_id} failed"
    html_content = (
        f"<p>Task <b>{task_id}</b> in DAG <b>{dag_id}</b> failed.</p>"
        f"<p>run_id: {run_id}<br>logical_date: {exec_date}</p>"
    )
    send(to=get_recipient(), subject=subject, html_content=html_content)
    return subject


def _context_get(context: Mapping[str, Any], key: str) -> str:
    """`context['dag'].dag_id` / `context['task'].task_id` when present, else the
    string form — tolerant of the fake contexts the offline test passes."""
    obj = context.get(key)
    if obj is None:
        return ""
    for attr in ("dag_id", "task_id"):
        value = getattr(obj, attr, None)
        if value is not None:
            return str(value)
    return str(obj)
