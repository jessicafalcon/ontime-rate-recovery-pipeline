"""Phase 10 (specs/phase-10-spanner-writeback.md): Spanner dims + write-back.

Behind OTR_INT — only `make test-int-spanner PROJECT=<id> CONFIRM=yes` runs it
(loader/cli.py validates PROJECT and gates CONFIRM before spawning this; CI
never runs it). Cloud-cost, ask-first, as the impersonated SA, and it needs a
spanner-enabled stack (`make tf-apply … VARS='enable_spanner=true'` — the
teardown date goes in docs/DEPLOYMENT.md the same day; tear down after with
`VARS='enable_spanner=false'`).

Lands the dim seed into Spanner (`spanner-load`), builds on the bigquery target
with the `dim_user` SOURCE swapped to the federation view `raw.dim_user_spanner`
(EXTERNAL_QUERY over the Spanner dims — §3.3's source swap, no model changes),
asserts the three goldens byte-for-byte, then runs the Spanner write-back twice:
the second run writes 0, and the read-back — rendered through the SAME golden
renderer as the DuckDB stand-in — hashes to SEND_SCHEDULE_SHA256_TINY
(cross-store byte parity)."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from eval import golden
from infra.cli import PROJECT_RE
from loader import cli as loader_cli
from loader import spanner as dims
from serving import spanner as spanner_wb
from tests import pins
from tests.test_writeback import SEND_SCHEDULE_GOLDEN

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "tiny"
MODELS_DATASET = "ontime"
GOLDENS = (golden.ATTRIBUTION, golden.ONTIME_RATE_DAILY, golden.SCORES_SEND_TIME)
SWAP = "dim_user_spanner"  # loader.cli.dbt_build's one var seam (dim_user_identifier)
MANIFEST = ROOT / "dbt" / "target" / "manifest.json"


def _project() -> str:
    project = os.environ.get("OTR_GCP_PROJECT", "")
    assert PROJECT_RE.match(project), "OTR_GCP_PROJECT is set by loader.cli only"
    return project


def carried_gate() -> tuple[str, str]:
    """The Amendment V shape: the CONFIRM gate is CARRIED from
    loader.cli::int_spanner (the make target), never forged here."""
    confirm = os.environ.get("OTR_CONFIRM", "")
    origin = os.environ.get("OTR_CONFIRM_ORIGIN", "")
    if not (confirm and origin):
        raise RuntimeError("refused: run via `make test-int-spanner … CONFIRM=yes`")
    return confirm, origin


@pytest.fixture(scope="module")
def built() -> Iterator[str]:
    """dims → Spanner, then the swapped build (dim_user = the federation view),
    once; yields the project id."""
    project = _project()
    assert os.environ.get("OTR_PROFILE", "tiny") == "tiny"  # tiny by definition
    confirm, origin = carried_gate()
    rc = loader_cli.spanner_load("tiny", project, confirm, origin)
    assert rc == 0, "make spanner-load failed"
    rc = loader_cli.dbt_build(
        "tiny", "bigquery", confirm, origin, project=project, dim_user_identifier=SWAP
    )
    assert rc == 0, "dbt-build TARGET=bigquery with the federated dims failed"
    yield project


def _dim_user_source_relation() -> str:
    """The relation dbt resolved the `dim_user` SOURCE to in the build just
    run — off dbt's own manifest, the artifact of that build."""
    manifest = json.loads(MANIFEST.read_text())
    (node,) = [n for k, n in manifest["sources"].items() if k.endswith(".raw.dim_user")]
    return f"{node['identifier']}|{node['relation_name']}"


def test_build_read_dims_through_the_federation_view(built: str) -> None:
    """Round 1 #5 — Done-when 4 falsifiable: the goldens could match off the
    landed table too (view rows ≡ landed rows), so this asserts the build
    actually resolved the dim_user source to `raw.dim_user_spanner` (dbt's
    manifest for the swapped build), not the landed table."""
    identifier, relation = _dim_user_source_relation().split("|")
    assert identifier == SWAP
    assert relation == f"`{built}`.`raw`.`{SWAP}`", relation


def _bq(project: str):  # noqa: ANN202 — the google type is a runtime import
    from google.cloud import bigquery

    return bigquery.Client(project=project, location="us-central1")


def test_federated_view_rows_equal_seed(built: str) -> None:
    """EXTERNAL_QUERY returns exactly the rows the seed landed — the federation
    is the dims, not an approximation of them."""
    names, seed = dims.read_rows("tiny")
    sql = f"select {', '.join(names)} from `{built}.raw.dim_user_spanner`"
    got = [tuple(r.values()) for r in _bq(built).query(sql).result()]
    key = tuple(names).index("user_id"), tuple(names).index("valid_from")

    def normal(rows: list[tuple]) -> list[tuple]:
        return sorted(
            (tuple(golden.normalize_cell(v) for v in r) for r in rows),
            key=lambda r: (r[key[0]], r[key[1]]),
        )

    assert normal(got) == normal(seed)
    assert len(got) == pins.DIM_USER_ROWS


def test_goldens_match_with_federated_dims(built: str) -> None:
    """The three goldens off the swapped build are byte-identical to the frozen
    files — the source swap changed where dims come from and nothing else."""
    for spec in GOLDENS:
        table = spec.relation.rsplit(".", 1)[1]
        sql = golden.select_sql(spec, f"`{built}.{MODELS_DATASET}.{table}`")
        rows = golden.rows_from(
            [tuple(r.values()) for r in _bq(built).query(sql).result()]
        )
        assert golden.render(rows, spec) == (FIXTURES / spec.file).read_text(), (
            spec.file
        )


def _send_schedule_rows(project: str) -> list[tuple]:
    client = spanner_wb.GoogleSpannerClient(project)
    return client.read(
        "select "
        + ", ".join(SEND_SCHEDULE_GOLDEN.columns)
        + " from send_schedule order by user_id"
    )


def _rendered_hash(rows: list[tuple]) -> str:
    rendered = golden.render(golden.rows_from(rows), SEND_SCHEDULE_GOLDEN)
    return hashlib.sha256(rendered.encode()).hexdigest()


def test_spanner_writeback_twice_writes_zero_and_matches_pin(built: str) -> None:
    """Done-when 1: two runs over the same scores — the second writes 0 and the
    row hash is unchanged; and the read-back equals the DuckDB stand-in's
    pinned hash byte-for-byte (the serving contract did not move across
    stores)."""
    n, written = spanner_wb.write_back(built)
    assert n == pins.SEND_SCHEDULE_ROWS_TINY
    first = _send_schedule_rows(built)
    assert len(first) == pins.SEND_SCHEDULE_ROWS_TINY
    n, written = spanner_wb.write_back(built)
    assert (n, written) == (pins.SEND_SCHEDULE_ROWS_TINY, 0)  # idempotent
    second = _send_schedule_rows(built)
    assert second == first  # the row hash cannot have moved
    assert _rendered_hash(second) == pins.SEND_SCHEDULE_SHA256_TINY
