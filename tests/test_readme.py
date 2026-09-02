"""Phase 13 — the README first-screen block, the findings chart, and the
cloud-free quickstart (specs/phase-13-docs-narrative.md invariants 1, 2, 4).

The two byte-identity tests are the CI proof that no number a reader sees is
typed by hand — the committed artifacts must equal what `eval/readme.py`
renders from `tests/pins.py`, exactly as `tests/test_power.py` pins the
AB_DESIGN block."""

from __future__ import annotations

import re

from eval import blocks, readme
from landing import load as landing

README = landing.ROOT / "README.md"
LIFT_SVG = landing.ROOT / "docs" / "img" / "lift.svg"

# A quickstart command is refused if it names a cloud-cost warehouse target, a
# CONFIRM gate, or a terraform target — the cold-reader path must be free.
_CLOUD_TOKENS = ("TARGET=bigquery", "TARGET=spanner", "CONFIRM=")
_CLOUD_TARGETS = ("tf-plan", "tf-apply", "tf-destroy", "tf-freeze", "tf-validate")
_MAKE = re.compile(r"^\s*make ([a-z][a-z0-9-]*)", re.M)


def test_first_screen_block_matches_committed() -> None:
    committed = blocks.find_block(README.read_text(), readme.BEGIN, readme.END)
    assert committed is not None, "README is missing the readme:begin/end markers"
    assert committed == readme.render_block(readme.first_screen_rows())


def test_lift_svg_matches_committed() -> None:
    assert LIFT_SVG.is_file(), (
        "docs/img/lift.svg is missing (run `make readme WRITE=yes`)"
    )
    assert LIFT_SVG.read_text() == readme.render_svg(readme.first_screen_rows())


def _quickstart_block() -> str:
    """The first fenced block under the Quickstart heading."""
    text = README.read_text()
    start = text.index("## Quickstart")
    fences = re.findall(r"```(.*?)```", text[start:], re.S)
    assert fences, "the Quickstart section has no fenced command block"
    return fences[0]


def test_quickstart_commands_are_cloud_free() -> None:
    block = _quickstart_block()
    targets = _MAKE.findall(block)
    assert "pipeline" in targets and "dbt-build" in targets  # it is the local chain
    for line in block.splitlines():
        if not line.strip().startswith("make "):
            continue
        assert not any(tok in line for tok in _CLOUD_TOKENS), f"cloud token: {line!r}"
        m = _MAKE.match(line)
        assert m and m.group(1) not in _CLOUD_TARGETS, f"cloud target: {line!r}"


def test_readme_write_takes_only_yes() -> None:
    from eval import cli

    # the check-mode default (empty) and the one write literal are the only
    # accepted values; anything else exits non-zero via die().
    assert cli.readme_cmd("") == 0  # check mode, block + svg match on a clean tree
    import pytest

    with pytest.raises(SystemExit):
        cli.readme_cmd("Yes")


def test_readme_write_persists_both_artifacts(tmp_path, monkeypatch) -> None:
    """WRITE=yes actually lands the block AND creates the SVG (the persistence
    path a `delete-call` on either write survived — round-1 finding #2)."""
    from eval import cli

    rows = readme.first_screen_rows()
    expected_block = readme.render_block(rows)
    expected_svg = readme.render_svg(rows)

    scratch_readme = tmp_path / "README.md"
    scratch_readme.write_text(f"top\n{readme.BEGIN}\nstale\n{readme.END}\nbottom\n")
    scratch_svg = tmp_path / "img" / "lift.svg"  # parent does not exist yet
    monkeypatch.setattr(cli, "README", scratch_readme)
    monkeypatch.setattr(cli, "LIFT_SVG", scratch_svg)

    assert cli.readme_cmd("yes") == 0
    written = blocks.find_block(scratch_readme.read_text(), readme.BEGIN, readme.END)
    assert written == expected_block  # write_block ran, not a no-op
    assert scratch_svg.read_text() == expected_svg  # write_text + mkdir ran


def test_readme_refuses_missing_file(tmp_path, monkeypatch) -> None:
    import pytest

    from eval import cli

    monkeypatch.setattr(cli, "README", tmp_path / "nope.md")
    with pytest.raises(SystemExit):
        cli.readme_cmd("")


def test_readme_refuses_missing_markers(tmp_path, monkeypatch) -> None:
    import pytest

    from eval import cli

    no_markers = tmp_path / "README.md"
    no_markers.write_text("no markers here\n")
    monkeypatch.setattr(cli, "README", no_markers)
    with pytest.raises(SystemExit):
        cli.readme_cmd("")


def test_structural_labels_derive_from_pins() -> None:
    """fix/front-door (BACKLOG row "structural labels … not pin-derived" closed):
    the user counts and the prompt volume in the block and the chart come from
    tests/pins.py, so a profile change moves them with the numbers."""
    from tests import pins

    rows = readme.first_screen_rows()
    block = readme.render_block(rows)
    svg = readme.render_svg(rows)
    assert f"**On the {pins.MEDIUM_USERS:,}-user profile" in block
    assert f"medium ({pins.MEDIUM_USERS:,} users, unfrozen)" in block
    assert f"(a {pins.SCORES_ROWS}-user cohort bin-tie)" in block
    assert f"(medium, {pins.PROMPTS_SENT_MEDIUM:,} prompts)" in svg
    assert "2,000" not in readme.render_block.__code__.co_consts  # no literal left
    assert "60,000" not in " ".join(map(str, readme.render_svg.__code__.co_consts))


def test_headline_points_equal_displayed_percentage_difference() -> None:
    """The +N is derived from the two DISPLAYED percentages, so the sentence
    cannot contradict itself on a pin refresh (functionality-tester gap #1)."""
    rows = readme.first_screen_rows()
    m = re.search(
        r"from (\d+)% to (\d+)% \(\+(\d+) points\)", readme.render_block(rows)
    )
    assert m, "headline shape"
    base, reco, points = map(int, m.groups())
    assert reco - base == points
    # a pair that rounds inconsistently under independent rounding still agrees
    rows2 = dict(rows, sim_medium=(0.464, 0.5, 0.626))
    m2 = re.search(
        r"from (\d+)% to (\d+)% \(\+(\d+) points\)", readme.render_block(rows2)
    )
    assert m2 and int(m2.group(2)) - int(m2.group(1)) == int(m2.group(3))


def test_headline_refuses_a_sub_point_lift() -> None:
    import pytest

    rows = dict(readme.first_screen_rows(), sim_medium=(0.461, 0.5, 0.463))
    with pytest.raises(AssertionError):
        readme.render_block(rows)
