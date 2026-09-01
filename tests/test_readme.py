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
