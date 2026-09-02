"""Send-time model pins (spec Phase 5 invariants 1–3, 8, 9): the real
`dbt build` of fixtures/tiny (tests/test_staging.py's in-process build), then
every assertion reads features_user_hour / scores_send_time or the golden;
plus the medium profile — seeded in-process into data/out/medium/ (the
generator's gitignored working dir, byte-identical on every run) and built
into a tmp DuckDB — for the MAE / coverage proof. No service, no network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_attribution import built as built  # noqa: PLC0414 — module fixture
from test_staging import DBT, build, q

from eval import golden, score
from generator import cli as gen_cli
from landing import load as landing
from tests import pins

ROOT = Path(__file__).parent.parent
TINY = ROOT / "fixtures" / "tiny"
EXPECTED = TINY / golden.SCORES_SEND_TIME.file
SPEC = golden.SCORES_SEND_TIME


def build_profile(profile: str, db: Path) -> bool:
    """tests/test_staging.py::build for any profile the landing can resolve."""
    os.environ.setdefault("DO_NOT_TRACK", "1")
    from dbt.cli.main import dbtRunner

    landing.load(profile, db)
    args = ["build", "--project-dir", str(DBT), "--profiles-dir", str(DBT)]
    args += ["--target", "duckdb", "--quiet", "--target-path", str(db.parent / "t")]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OTR_DUCKDB_PATH", str(db))
        return bool(dbtRunner().invoke(args).success)


def _scores(db: Path) -> tuple[list, list]:
    return (
        golden.export_rows(db, SPEC),
        q(
            db,
            "select * from main_features.features_user_hour "
            "order by user_id, hour_local",
        ),
    )


# ------------------------------------------------- tiny: features


def test_tiny_features_match_organic_open_pin(built: Path) -> None:  # noqa: F811
    """Invariant 1 on the fixture: the histogram sums to the staged organic
    opens (every open is inside the 30-day window on a 7-day fixture), one
    histogram per user — the tz-change users included (invariant 2)."""
    (total, users, rows) = q(
        built,
        "select sum(n_opens), count(distinct user_id), count(*) "
        "from main_features.features_user_hour",
    )[0]
    assert total == pins.ORGANIC_OPEN_ROWS
    assert users == pins.SCORES_ROWS
    assert rows == 140
    for uid, n in (("u-000008", 11), ("u-000010", 12)):  # 9 + 2 and 7 + 5 opens
        (got,) = q(
            built,
            f"select sum(n_opens) from main_features.features_user_hour "
            f"where user_id = '{uid}'",
        )[0]
        assert got == n, uid
    (bins,) = q(built, "select max(hour_local) from main_features.features_user_hour")[
        0
    ]
    assert bins <= 23


# ------------------------------------------------- tiny: scores


def test_scores_golden_matches_fixture(built: Path) -> None:  # noqa: F811
    rows = golden.export_rows(built, SPEC)
    assert len(rows) == pins.SCORES_ROWS
    assert golden.render(rows, SPEC) == EXPECTED.read_text()
    assert golden.diff_rows(rows, golden.parse(EXPECTED.read_text(), SPEC), 1) == []


def test_scores_depends_on_dim_user_current_not_raw(built: Path) -> None:  # noqa: F811
    """The layering fix (fix/scores-dim-current): scores_send_time reads the
    open dim row through the dim_user_current mart, not the raw dim_user source.
    dbt's own manifest (the artifact of the build just run under `built`) is the
    proof the DAG edge moved — dim_user_current is now upstream, the raw source
    no longer directly."""
    manifest = json.loads((built.parent / "t" / "manifest.json").read_text())
    (node,) = [
        n for k, n in manifest["nodes"].items() if k.endswith(".scores_send_time")
    ]
    deps = node["depends_on"]["nodes"]
    assert any(d.endswith(".dim_user_current") for d in deps), deps
    assert not any(d.endswith(".raw.dim_user") for d in deps), deps


def test_cohort_moments_and_as_of_match_pins(built: Path) -> None:  # noqa: F811
    """Invariant 7 on the fixture: c-morning's bins 3 and 10 tie at 12 pooled
    opens and the smaller hour wins."""
    moments = dict(
        q(
            built,
            "select distinct cohort_id, cohort_hour_local "
            "from main_scores.scores_send_time",
        )
    )
    assert moments == pins.COHORT_HOUR_TINY
    (m3, m10) = (
        q(
            built,
            "select sum(f.n_opens) from main_features.features_user_hour f "
            "join raw.dim_user d on d.user_id = f.user_id and d.valid_to is null "
            f"where d.cohort_id = 'c-morning' and f.hour_local = {h}",
        )[0][0]
        for h in (3, 10)
    )
    assert m3 == m10 == 12


def test_computed_as_of_is_the_window_max(built: Path) -> None:  # noqa: F811
    (distinct, as_of) = q(
        built,
        "select count(distinct computed_as_of), min(computed_as_of) "
        "from main_scores.scores_send_time",
    )[0]
    assert distinct == 1
    assert str(as_of) == pins.COMPUTED_AS_OF_TINY
    (open_max,) = q(
        built,
        "select max(client_event_time) from main_staging.stg_events "
        "where event_type = 'app_opened'",
    )[0]
    assert as_of == open_max
    (all_max,) = q(built, "select max(client_event_time) from main_staging.stg_events")[
        0
    ]
    assert all_max > as_of  # the horizon is a later, non-organic event


def test_model_version_is_the_var(built: Path) -> None:  # noqa: F811
    assert q(
        built, "select distinct model_version from main_scores.scores_send_time"
    ) == [("v1",)]
    assert "  model_version: v1\n" in (DBT / "dbt_project.yml").read_text()


def test_vars_equal_the_pins() -> None:
    text = (DBT / "dbt_project.yml").read_text()
    assert f"  feature_window_days: {pins.FEATURE_WINDOW_DAYS}\n" in text
    assert f"  max_user_shift_min: {pins.MAX_USER_SHIFT_MIN}\n" in text
    assert f"  shrinkage_pseudo_count: {pins.SHRINKAGE_PSEUDO_COUNT}\n" in text


def test_every_served_time_is_inside_the_band_and_in_range(built: Path) -> None:  # noqa: F811
    """Invariants 5 and 6 recomputed in Python from the served columns."""
    rows = q(
        built,
        "select send_hour_local, send_minute_local, cohort_hour_local, "
        "center_hour_local, confidence from main_scores.scores_send_time",
    )
    assert len(rows) == pins.SCORES_ROWS
    for h, m, moment, center, conf in rows:
        assert 0 <= h <= 23 and 0 <= m <= 59
        assert 0 <= center < 24 and 0 <= conf <= 1
        served = h + m / 60
        assert (
            score.circular_abs_diff_hours(served, moment)
            <= pins.MAX_USER_SHIFT_MIN / 60 + 1e-9
        )
        # a centre inside the band is served as itself (to the minute)
        if score.circular_abs_diff_hours(center, moment) < pins.MAX_USER_SHIFT_MIN / 60:
            assert score.circular_abs_diff_hours(served, center) <= 1 / 60 + 1e-9


def test_tiny_mae_and_coverage_match_pins(built: Path) -> None:  # noqa: F811
    windows = score.truth_windows(TINY / "truth" / "users.jsonl")
    scores = score.built_scores(built)
    assert len(windows) == len(scores) == pins.SCORES_ROWS
    assert score.reachable_center_mae(scores, windows) == pytest.approx(
        pins.MAE_TINY, abs=1e-9
    )
    assert score.coverage(scores, windows) == pytest.approx(
        pins.COVERAGE_TINY, abs=1e-9
    )


# ------------------------------------------------- determinism


def test_two_builds_give_the_same_features_and_scores(
    built: Path, tmp_path: Path
) -> None:  # noqa: F811
    db2 = tmp_path / "again.duckdb"
    assert build(db2)
    assert _scores(built) == _scores(db2)


def test_scores_under_a_non_utc_host_zone_are_identical(built: Path, tmp_path: Path):  # noqa: F811
    db2 = tmp_path / "tokyo.duckdb"
    code = (
        "import sys; from pathlib import Path; from tests.test_staging import build; "
        "sys.exit(0 if build(Path(sys.argv[1])) else 1)"
    )
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONPATH": str(ROOT)}
    subprocess.run(
        [sys.executable, "-c", code, str(db2)], cwd=ROOT, env=env, check=True
    )
    assert _scores(built) == _scores(db2)


# ------------------------------------------------- medium: the proof


def test_medium_mae_and_coverage_match_pins(tmp_path: Path, capsys) -> None:
    """Reconciliation item 1: medium is seeded (never frozen); the pins are
    its manifest. Seeds into data/out/medium/ (idempotent, byte-identical),
    builds into a tmp DuckDB, scores against data/out/medium/truth/."""
    assert gen_cli.seed("medium") == 0
    assert "seed OK" in capsys.readouterr().out
    db = tmp_path / "medium.duckdb"
    assert build_profile("medium", db)
    windows = score.truth_windows(
        landing.ROOT / "data" / "out" / "medium" / "truth" / "users.jsonl"
    )
    scores = score.built_scores(db)
    assert len(windows) == len(scores) == pins.MEDIUM_USERS
    mae = score.reachable_center_mae(scores, windows)
    assert mae <= pins.MAE_MEDIUM + 1e-9  # PHASES: "MAE ≤ pin on medium"
    assert mae == pytest.approx(pins.MAE_MEDIUM, abs=1e-9)  # and the regression pin
    assert score.coverage(scores, windows) == pytest.approx(
        pins.COVERAGE_MEDIUM, abs=1e-9
    )
    assert mae < pins.MAE_TINY  # more opens per user → a tighter recovery
