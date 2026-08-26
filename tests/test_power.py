"""Phase 6 — the A/B power table (specs/phase-6-simulation.md invariant 10)."""

from __future__ import annotations

import pytest

from eval import blocks, power
from loader import load as loader
from tests import pins

AB_DESIGN = loader.ROOT / "docs" / "AB_DESIGN.md"


def test_z_quantile_inverts_the_normal_cdf() -> None:
    assert power.z_quantile(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert power.z_quantile(0.8) == pytest.approx(0.841621, abs=1e-6)
    assert power.z_quantile(0.5) == pytest.approx(0.0, abs=1e-9)
    for p in (0.01, 0.3, 0.9):
        assert power.normal_cdf(power.z_quantile(p)) == pytest.approx(p, abs=1e-9)
    for bad in (0.0, 1.0, -0.1):
        with pytest.raises(ValueError):
            power.z_quantile(bad)


def test_power_table_matches_pins() -> None:
    assert power.table_rows() == pins.POWER_TABLE
    assert pins.ONTIME_RATE_MEDIUM == pytest.approx(0.461143, abs=1e-6)


def test_sample_size_falls_with_mde_and_rises_with_power() -> None:
    p1 = pins.ONTIME_RATE
    sizes = [power.sample_size_per_arm(p1, pp / 100) for pp in (1, 2, 5, 10)]
    assert sizes == sorted(sizes, reverse=True) and len(set(sizes)) == 4
    assert power.sample_size_per_arm(p1, 0.02, power=0.9) > power.sample_size_per_arm(
        p1, 0.02, power=0.8
    )
    assert power.sample_size_per_arm(p1, 0.02, alpha=0.01) > power.sample_size_per_arm(
        p1, 0.02, alpha=0.05
    )
    with pytest.raises(ValueError):
        power.sample_size_per_arm(p1, 0.0)
    # the textbook check: p 0.5 → 0.55, α 0.05, power 0.8 ≈ 1,565 per arm
    assert power.sample_size_per_arm(0.5, 0.05) == pytest.approx(1565, abs=5)


def test_days_to_power_is_the_ceiling_of_the_ratio() -> None:
    assert power.days_to_power(100, 10, 1.0) == 10
    assert power.days_to_power(101, 10, 1.0) == 11
    assert power.days_to_power(100, 10, 0.5) == 20


def test_ab_design_block_matches_the_committed_block() -> None:
    committed = blocks.find_block(AB_DESIGN.read_text(), power.BEGIN, power.END)
    assert committed == power.render()


def test_ab_design_links_metrics_and_never_restates_ontime_rate() -> None:
    text = AB_DESIGN.read_text()
    assert "(METRICS.md#ontime_rate)" in text
    assert "sum(on_time)" not in text  # the definition lives in METRICS only
    for heading in ("holdout", "jitter", "Guardrails", "Randomisation unit"):
        assert heading in text
