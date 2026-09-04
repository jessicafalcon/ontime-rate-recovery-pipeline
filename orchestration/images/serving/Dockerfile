# syntax=docker/dockerfile:1
# The serving+landing image for the Cloud-Composer runtime (fix/composer-cosmos,
# 7a). The KubernetesPodOperator steps (bq_load, spanner_load, writeback) run the
# committed module CLIs inside this image — the toolchain the Phase 12 make-based
# DAG lacked on a Composer worker (§8). Built and pushed to Artifact Registry by
# `make build-serving-image PROJECT=<id> CONFIRM=yes` (ask-first; the push runs in
# 7b). Authenticates by the environment's Workload-Identity SA at run — NO
# credential is ever baked (no keyfile, no GOOGLE_APPLICATION_CREDENTIALS).
#
# Truth isolation: this ships the SERVING side only. It COPYs exact subpaths, never
# `COPY .`, and never copies any `truth/` tree (the write-back reads BigQuery
# `ontime` and writes Spanner; the landing reads raw/+dims/ only — manifest_drift
# hashes those two subtrees, never truth). data/, .git, the venv and secrets are
# excluded by Dockerfile.dockerignore AND never named by a COPY.
#
# Build context is the repo root:
#   docker build -f orchestration/images/serving/Dockerfile -t <ar-image> .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

ENV UV_FROZEN=1 \
    UV_PYTHON=/usr/local/bin/python \
    DO_NOT_TRACK=1 \
    VIRTUAL_ENV=/opt/otr/.venv \
    PATH=/opt/otr/.venv/bin:$PATH \
    PYTHONPATH=/opt/otr
WORKDIR /opt/otr

# Layer 1: locked runtime deps (google-cloud-bigquery/storage/spanner, duckdb,
# pydantic, …), cached unless the lock changes. --no-dev / --no-install-project:
# the repo packages are COPYd below, not pip-installed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Layer 2: ONLY the packages the two entrypoints import, each an explicit path so
# nothing else (no truth/, no data/, no dbt project) can slip in via a broad COPY.
# generator/: JUST the manifest hasher the landing uses to verify MANIFEST.sha256
# (stdlib-only) — NEVER the generation logic (generate/response/dims/writer) or
# the truth writer (generator/truth.py); the serving side must not ship either
# (truth isolation, invariant 1). test_serving_image.py pins the exact set.
COPY generator/__init__.py generator/manifest.py /opt/otr/generator/
COPY landing/ /opt/otr/landing/
COPY serving/ /opt/otr/serving/
COPY pipeline/ /opt/otr/pipeline/
COPY infra/ /opt/otr/infra/

# The tiny fixture's staged subtrees + manifest — the raw export files and the dim
# seed the BigQuery/Spanner landings read (raw/ + dims/ only; truth/ is never
# copied and never read by manifest_drift).
COPY fixtures/tiny/raw/ /opt/otr/fixtures/tiny/raw/
COPY fixtures/tiny/dims/ /opt/otr/fixtures/tiny/dims/
COPY fixtures/tiny/MANIFEST.sha256 /opt/otr/fixtures/tiny/MANIFEST.sha256

# No ENTRYPOINT/CMD: the KubernetesPodOperator sets the container `cmds`
# (orchestration/composer_tasks.build_kpo_command) — a fixed, reviewed argv.
