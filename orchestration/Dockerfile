# syntax=docker/dockerfile:1
# Lean, Docker-local Airflow for Phase 8b's `make test-int-airflow`:
# SequentialExecutor + SQLite, driven by `airflow dags test` (a synchronous dag
# run — no webserver/scheduler/Postgres). apache-airflow lives ONLY in this image,
# never in the repo venv / uv.lock. The image is self-contained (repo COPYd, deps
# synced from the lock) so a container run needs no network.
#
# Two Pythons by design: Airflow runs under the image's interpreter; the DAG's
# BashOperators shell out to `make … → uv run` which uses a SEPARATE project venv
# (UV_PROJECT_ENVIRONMENT) holding dbt/duckdb, not airflow. Clean separation.
ARG AIRFLOW_VERSION=2.10.3
FROM apache/airflow:${AIRFLOW_VERSION}-python3.12

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends make \
 && rm -rf /var/lib/apt/lists/*
# uv drives the project venv (kept out of the copied repo tree).
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

USER airflow
ENV UV_PROJECT_ENVIRONMENT=/opt/airflow/otr-venv \
    UV_FROZEN=1 \
    UV_PYTHON=/usr/local/bin/python \
    DO_NOT_TRACK=1 \
    PYTHONPATH=/opt/otr \
    AIRFLOW__CORE__EXECUTOR=SequentialExecutor \
    AIRFLOW__CORE__DAGS_FOLDER=/opt/otr/orchestration/dags \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=False
WORKDIR /opt/otr

# Layer 1: project runtime deps from the lock (cached unless the lock changes) —
# installed into the separate venv so the container run needs no network.
COPY --chown=airflow:root pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Layer 2: the repo (data/, .venv, .git excluded via .dockerignore).
COPY --chown=airflow:root . /opt/otr
