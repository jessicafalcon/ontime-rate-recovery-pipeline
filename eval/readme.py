"""Phase 13 — the README first-screen block and the findings chart, rendered
from `tests/pins.py` (which the committed `docs/RESULTS.md` blocks are pinned
to) by the same marker-confined writer Phase 6 uses (`eval/blocks.py`). Not one
number here is typed by a human: a drift is a red test (`tests/test_readme.py`),
never a
hand-edited constant. `first_screen_rows()` reads the pins; `render_block`
formats the README table; `render_svg` draws the deterministic bar chart —
integer coordinates, no clock, no order dependence, so it regenerates
byte-identically."""

from __future__ import annotations

from eval.simulate import ontime_rate

BEGIN = "<!-- readme:begin -->"
END = "<!-- readme:end -->"


def _sim_rate(counts: dict[str, int]) -> float:
    """on_time / (sent − delivery_fault) via `simulate.ontime_rate`, so the
    README's tiny rate is computed exactly as the RESULTS block's is — the two
    cannot diverge (a simulated arm re-draws delivery_fault)."""
    rate = ontime_rate(counts)
    assert rate is not None  # a simulated arm always delivers something
    return rate


def first_screen_rows() -> dict[str, object]:
    """The headline figures, every one a pin (or derived from pins) — the
    numbers backing the committed RESULTS blocks, read here, never restated."""
    from tests import pins  # every pin lives there

    tiny_base = _sim_rate(pins.SIMULATED_TINY["baseline"])
    tiny_reco = _sim_rate(pins.SIMULATED_TINY["recommended"])
    med_base, med_cohort, med_reco = pins.SIMULATED_MEDIUM_ONTIME_RATE
    return {
        "label_accuracy_tiny": pins.LABEL_ACCURACY,
        "mae_tiny": pins.MAE_TINY,
        "mae_medium": pins.MAE_MEDIUM,
        "coverage_tiny": pins.COVERAGE_TINY,
        "coverage_medium": pins.COVERAGE_MEDIUM,
        "ontime_tiny": pins.ONTIME_RATE,
        "ontime_medium": pins.ONTIME_RATE_MEDIUM,
        "lift_tiny": tiny_reco - tiny_base,
        "lift_medium": med_reco - med_base,
        "sim_medium": (med_base, med_cohort, med_reco),
    }


def render_block(rows: dict[str, object]) -> str:
    """The `readme:begin` markdown table + a one-line honest note (no volatile
    number in the prose)."""
    table = [
        (
            "label accuracy vs generator truth",
            f"{rows['label_accuracy_tiny']:.3f}",
            "—",
        ),
        (
            "reachable-centre MAE (hours)",
            f"{rows['mae_tiny']:.6f}",
            f"{rows['mae_medium']:.6f}",
        ),
        (
            "reachable-window coverage",
            f"{rows['coverage_tiny']:.4f}",
            f"{rows['coverage_medium']:.4f}",
        ),
        (
            "built on-time rate",
            f"{rows['ontime_tiny']:.6f}",
            f"{rows['ontime_medium']:.6f}",
        ),
        (
            "simulated lift (recommended − baseline)",
            f"{rows['lift_tiny']:+.6f}",
            f"{rows['lift_medium']:+.6f}",
        ),
    ]
    lines = [
        "| metric | tiny (frozen fixture) | medium (2,000 users, unfrozen) |",
        "|---|---|---|",
        *(f"| {label} | {tiny} | {medium} |" for label, tiny, medium in table),
        "",
        "Tiny's lift is a regression pin (a 20-user cohort bin-tie), not a "
        "result; medium is the proof. The simulation is counterfactual, not an "
        "A/B — see [docs/INSIGHT.md](docs/INSIGHT.md).",
    ]
    return "\n".join(lines) + "\n"


def render_svg(rows: dict[str, object]) -> str:
    """The findings chart: medium simulated on-time rate by schedule arm. All
    coordinates are integers derived from the rates, so the bytes are a pure
    function of the pins."""
    base, cohort, reco = rows["sim_medium"]  # type: ignore[misc]
    axis_max = 0.70
    plot_h, floor_y = 220, 260
    x0, bw, gap = 120, 120, 60
    bars = (
        ("baseline", base, "#9aa4b2"),
        ("cohort", cohort, "#9aa4b2"),
        ("recommended", reco, "#2f6f4f"),
    )
    # explicit ceiling: a rate above axis_max would draw a bar/label off-canvas
    assert all(0.0 <= rate <= axis_max for _, rate, _ in bars), (
        f"a simulated rate exceeds axis_max={axis_max}; widen the axis"
    )
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="320" '
        'viewBox="0 0 680 320" role="img" '
        'aria-label="Simulated on-time rate by schedule, medium profile">',
        '<rect width="680" height="320" fill="#ffffff"/>',
        '<text x="40" y="28" font-family="sans-serif" font-size="18" '
        'font-weight="bold" fill="#1b1f24">Simulated on-time rate by schedule '
        "(medium, 60,000 prompts)</text>",
        f'<line x1="80" y1="{floor_y}" x2="640" y2="{floor_y}" '
        'stroke="#c7ced6" stroke-width="1"/>',
    ]
    for i, (name, rate, colour) in enumerate(bars):
        h = round(rate / axis_max * plot_h)
        x = x0 + i * (bw + gap)
        y = floor_y - h
        cx = x + bw // 2
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bw}" height="{h}" fill="{colour}"/>'
        )
        parts.append(
            f'<text x="{cx}" y="{y - 10}" font-family="sans-serif" '
            f'font-size="16" text-anchor="middle" fill="#1b1f24">{rate:.6f}</text>'
        )
        parts.append(
            f'<text x="{cx}" y="{floor_y + 22}" font-family="sans-serif" '
            f'font-size="15" text-anchor="middle" fill="#4a5461">{name}</text>'
        )
    parts.append(
        f'<text x="40" y="312" font-family="sans-serif" font-size="12" '
        f'fill="#6b7480">Counterfactual simulation (docs/RESULTS.md); the served '
        f"schedule lifts on-time rate {reco - base:+.6f} over baseline.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
