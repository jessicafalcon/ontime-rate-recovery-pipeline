"""Attribution pins (spec Phase 3 invariants 1, 3, 4, 5, 7, 10): the real
`dbt build` of fixtures/tiny (tests/test_staging.py's in-process build), then
every assertion reads the attribution table or the golden. No service."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_staging import build, q

from eval import golden
from generator.models import Cause
from tests import pins

ROOT = Path(__file__).parent.parent
DBT = ROOT / "dbt"
EXPECTED = ROOT / "fixtures" / "tiny" / "expected" / "attribution.csv"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("attr") / "tiny.duckdb"
    assert build(db)
    return db


def _var(v: str) -> float | str:
    try:
        return float(v)
    except ValueError:  # Phase 5: model_version is a string literal
        return v.strip()


def project_vars() -> dict[str, float | str]:
    """The `vars:` block of dbt_project.yml, without a YAML package (pyyaml is
    dbt's transitive dependency, not ours)."""
    text = (DBT / "dbt_project.yml").read_text()
    block = text.split("\nvars:\n", 1)[1].split("\n\n", 1)[0]
    return {k.strip(): _var(v) for k, v in (ln.split(":") for ln in block.splitlines())}


def label_counts(db: Path) -> dict[str, int]:
    rows = q(db, "select label, count(*) from main_attribution.attribution group by 1")
    return {label: n for label, n in rows}


def test_label_counts_match_pin(built: Path) -> None:
    counts = label_counts(built)
    assert counts == pins.ATTRIBUTION_LABEL_COUNTS
    assert sum(counts.values()) == pins.ATTRIBUTION_ROWS
    assert set(counts) == {c.value for c in Cause}
    (n,) = q(built, "select count(*) from main_staging.stg_prompts")[0]
    assert n == pins.ATTRIBUTION_ROWS


def test_unattributed_share_matches_pin(built: Path) -> None:
    counts = label_counts(built)
    share = counts["unattributed"] / sum(counts.values())
    assert share == pytest.approx(pins.UNATTRIBUTED_SHARE)
    assert share <= project_vars()["unattributed_max"]


def test_skew_var_equals_generator_pin() -> None:
    from generator.models import SKEW_MAX_MIN

    v = project_vars()
    assert v["skew_max_min"] == SKEW_MAX_MIN == pins.SKEW_MAX_MIN
    assert set(v) == {
        "skew_max_min",
        "delivery_grace_min",
        "unattributed_max",
        "retention_days",
        "feature_window_days",  # Phase 5
        "max_user_shift_min",
        "shrinkage_pseudo_count",
        "model_version",
        "lookback_days",  # Phase 7
    }


def test_skew_is_negative_only_on_tiny(built: Path) -> None:
    """Every prompt with a client clock ahead past the bound is unattributed;
    a large POSITIVE delay never is (the offline upload faults reach 22090 s)."""
    bound = -pins.SKEW_MAX_MIN * 60
    rows = q(
        built,
        "select label, min_upload_delay_seconds from main_attribution.attribution",
    )
    assert all(label == "unattributed" for label, d in rows if d < bound)
    assert sum(d < bound for _, d in rows) == pins.TRUTH_LABEL_COUNTS["unattributed"]
    positive = q(
        built,
        "select max(upload_delay_seconds) from main_staging.stg_events e "
        "join main_attribution.attribution a using (prompt_id) "
        "where a.label = 'upload_fault'",
    )[0][0]
    assert positive == pins.STG_DELAY_RANGE_SECONDS[1]


def test_golden_matches_fixture(built: Path) -> None:
    rows = golden.export_rows(built)
    assert len(rows) == pins.ATTRIBUTION_ROWS
    assert golden.diff_rows(rows, golden.parse(EXPECTED.read_text())) == []
    assert golden.render(rows) == EXPECTED.read_text()  # byte-identical CSV


def test_two_builds_give_the_same_golden(built: Path, tmp_path: Path) -> None:
    db2 = tmp_path / "again.duckdb"
    assert build(db2)
    assert golden.export_rows(built) == golden.export_rows(db2)


def test_build_under_a_non_utc_host_zone_is_identical(built: Path, tmp_path: Path):
    db2 = tmp_path / "tokyo.duckdb"
    code = (
        "import sys; from pathlib import Path; from tests.test_staging import build; "
        "sys.exit(0 if build(Path(sys.argv[1])) else 1)"
    )
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONPATH": str(ROOT)}
    subprocess.run(
        [sys.executable, "-c", code, str(db2)], cwd=ROOT, env=env, check=True
    )
    assert golden.export_rows(built) == golden.export_rows(db2)


def test_cohort_is_the_prompts_and_agrees_with_dim_on_tiny(built: Path) -> None:
    rows = q(
        built,
        "select count(*) from main_attribution.attribution a "
        "join main_staging.stg_prompts p using (prompt_id) "
        "where a.cohort_id != p.prompt_cohort_id or p.prompt_cohort_id != p.cohort_id",
    )
    assert rows[0][0] == 0


def test_unit_tests_cover_every_arm_and_adjacent_pair() -> None:
    """Spec invariant 2 names 13 unit tests; each must exist in schema.yml."""
    schema = (DBT / "models" / "attribution" / "schema.yml").read_text()
    unit = schema.split("\nunit_tests:\n", 1)[1]
    names = set(re.findall(r"^  - name: (\S+)$", unit, re.M))
    assert {
        "attribution_delivery_fault_no_receipt",
        "attribution_delivery_fault_receipt_after_grace",
        "attribution_skew_negative_delay",
        "attribution_on_time",
        "attribution_upload_fault_received_after_window",
        "attribution_upload_fault_failed_chain",
        "attribution_timing_gap",
        "attribution_residual_is_unattributed",
        "attribution_delivery_fault_beats_everything",
        "attribution_skew_beats_on_time",
        "attribution_on_time_beats_upload_fault",
        "attribution_upload_fault_beats_timing_gap",
        "attribution_cohort_is_the_prompts",
    } <= names
    assert len(re.findall(r"^    model: attribution$", unit, re.M)) == len(names)
