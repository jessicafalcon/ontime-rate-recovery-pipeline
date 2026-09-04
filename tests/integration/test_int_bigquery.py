"""Phase 9b (specs/phase-9b-bigquery-dialect.md): DuckDB ≡ BigQuery pin parity.

Behind OTR_INT — only `make test-int-bigquery PROJECT=<id> CONFIRM=yes` runs it
(pipeline/cli.py validates PROJECT and gates CONFIRM before spawning this; CI
never runs it — the CI leg needs the opt-in WIF apply, docs/DEPLOYMENT.md).
Cloud-cost, ask-first, as the impersonated SA.

Lands fixtures/tiny into `<project>.raw` (GCS → BigQuery), `dbt build`s on the
bigquery target (into `ontime`), then reads the three golden tables back through
the SAME `Golden` specs and renderer every DuckDB gate uses and diffs them
against the read-only fixtures/tiny/expected/*.csv — byte-for-byte. The pins
follow from the goldens (label accuracy, the overall rate, the send-time pins)
and are re-asserted off the BigQuery rows with the same eval functions. Finally:
exactly two datasets exist (invariant 1 — nothing created out of band).

fix/prune-live-proof adds the INCREMENTAL source-scan prune's live proof. The
`built` reference is now a self-contained FULL reset (drop raw.events + a
--full-refresh build, so a warehouse left by another profile can't corrupt the
tiny parity — the hermeticity fix). A second phase then lands ≤ a cut and
FULL-builds, lands the late tail and runs a PLAIN (incremental) build — so
`is_incremental()` is true and the BigQuery source-scan prune predicate renders
and executes live — and asserts the built tables converge byte-identical to the
frozen full-scan goldens. (tiny's 9-day span sits inside the 10-day prune
window, so the predicate prunes no partition here — this proves the prune's
live correctness/byte-parity, not a bytes reduction, which is a >10-day-span
effect measured on `large`.)"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from eval import golden, score
from infra.cli import PROJECT_RE, confirmed
from pipeline import cli as pipeline_cli
from tests import pins

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "tiny"
MODELS_DATASET = "ontime"  # infra/variables.tf models_dataset default
GOLDENS = (golden.ATTRIBUTION, golden.ONTIME_RATE_DAILY, golden.SCORES_SEND_TIME)


def _project() -> str:
    project = os.environ.get("OTR_GCP_PROJECT", "")
    assert PROJECT_RE.match(project), "OTR_GCP_PROJECT is set by pipeline.cli only"
    return project


def carried_gate() -> tuple[str, str]:
    """Amendment V: the CONFIRM gate is CARRIED from pipeline.cli::int_bigquery
    (the make target), never forged here — a bare pytest with OTR_INT=1 and a
    project in its env finds no confirmation and is refused before any build.
    Offline pin: tests/test_bq_landing.py::
    test_parity_fixture_refuses_without_the_carried_gate."""
    confirm = os.environ.get("OTR_CONFIRM", "")
    origin = os.environ.get("OTR_CONFIRM_ORIGIN", "")
    # The pair is CARRIED, and re-checked with the make target's own predicate
    # (round 2 #7) — never a literal forged here.
    if not confirmed(confirm, origin):
        raise RuntimeError("refused: run via `make test-int-bigquery … CONFIRM=yes`")
    return confirm, origin


def _drop_raw_events(project: str) -> None:
    """Reset raw.events to absent so the next landing self-creates a fresh
    DAY-partitioned table. Insulates the run from a prior profile's leftover
    partitions (hermeticity) AND from a pre-existing non-partitioned table (the
    append-landing migration) — either otherwise silently corrupts the tiny
    parity (fix/prune-live-proof)."""
    _client(project).query(f"drop table if exists `{project}.raw.events`").result()


@pytest.fixture(scope="module")
def built() -> Iterator[str]:
    """The full-scan reference, built once. Reset the warehouse clean first —
    drop raw.events (the next landing self-creates it DAY-partitioned) and
    --full-refresh the ontime models — so the run is self-contained no matter
    what the dataset held before (fix/prune-live-proof: the hermeticity fix).
    Yields the project id."""
    project = _project()
    assert os.environ.get("OTR_PROFILE", "tiny") == "tiny"  # tiny by definition
    confirm, origin = carried_gate()
    _drop_raw_events(project)
    rc = pipeline_cli.dbt_build(
        "tiny",
        "bigquery",
        confirm,
        origin,
        full="yes",
        full_origin=origin,
        project=project,
    )
    assert rc == 0, "make dbt-build TARGET=bigquery PROFILE=tiny FULL=yes failed"
    yield project


@pytest.fixture(scope="module")
def incremental_parity(built: str) -> str:
    """fix/prune-live-proof: prove the INCREMENTAL source-scan prune live, after
    the full-scan reference. Reset raw.events, land ≤ the split cut and FULL-build
    (the closed state), then land the late tail and run a PLAIN build — so
    `is_incremental()` is true and the BigQuery source-scan prune predicate renders
    and executes on a real warehouse. Yields the project id; the built tables must
    converge byte-identical to the frozen full-scan goldens (the two-landing
    convergence of tests/test_incremental.py, on the second dialect)."""
    project = built
    confirm, origin = carried_gate()
    _drop_raw_events(project)  # cut-horizon raw + a fresh DAY-partitioned table
    rc = pipeline_cli.dbt_build(
        "tiny",
        "bigquery",
        confirm,
        origin,
        full="yes",
        full_origin=origin,
        through=pins.LANDING_SPLIT_TINY,
        project=project,
    )
    assert rc == 0, "closed-state build (≤ cut, --full-refresh) failed"
    rc = pipeline_cli.dbt_build("tiny", "bigquery", confirm, origin, project=project)
    assert rc == 0, "incremental late-tail build failed"  # PLAIN → the prune fires
    return project


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
    # the send-time pins, off the BigQuery rows with the same eval functions
    # (served pair → minute of day / 60; the centre column) as `make eval`
    served = {
        r[0]: (float(r[5]), int(r[2]) + int(r[3]) / 60.0) for r in scores
    }  # user_id → (center_hour_local, served hour) — score.built_scores' shape
    windows = score.truth_windows(FIXTURES / "truth" / "users.jsonl")
    assert round(score.reachable_center_mae(served, windows), 8) == round(
        pins.MAE_TINY, 8
    )
    assert score.coverage(served, windows) == pins.COVERAGE_TINY


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


def test_planted_conflict_fails_on_bigquery(built: str) -> None:
    """Invariant 6 on the SECOND dialect (round 2 #4): two `raw.events` rows on
    one clock triple whose payloads differ only in `""` vs `null` make
    `assert_no_conflicting_duplicates` fail through the json_value form; the
    rows are removed after, and the test is re-run green."""

    from dbt.cli.main import dbtRunner

    client = _client(built)
    table = f"`{built}.raw.events`"
    schema = json.loads((ROOT / "landing" / "bq_schema.json").read_text())
    columns = ", ".join(f["name"] for f in schema["events"])  # contract order
    common = (
        "'e-planted', 'upload_started', 'u-1', 'd-1', "
        "timestamp '2026-01-05 08:00:00', timestamp '2026-01-05 08:00:01', "
        "timestamp '2026-01-05 08:01:00', "
    )
    args = ["test", "--select", "assert_no_conflicting_duplicates"]
    args += ["--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt")]
    args += ["--target", "bigquery", "--quiet"]
    try:
        for payload in (
            '{"prompt_id": "p-x", "attempt": 1, "error_code": ""}',
            '{"prompt_id": "p-x", "attempt": 1, "error_code": null}',
        ):
            client.query(
                f"insert into {table} ({columns}) values ({common} json '{payload}')"
            ).result()
        assert not dbtRunner().invoke(args).success
    finally:
        client.query(f"delete from {table} where insert_id = 'e-planted'").result()
    assert dbtRunner().invoke(args).success


def test_incremental_build_matches_frozen(incremental_parity: str) -> None:
    """The prune's live byte-parity: after a two-landing INCREMENTAL build (the
    prune predicate rendered and executed on BigQuery), every built golden is
    byte-identical to the frozen full-scan golden — so the pruned source window
    is a superset of every row the full scan keeps. This is what a full build
    never exercised (the predicate fires only when `is_incremental()`)."""
    for spec in GOLDENS:
        rows = _rows(incremental_parity, spec)
        got = golden.render(rows, spec)
        assert got == (FIXTURES / spec.file).read_text(), spec.file
    # The pins derive from these goldens; re-assert the two headline ones so the
    # "pins hold on the incremental build" claim is explicit, not only transitive.
    labels = {r[0]: r[3] for r in _rows(incremental_parity, golden.ATTRIBUTION)}
    truth = score.truth_labels(FIXTURES / "truth" / "prompts.jsonl")
    assert score.label_accuracy(labels, truth) == pins.LABEL_ACCURACY
    daily = _rows(incremental_parity, golden.ONTIME_RATE_DAILY)
    on_time = sum(int(r[4]) for r in daily)
    delivered = sum(int(r[3]) for r in daily)
    assert on_time / delivered == pins.ONTIME_RATE


def test_incremental_prune_predicate_rendered(incremental_parity: str) -> None:
    """The source-scan prune ran in the incremental build: the compiled
    `stg_events` (dbt's last render — the incremental late-tail build) carries the
    `timestamp_sub(...)` source predicate, which the template emits only inside
    the `is_incremental() and target.type == 'bigquery'` guard. So the guard held
    and the prune executed live, not only in the offline compile test."""
    compiled = list((ROOT / "dbt" / "target" / "compiled").rglob("stg_events.sql"))
    assert compiled, "no compiled stg_events.sql — did the incremental build run?"
    sql = compiled[0].read_text()
    assert "timestamp_sub" in sql, sql
