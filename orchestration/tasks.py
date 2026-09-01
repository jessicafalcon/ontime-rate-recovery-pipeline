"""The pipeline's ordered task commands — the one definition the Airflow DAG and
the offline structure test share.

No Airflow import: this is a static manifest of `make` targets (a task is a
`make` target — CLAUDE.md § Engineering contracts), so importing it needs no
scheduler and `tests/test_dag_structure.py` runs under `make test` without
apache-airflow (which is Docker-only, never in the venv / uv.lock). The DAG in
`dags/pipeline_dag.py` builds one BashOperator per entry, in order.

The interval → THROUGH mapping is a **literal Jinja token** Airflow renders at
run time (`{{ data_interval_end | ds }}`); we compute nothing, so "Airflow
contains no logic" holds. These two steps ARE `make pipeline`'s WRITING steps
(serving.cli.pipeline runs dbt build → eval → write-back; `eval` asserts the
full-data pins and reads the side-file, so it is a union-only validation gate in
`make pipeline`/CI, never a per-interval task — Amendment 1). The build carries
the interval's THROUGH so a per-interval run lands only files ≤ that date; eval
writes no table, so the DAG's two outputs stay byte-identical to `make
pipeline`'s."""

from __future__ import annotations

import os
import re

PROFILE = "tiny"

# The recognised cloud target (the only non-duckdb value build_tasks renders).
CLOUD_TARGET = "bigquery"

# GCP project-id shape — the SAME pattern infra.cli.PROJECT_RE pins (inlined so
# tasks.py stays stdlib-only for a Composer parse). A malformed project (a shell
# metacharacter, a quote) never reaches the rendered command — it refuses here,
# not only at the downstream make/pipeline.cli validator.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")

# Rendered to YYYY-MM-DD by Airflow, never by us. A per-interval build lands only
# the files uploaded on or before this date (Phase 8b); the DAG's @daily schedule
# gives consecutive intervals a 1-day gap, well within lookback_days, so an
# explicit backfill converges to the union (catchup=False).
THROUGH_TEMPLATE = "{{ data_interval_end | ds }}"


def build_tasks(target: str, project: str) -> list[tuple[str, str]]:
    """The ordered (task_id, make command) list — make pipeline's WRITING steps.

    An ALLOWLIST over the target, not a denylist (Boundary contract): `duckdb`
    renders the local build + local write-back, byte-identical to the Docker-local
    DAG (Phase 8b); `bigquery` (CLOUD_TARGET) renders the cloud build + Spanner
    write-back; ANY OTHER value REFUSES (raises) — an unrecognised or mistyped
    `OTR_DAG_TARGET` can never silently render a cloud-cost command. The cloud
    branch also refuses a project that is not a valid GCP project id, so no
    unvalidated value is interpolated into the rendered shell command. Each cloud
    command carries `PROJECT` (single-quoted) and `CONFIRM=yes` — command-line
    origin inside the BashOperator, so the `$(origin CONFIRM)` gate accepts it.
    Selecting a target is config, not logic: every rendered command is a `make`
    target (CLAUDE.md "Airflow contains no logic"). `THROUGH` is single-quoted so
    the rendered date is one shell token.
    """
    if target == "duckdb":
        return [
            (
                "dbt_build",
                f"make dbt-build PROFILE={PROFILE} TARGET={target} "
                f"THROUGH='{THROUGH_TEMPLATE}'",
            ),
            ("writeback", f"make writeback PROFILE={PROFILE}"),
        ]
    if target == CLOUD_TARGET:
        if not PROJECT_RE.match(project):
            raise ValueError(
                f"OTR_DAG_PROJECT is not a valid GCP project id: {project!r}"
            )
        return [
            (
                "dbt_build",
                f"make dbt-build PROFILE={PROFILE} TARGET={target} "
                f"PROJECT='{project}' CONFIRM=yes THROUGH='{THROUGH_TEMPLATE}'",
            ),
            (
                "writeback",
                f"make writeback PROFILE={PROFILE} TARGET=spanner "
                f"PROJECT='{project}' CONFIRM=yes",
            ),
        ]
    raise ValueError(
        f"unrecognised OTR_DAG_TARGET: {target!r} "
        f"(expected 'duckdb' or {CLOUD_TARGET!r})"
    )


# The warehouse the build lands in and builds against (Phase 9b): the build's
# landing is the target's own (duckdb → the DuckDB file; bigquery → GCS →
# BigQuery), so the DAG names it. Env-driven config (Phase 12): unset → duckdb,
# the Docker-local DAG unchanged; the live rehearsal and the Composer run set
# OTR_DAG_TARGET=bigquery + OTR_DAG_PROJECT=<id> so one DAG run builds on BigQuery
# and writes Spanner. Read at parse time; it selects a target, never a path.
TARGET = os.environ.get("OTR_DAG_TARGET", "duckdb")
PROJECT = os.environ.get("OTR_DAG_PROJECT", "")

TASKS: list[tuple[str, str]] = build_tasks(TARGET, PROJECT)
