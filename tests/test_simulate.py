"""Phase 6 — the counterfactual simulation (specs/phase-6-simulation.md
invariants 1–7, 9, 11). tiny against the pins and the committed block;
medium seeded + built in-process against its committed block."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from test_attribution import built as built  # noqa: PLC0414 — module fixture
from test_scores import build_profile

from eval import blocks, score, simulate
from generator import cli as gen_cli
from landing import load as landing
from tests import pins

ROOT = landing.ROOT
TRUTH_TINY = ROOT / "fixtures" / "tiny" / "truth"
RESULTS = ROOT / "docs" / "RESULTS.md"


def _rows(
    db: Path,
    truth: Path = TRUTH_TINY,
    profile: str = "tiny",
    seed: int = pins.SIMULATE_SEED,
):
    return simulate.arm_rows(db, truth, profile, seed)


def _labels(db: Path, arm: str, truth: Path = TRUTH_TINY, profile: str = "tiny"):
    prompts = simulate.read_prompts(truth / "prompts.jsonl")
    latent = simulate.read_latent(truth / "users.jsonl")
    k = simulate.knobs(profile)
    u = simulate.draw_uniforms(len(prompts), pins.SIMULATE_SEED)
    hours = simulate.schedules(simulate.built_schedule(db))
    return simulate.label_prompts(prompts, hours[arm], u, latent, k), (
        prompts,
        latent,
        k,
        u,
    )


# ------------------------------------------------- tiny: pins and identities


def test_tiny_arm_counts_match_pins(built: Path) -> None:  # noqa: F811
    """Invariants 3, 7: the data row is the attribution pin; the arms are the
    numbers read off the first green run."""
    rows = dict(_rows(built))
    assert rows["data"] == pins.ATTRIBUTION_LABEL_COUNTS
    for arm in simulate.ARMS:
        assert rows[arm] == pins.SIMULATED_TINY[arm], arm


def test_every_arm_partitions_prompts_sent(built: Path) -> None:  # noqa: F811
    for name, counts in _rows(built):
        assert set(counts) == set(score.LABELS), name
        assert sum(counts.values()) == pins.STG_PROMPT_ROWS, name
        delivered = pins.STG_PROMPT_ROWS - counts["delivery_fault"]
        assert simulate.ontime_rate(counts) == pytest.approx(
            counts["on_time"] / delivered
        )


def test_ontime_rate_is_null_only_when_nothing_is_delivered() -> None:
    zero = {label: 0 for label in score.LABELS}
    assert simulate.ontime_rate({**zero, "delivery_fault": 5}) is None
    assert simulate.ontime_rate({**zero, "timing_gap": 5}) == 0.0


def test_fixed_causes_are_identical_across_arms(built: Path) -> None:  # noqa: F811
    """Invariant 4: the same u1, u2 in every arm."""
    rows = dict(_rows(built))
    for cause in ("delivery_fault", "unattributed"):
        assert len({rows[arm][cause] for arm in simulate.ARMS}) == 1, cause


def test_only_timing_gap_moves_between_arms(built: Path) -> None:  # noqa: F811
    """Invariants 4, 5 at the prompt level: a label differs between two arms
    only as timing_gap ↔ {on_time, upload_fault}; a prompt that responds in
    both carries the same upload verdict (same u4) — lateness never enters."""
    labelled = {arm: _labels(built, arm)[0] for arm in simulate.ARMS}
    moved = 0
    for a in simulate.ARMS:
        for b in simulate.ARMS:
            for pid, la in labelled[a].items():
                lb = labelled[b][pid]
                if la == lb:
                    continue
                moved += 1
                assert {la, lb} & {"timing_gap"} and {la, lb} <= {
                    "timing_gap",
                    "on_time",
                    "upload_fault",
                }, (pid, la, lb)
    assert moved > 0  # the schedules really differ on tiny


def test_cause_of_follows_the_generator_order() -> None:
    from generator.models import LatentUser

    user = LatentUser(
        user_id="u",
        cohort_id="c",
        reachable_center_local_hour=8.0,
        reachable_width_hours=6.0,
    )
    k = simulate.Knobs(0.1, 0.05, 0.15, 60)
    assert simulate.cause_of((0.0, 0.0, 0.0, 0.0), 8.0, user, k) == "delivery_fault"
    assert simulate.cause_of((0.5, 0.0, 0.0, 0.0), 8.0, user, k) == "unattributed"
    assert simulate.cause_of((0.5, 0.5, 0.99, 0.0), 8.0, user, k) == "timing_gap"
    assert simulate.cause_of((0.5, 0.5, 0.0, 0.0), 8.0, user, k) == "upload_fault"
    assert simulate.cause_of((0.5, 0.5, 0.0, 0.5), 8.0, user, k) == "on_time"
    # 12 h off the centre: open_probability ≈ 0 → timing_gap for any u3 > 0
    assert simulate.cause_of((0.5, 0.5, 0.01, 0.5), 20.0, user, k) == "timing_gap"


# ------------------------------------------------- served only, monotone sanity


def test_recommended_arm_reads_the_served_pair_not_the_centre(
    built: Path, tmp_path: Path
) -> None:  # noqa: F811
    """Invariant 1: a centre planted 12 h from the served time changes no
    block."""
    before = simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED)
    copy = tmp_path / "planted.duckdb"
    con = duckdb.connect(str(built))
    try:
        con.execute("checkpoint")  # dbt's handle may hold the build in the WAL
    finally:
        con.close()
    shutil.copy(built, copy)
    con = duckdb.connect(str(copy))
    try:
        con.execute(
            "update main_scores.scores_send_time set center_hour_local = "
            "send_hour_local + send_minute_local / 60.0 + 12 - "
            "24 * floor((send_hour_local + send_minute_local / 60.0 + 12) / 24)"
        )
        (moved,) = con.execute(
            "select count(*) from main_scores.scores_send_time where "
            "abs(center_hour_local - (send_hour_local + send_minute_local / 60.0)) > 6"
        ).fetchone()
    finally:
        con.close()
    assert moved == pins.SCORES_ROWS
    assert simulate.render(copy, TRUTH_TINY, "tiny", pins.SIMULATE_SEED) == before


def test_cohort_arm_reads_the_band_anchor(built: Path) -> None:  # noqa: F811
    sched = simulate.built_schedule(built)
    hours = simulate.schedules(sched)
    p = simulate.Prompt("p-000001", "u-000001", 20.0)
    anchor = pins.COHORT_HOUR_TINY["c-evening"]  # u-000001 is c-evening
    assert hours["cohort"](p) == float(anchor)
    assert hours["baseline"](p) == 20.0
    assert hours["recommended"](p) == sched["u-000001"][0]


def test_schedule_at_the_latent_centre_bounds_the_recommended_arm(
    built: Path,
) -> None:  # noqa: F811
    """Invariant 6: the arm at every user's latent centre scores ≥ the
    recommended arm."""
    _, (prompts, latent, k, u) = _labels(built, "recommended")
    rec = dict(_rows(built))["recommended"]
    at_centre = simulate.simulate_arm(
        prompts, lambda p: latent[p.user_id].reachable_center_local_hour, u, latent, k
    )
    assert simulate.ontime_rate(at_centre) >= simulate.ontime_rate(rec)


def test_schedule_twelve_hours_off_bounds_the_baseline(built: Path) -> None:  # noqa: F811
    _, (prompts, latent, k, u) = _labels(built, "baseline")
    base = dict(_rows(built))["baseline"]
    off = simulate.simulate_arm(
        prompts,
        lambda p: (latent[p.user_id].reachable_center_local_hour + 12.0) % 24.0,
        u,
        latent,
        k,
    )
    assert simulate.ontime_rate(off) <= simulate.ontime_rate(base)
    assert (
        off["delivery_fault"] == base["delivery_fault"]
    )  # CRN holds for a planted arm too


# ------------------------------------------------- determinism and the block


def test_tiny_block_matches_the_committed_block(built: Path) -> None:  # noqa: F811
    """Invariants 2, 9: the committed tiny block is the rendered one, byte
    for byte (causes in LABELS order, arms in ARMS order)."""
    text = RESULTS.read_text()
    committed = blocks.find_block(
        text, simulate.BEGIN.format(profile="tiny"), simulate.END.format(profile="tiny")
    )
    rendered = simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED)
    assert committed == rendered
    lines = rendered.splitlines()
    assert lines[0].startswith("| arm | " + " | ".join(score.LABELS) + " |")
    assert [ln.split(" | ")[0].strip("| ") for ln in lines[2:6]] == [
        "data",
        *simulate.ARMS,
    ]


def test_two_renders_are_byte_identical(built: Path) -> None:  # noqa: F811
    a = simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED)
    b = simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED)
    assert a == b


def test_a_different_seed_gives_a_different_block(built: Path) -> None:  # noqa: F811
    a = simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED)
    b = simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED + 1)
    assert a != b
    assert simulate.draw_uniforms(3, 1) == simulate.draw_uniforms(3, 1)
    assert simulate.draw_uniforms(3, 1) != simulate.draw_uniforms(3, 2)


def test_render_under_a_non_utc_host_zone_is_identical(built: Path) -> None:  # noqa: F811
    code = (
        "import sys; from pathlib import Path; from eval import simulate; "
        "from tests import pins\n"
        "sys.stdout.write(simulate.render(Path(sys.argv[1]), Path(sys.argv[2]), "
        "'tiny', pins.SIMULATE_SEED))"
    )
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONPATH": str(ROOT)}
    out = subprocess.run(
        [sys.executable, "-c", code, str(built), str(TRUTH_TINY)],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out == simulate.render(built, TRUTH_TINY, "tiny", pins.SIMULATE_SEED)


# ------------------------------------------------- by construction


def test_simulation_draws_no_time_quantity() -> None:
    """Invariant 5: no upload delay, no clock column, no duration is drawn
    or written — the arms differ in causes, never in lateness."""
    src = (ROOT / "eval" / "simulate.py").read_text()
    for token in (
        "upload_delay",
        "server_received",
        "server_upload",
        "timedelta",
        "datetime",
        "_secs",
        "uniform(",
        "responds",
    ):
        assert token not in src, token
    assert "open_probability" in src


def test_simulation_has_no_clock_call() -> None:
    for name in ("simulate.py", "power.py", "blocks.py"):
        src = (ROOT / "eval" / name).read_text()
        for token in ("now(", "time.time", "datetime.now", "utcnow"):
            assert token not in src, (name, token)


# ------------------------------------------------- medium: the proof


def test_medium_block_matches_the_committed_block(tmp_path: Path, capsys) -> None:
    """Seeds medium into data/out/medium/ (idempotent), builds into a tmp
    DuckDB, renders, and equals the committed block byte for byte; the
    recommended arm beats baseline and the fixed causes hold."""
    assert gen_cli.seed("medium") == 0
    assert "seed OK" in capsys.readouterr().out
    db = tmp_path / "medium.duckdb"
    assert build_profile("medium", db)
    truth = ROOT / "data" / "out" / "medium" / "truth"
    rows = simulate.arm_rows(db, truth, "medium", pins.SIMULATE_SEED)
    rendered = simulate.render_block("medium", rows)
    committed = blocks.find_block(
        RESULTS.read_text(),
        simulate.BEGIN.format(profile="medium"),
        simulate.END.format(profile="medium"),
    )
    assert committed == rendered
    by = dict(rows)
    rates = tuple(round(simulate.ontime_rate(by[a]), 6) for a in simulate.ARMS)
    assert rates == pins.SIMULATED_MEDIUM_ONTIME_RATE
    assert rates[2] > rates[0]  # the proof: the served schedule lifts on-time
    assert sum(by["data"].values()) == pins.PROMPTS_SENT_MEDIUM
    for arm in simulate.ARMS:
        assert sum(by[arm].values()) == pins.PROMPTS_SENT_MEDIUM
        assert by[arm]["delivery_fault"] == by["baseline"]["delivery_fault"]
        assert by[arm]["unattributed"] == by["baseline"]["unattributed"]
