"""Phase 9b (specs/phase-9b-bigquery-dialect.md): DuckDB ≡ BigQuery pin parity.

Behind OTR_INT — only `make test-int-bigquery PROJECT=<id> CONFIRM=yes` runs it
(loader/cli.py validates PROJECT and gates CONFIRM before spawning this; CI
never runs it — the CI leg needs the opt-in WIF apply, docs/DEPLOYMENT.md).
Cloud-cost, ask-first, as the impersonated SA.

Lands fixtures/tiny into `<project>.raw` (GCS → BigQuery), `dbt build`s on the
bigquery target (into `ontime`), then reads the three golden tables back through
the SAME `Golden` specs and renderer every DuckDB gate uses and diffs them
against the read-only fixtures/tiny/expected/*.csv — byte-for-byte. The pins
follow from the goldens (label accuracy, the overall rate, the send-time pins)
and are re-asserted off the BigQuery rows with the same eval functions. Finally:
exactly two datasets exist (invariant 1 — nothing created out of band)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from eval import golden, score
from infra.cli import PROJECT_RE
from loader import cli as loader_cli
from tests import pins

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "tiny"
MODELS_DATASET = "ontime"  # infra/variables.tf models_dataset default
GOLDENS = (golden.ATTRIBUTION, golden.ONTIME_RATE_DAILY, golden.SCORES_SEND_TIME)


def _project() -> str:
    project = os.environ.get("OTR_GCP_PROJECT", "")
    assert PROJECT_RE.match(project), "OTR_GCP_PROJECT is set by loader.cli only"
    return project


@pytest.fixture(scope="module")
def built() -> Iterator[str]:
    """The landing + the build, once; yields the project id."""
    project = _project()
    assert os.environ.get("OTR_PROFILE", "tiny") == "tiny"  # tiny by definition
    rc = loader_cli.dbt_build(
        "tiny", "bigquery", "yes", "command line", project=project
    )
    assert rc == 0, "make dbt-build TARGET=bigquery PROFILE=tiny failed"
    yield project


def _client(project: str):  # noqa: ANN202 — the google type is a runtime import
    from google.cloud import bigquery

    return bigquery.Client(project=project, location="us-central1")


def _rows(project: str, spec: golden.Golden) -> list[tuple[str, ...]]:
    table = spec.relation.rsplit(".", 1)[1]
    sql = golden.select_sql(spec, f"`{project}.{MODELS_DATASET}.{table}`")
    rows = [tuple(r.values()) for r in _client(project).query(sql).result()]
    return golden.rows_from(rows)


def test_goldens_match_frozen(built: str) -> None:
    for spec in GOLDENS:
        rows = _rows(built, spec)
        frozen = golden.parse((FIXTURES / spec.file).read_text(), spec)
        diff = golden.diff_rows(rows, frozen, spec.key_width)
        assert diff == [], f"{spec.file}: {len(diff)} differ\n" + "\n".join(diff[:20])
        assert golden.render(rows, spec) == (FIXTURES / spec.file).read_text()


def test_pins_hold_on_bigquery(built: str) -> None:
    labels = {r[0]: r[3] for r in _rows(built, golden.ATTRIBUTION)}
    truth = score.truth_labels(FIXTURES / "truth" / "prompts.jsonl")
    assert score.label_accuracy(labels, truth) == pins.LABEL_ACCURACY
    assert score.label_counts(labels) == pins.ATTRIBUTION_LABEL_COUNTS
    daily = _rows(built, golden.ONTIME_RATE_DAILY)
    on_time = sum(int(r[4]) for r in daily)
    delivered = sum(int(r[3]) for r in daily)
    assert len(daily) == pins.COHORT_DAYS
    assert on_time / delivered == pins.ONTIME_RATE
    scores = _rows(built, golden.SCORES_SEND_TIME)
    assert len(scores) == pins.SCORES_ROWS
    assert {r[1]: int(r[4]) for r in scores} == pins.COHORT_HOUR_TINY
    assert {r[8] for r in scores} == {pins.COMPUTED_AS_OF_TINY}


def test_exactly_two_datasets_exist(built: str) -> None:
    """Invariant 1 / 9a Done-when 5: the build created no dataset out of band."""
    names = sorted(d.dataset_id for d in _client(built).list_datasets())
    assert names == [MODELS_DATASET, "raw"], names
    tables = sorted(
        t.table_id for t in _client(built).list_tables(f"{built}.{MODELS_DATASET}")
    )
    built_models = {
        "stg_events",
        "attribution",
        "ontime_rate_daily",
        "scores_send_time",
    }
    assert built_models <= set(tables)
