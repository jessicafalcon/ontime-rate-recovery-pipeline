"""fix/holdout-eval (ROADMAP item 4) — the temporal holdout (ARCHITECTURE §7
report (d)). The served schedule, trained on data landed with an upload-date cut
(THROUGH), scored against the RAW organic app_opened opens uploaded AFTER the cut.

tiny (frozen fixture) is the cheap regression pin; medium (seeded into
data/out/medium/, byte-identical on every run) is the proof. Each profile's two
warehouses — served (≤ cut) and full (held-out opens) — are built once per module,
each in its own isolated subprocess (holdout.build); every assertion reads off
them. No service, no network, no truth."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from eval import blocks, holdout
from generator import cli as gen_cli
from landing import load as landing
from tests import pins

ROOT = landing.ROOT
RESULTS = ROOT / "docs" / "RESULTS.md"
WINDOW = pins.HOLDOUT_WINDOW_HOURS


def _built_dbs(profile: str, into: Path) -> tuple[Path, Path]:
    served = into / f"{profile}_served.duckdb"
    full = into / f"{profile}_full.duckdb"
    assert holdout.build(profile, served, pins.HOLDOUT_CUTS[profile]), profile
    assert holdout.build(profile, full, None), profile
    return served, full


@pytest.fixture(scope="module")
def tiny_dbs(tmp_path_factory) -> tuple[Path, Path]:
    return _built_dbs("tiny", tmp_path_factory.mktemp("holdout_tiny"))


@pytest.fixture(scope="module")
def medium_dbs(tmp_path_factory) -> tuple[Path, Path]:
    assert gen_cli.seed("medium") == 0  # idempotent into data/out/medium/
    return _built_dbs("medium", tmp_path_factory.mktemp("holdout_medium"))


def _results(profile: str, dbs: tuple[Path, Path]) -> dict[str, holdout.ArmResult]:
    served_db, full_db = dbs
    results = holdout.evaluate(
        holdout.served_schedule(served_db),
        holdout.heldout_opens(full_db, pins.HOLDOUT_CUTS[profile]),
        WINDOW,
    )
    return {r.arm: r for r in results}


def _committed(profile: str) -> str | None:
    return blocks.find_block(
        RESULTS.read_text(),
        holdout.BEGIN.format(profile=profile),
        holdout.END.format(profile=profile),
    )


def _render(profile: str, dbs: tuple[Path, Path]) -> str:
    served_db, full_db = dbs
    results = holdout.evaluate(
        holdout.served_schedule(served_db),
        holdout.heldout_opens(full_db, pins.HOLDOUT_CUTS[profile]),
        WINDOW,
    )
    return holdout.render_block(profile, pins.HOLDOUT_CUTS[profile], WINDOW, results)


# ------------------------------------------------- tiny: pins and the block


def test_tiny_arm_measures_match_pins(tiny_dbs: tuple[Path, Path]) -> None:
    by = _results("tiny", tiny_dbs)
    for arm, (share, nearest) in pins.HOLDOUT_TINY.items():
        assert round(by[arm].in_window_share, 6) == share, arm
        assert round(by[arm].mean_nearest_hours, 6) == nearest, arm


def test_tiny_recommended_beats_cohort(tiny_dbs: tuple[Path, Path]) -> None:
    """The non-circular signal, even on 20 users: the per-user served hour lands
    nearer real held-out opens than the cohort band anchor does."""
    by = _results("tiny", tiny_dbs)
    assert by["recommended"].in_window_share > by["cohort"].in_window_share
    assert by["recommended"].mean_nearest_hours < by["cohort"].mean_nearest_hours


def test_tiny_block_matches_committed(tiny_dbs: tuple[Path, Path]) -> None:
    assert _committed("tiny") == _render("tiny", tiny_dbs)


def test_arms_share_one_held_out_set(tiny_dbs: tuple[Path, Path]) -> None:
    """Both arms score the SAME users and opens — only the served hour differs."""
    by = _results("tiny", tiny_dbs)
    assert by["recommended"].n_users == by["cohort"].n_users == pins.SCORES_ROWS
    assert by["recommended"].n_opens == by["cohort"].n_opens


def test_two_evaluates_are_byte_identical(tiny_dbs: tuple[Path, Path]) -> None:
    assert _render("tiny", tiny_dbs) == _render("tiny", tiny_dbs)


# ------------------------------------------------- the cut semantics


def test_held_out_opens_are_strictly_after_the_cut(tiny_dbs: tuple[Path, Path]) -> None:
    """The held-out set is exactly the organic opens uploaded AFTER the cut
    (upload date = cast(server_upload_time as date)); the split is non-degenerate
    (opens on both sides)."""
    _, full_db = tiny_dbs
    cut = pins.HOLDOUT_CUTS["tiny"]
    con = duckdb.connect(str(full_db))
    try:
        after = con.execute(
            "select count(*) from main_staging.stg_events where "
            "event_type = 'app_opened' and cast(server_upload_time as date) > "
            "cast(? as date)",
            [cut],
        ).fetchone()[0]
        at_or_before = con.execute(
            "select count(*) from main_staging.stg_events where "
            "event_type = 'app_opened' and cast(server_upload_time as date) <= "
            "cast(? as date)",
            [cut],
        ).fetchone()[0]
    finally:
        con.close()
    opens = holdout.heldout_opens(full_db, cut)
    assert sum(len(v) for v in opens.values()) == after
    assert after > 0 and at_or_before > 0  # a real train-past / score-future split


def test_a_different_cut_changes_the_held_out_set(tiny_dbs: tuple[Path, Path]) -> None:
    """The cut is a real parameter, not a constant baked to match the pin: an
    earlier cut holds out more opens, a later one fewer. Kills a mutation that
    hardcodes HOLDOUT_CUTS to a fixed date."""
    _, full_db = tiny_dbs

    def n_opens(cut: str) -> int:
        return sum(len(v) for v in holdout.heldout_opens(full_db, cut).values())

    earlier = n_opens("2026-01-06")
    pinned = n_opens(pins.HOLDOUT_CUTS["tiny"])  # 2026-01-08
    later = n_opens("2026-01-10")
    assert earlier > pinned > later > 0  # monotone in the cut
    assert pinned == 94  # the pinned cut's held-out open count (the block's denom)


# ------------------------------------------------- non-circular by construction


def test_holdout_reads_no_truth_and_no_clock() -> None:
    """Invariant (ARCHITECTURE §7 report (d)): the only input is raw organic opens
    off the warehouse — never a truth file, never a reachable-window or centre
    column, no clock. The served arms read the SERVED columns only."""
    src = (ROOT / "eval" / "holdout.py").read_text()
    for token in (
        "prompts.jsonl",
        "users.jsonl",
        "truth_dir",
        "LatentUser",
        "reachable_center",
        "reachable_width",
        "center_hour_local",
        "now(",
        "utcnow",
        "datetime.now",
    ):
        assert token not in src, token
    assert "app_opened" in src  # the held-out set is organic opens
    assert "send_hour_local" in src and "cohort_hour_local" in src  # served columns


def test_evaluate_excludes_unserved_and_openless_users() -> None:
    """A user with no served row cannot be scored; a served user with no held-out
    open cannot score one — both are out of the denominators."""
    served = {"u-a": (8.0, 8.0), "u-b": (9.0, 9.0)}  # u-b has no held-out open
    opens = {"u-a": [8.4, 20.0], "u-c": [8.0]}  # u-c is unserved
    by = {r.arm: r for r in holdout.evaluate(served, opens, WINDOW)}
    assert by["recommended"].n_users == 1  # only u-a
    assert by["recommended"].n_opens == 2  # u-a's two opens
    # u-a: one open (8.4) within ±1 h of 8.0, one (20.0) far → share 0.5, nearest 0.4
    assert round(by["recommended"].in_window_share, 6) == 0.5
    assert by["recommended"].mean_nearest_hours == pytest.approx(0.4, abs=1e-9)


# ------------------------------------------------- medium: the proof


def test_medium_block_matches_committed(medium_dbs: tuple[Path, Path]) -> None:
    assert _committed("medium") == _render("medium", medium_dbs)


def test_medium_arm_measures_match_pins(medium_dbs: tuple[Path, Path]) -> None:
    by = _results("medium", medium_dbs)
    assert by["recommended"].n_users == pins.MEDIUM_USERS
    for arm, (share, nearest) in pins.HOLDOUT_MEDIUM.items():
        assert round(by[arm].in_window_share, 6) == share, arm
        assert round(by[arm].mean_nearest_hours, 6) == nearest, arm


def test_medium_recommended_beats_cohort_on_unseen_opens(
    medium_dbs: tuple[Path, Path],
) -> None:
    """The proof: at 2,000 users the served per-user schedule tracks real opens
    the model never saw — a higher in-window share and a shorter nearest distance
    than the cohort band, non-circular (no truth, no re-draw)."""
    by = _results("medium", medium_dbs)
    assert by["recommended"].in_window_share > by["cohort"].in_window_share
    assert by["recommended"].mean_nearest_hours < by["cohort"].mean_nearest_hours
