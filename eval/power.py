"""Power calculation for the production A/B (Phase 6, docs/AB_DESIGN.md):
users per arm to detect a minimum detectable effect on the on-time rate
with the two-proportion normal approximation, and days to reach it at one
prompt per user-day. Standard library only: the normal quantile is a
bisection on `math.erf`. Rendered as the `power:begin` block."""

from __future__ import annotations

import math

ALPHA = 0.05
POWER = 0.8
MDE_PP = (1, 2, 5)  # percentage points
BEGIN = "<!-- power:begin -->"
END = "<!-- power:end -->"


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def z_quantile(p: float) -> float:
    """Inverse normal CDF by bisection on [-10, 10], to well under 1e-9."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def sample_size_per_arm(
    p1: float, mde: float, alpha: float = ALPHA, power: float = POWER
) -> int:
    """Two-sample z-test for proportions: users per arm to detect `p1 → p1 +
    mde` at `alpha` (two-sided) with `power`."""
    if mde <= 0:
        raise ValueError("mde must be positive")
    p2 = p1 + mde
    z = z_quantile(1 - alpha / 2) + z_quantile(power)
    n = z * z * (p1 * (1 - p1) + p2 * (1 - p2)) / (mde * mde)
    return math.ceil(n)


def days_to_power(n_per_arm: int, users_per_arm: int, delivered_share: float) -> int:
    """Days of one prompt per user-day until each arm has `n_per_arm`
    delivered prompts (the on-time denominator)."""
    return math.ceil(n_per_arm / (users_per_arm * delivered_share))


def table_rows() -> list[tuple[str, int, int, int]]:
    """(profile, mde_pp, n_per_arm, days) for both profiles' baseline rates
    — read from the pins, the mart's numbers."""
    from tests import pins  # every pin lives there

    profiles = (
        (
            "tiny",
            pins.ONTIME_RATE,
            pins.SCORES_ROWS,
            pins.PROMPTS_DELIVERED / pins.STG_PROMPT_ROWS,
        ),
        (
            "medium",
            pins.ONTIME_RATE_MEDIUM,
            pins.MEDIUM_USERS,
            pins.PROMPTS_DELIVERED_MEDIUM / pins.PROMPTS_SENT_MEDIUM,
        ),
    )
    rows: list[tuple[str, int, int, int]] = []
    for name, rate, users, share in profiles:
        for pp in MDE_PP:
            n = sample_size_per_arm(rate, pp / 100.0)
            rows.append((name, pp, n, days_to_power(n, users // 2, share)))
    return rows


def render_block(rows: list[tuple[str, int, int, int]]) -> str:
    lines = [
        "| profile | baseline ontime_rate | MDE (pp) | delivered prompts per arm "
        "| days at half the users per arm |",
        "|---|---|---|---|---|",
    ]
    from tests import pins

    rates = {"tiny": pins.ONTIME_RATE, "medium": pins.ONTIME_RATE_MEDIUM}
    for name, pp, n, days in rows:
        lines.append(f"| {name} | {rates[name]:.6f} | {pp} | {n} | {days} |")
    lines.append("")
    lines.append(
        f"Two-sided α = {ALPHA}, power = {POWER}, two-proportion normal "
        f"approximation (`eval/power.py`); the denominator is delivered prompts "
        f"(`docs/METRICS.md`), one prompt per user-day × the profile's delivered "
        f"share."
    )
    return "\n".join(lines) + "\n"


def render() -> str:
    return render_block(table_rows())
