"""`docs/INSIGHT.md` types its figures by hand (DECISIONS: the essay, not a
generated block). Each typed figure must equal the pin it cites, and the set of
six-decimal figures in the page must be EXACTLY the pinned set — a new
hand-typed number is a red test until it is pinned here (BACKLOG: "INSIGHT
hand-types pinned figures with no value-parity test")."""

from __future__ import annotations

import re
from pathlib import Path

from tests import pins

INSIGHT = Path(__file__).parent.parent / "docs" / "INSIGHT.md"
MINUS = "−"  # the essay writes negatives with a typographic minus
_FIGURE = re.compile(rf"(?<![\d.])[+{MINUS}-]?\d\.\d{{6}}(?![\d.])")


def _signed(x: float) -> str:
    return f"{x:+.6f}".replace("-", MINUS)


def expected_figures() -> dict[str, str]:
    """Figure → the pin it renders from, formatted as the essay prints it."""
    base, _, rec = pins.SIMULATED_MEDIUM_ONTIME_RATE
    tiny = pins.SIMULATED_TINY
    delivered = pins.STG_PROMPT_ROWS - tiny["baseline"]["delivery_fault"]
    tiny_lift = (
        tiny["recommended"]["on_time"] - tiny["baseline"]["on_time"]
    ) / delivered
    return {
        "medium lift": _signed(rec - base),
        "medium baseline": f"{base:.6f}",
        "medium recommended": f"{rec:.6f}",
        "tiny lift": _signed(tiny_lift),
        "mae tiny": f"{pins.MAE_TINY:.6f}",
        "mae medium": f"{pins.MAE_MEDIUM:.6f}",
    }


def test_every_typed_figure_equals_its_pin() -> None:
    text = INSIGHT.read_text()
    for name, figure in expected_figures().items():
        assert figure in text, (name, figure)
    assert f"**{pins.LABEL_ACCURACY:.3f}**" in text
    assert f"{pins.MEDIUM_USERS:,}-user" in text
    assert f"{pins.SCORES_ROWS}-user" in text
    assert f"hour {pins.COHORT_HOUR_TINY['c-morning']}" in text


def test_six_decimal_figures_are_exactly_the_pinned_set() -> None:
    found = set(_FIGURE.findall(INSIGHT.read_text()))
    assert found == set(expected_figures().values()), found


def test_a_moved_pin_is_caught(monkeypatch) -> None:
    monkeypatch.setattr(pins, "MAE_MEDIUM", pins.MAE_MEDIUM + 0.01)
    assert expected_figures()["mae medium"] not in INSIGHT.read_text()
