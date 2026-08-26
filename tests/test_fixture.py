"""Invariant 6: the committed fixture and a fresh regeneration both hash to the
manifest."""

from __future__ import annotations

from pathlib import Path

from generator import cli, manifest, profiles
from generator.generate import generate
from tests import pins

ROOT = Path(__file__).parent.parent
TINY = ROOT / "fixtures" / "tiny"


def test_committed_tiny_matches_manifest() -> None:
    assert (TINY / manifest.NAME).is_file()
    assert manifest.diff(TINY, TINY / manifest.NAME) == []
    assert manifest.matches(TINY, TINY / manifest.NAME)
    assert (TINY / manifest.NAME).read_text() == manifest.render(manifest.compute(TINY))


def test_regenerated_tiny_matches_manifest(tmp_path: Path) -> None:
    """The generator's keys only: expected/ (Phase 3) is the golden's, not
    the generator's — `seed` checks the same subset (cli.generated_drift)."""
    cli.write_output(tmp_path, generate(profiles.load("tiny")))
    assert cli.generated_drift(tmp_path, TINY / manifest.NAME) == []
    assert manifest.diff(tmp_path, TINY / manifest.NAME) == [
        "expected/attribution.csv: missing",
        "expected/ontime_rate_daily.csv: missing",
    ]


def test_manifest_roundtrip_and_a_changed_byte_is_a_diff(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("b")
    text = manifest.render(manifest.compute(tmp_path))
    (tmp_path / manifest.NAME).write_text(text)
    assert manifest.parse(text) == manifest.compute(tmp_path)
    assert manifest.matches(tmp_path, tmp_path / manifest.NAME)
    (tmp_path / "sub" / "b.txt").write_text("B")
    (tmp_path / "c.txt").write_text("c")
    assert not manifest.matches(tmp_path, tmp_path / manifest.NAME)
    assert manifest.diff(tmp_path, tmp_path / manifest.NAME) == [
        "c.txt: extra",
        "sub/b.txt: changed",
    ]


def test_raw_dims_truth_hashes_are_the_phase_1_hashes() -> None:
    """The Phase 3 re-freeze added expected/attribution.csv and moved nothing:
    the generator's own keys still hash to a fresh regeneration, and the
    manifest is exactly the Phase 1 lines plus one."""
    lines = manifest.parse((TINY / manifest.NAME).read_text())
    generated = cli.generated_keys(lines)
    assert len(generated) == pins.PHASE1_MANIFEST_LINES
    assert set(lines) - set(generated) == {
        "expected/attribution.csv",
        "expected/ontime_rate_daily.csv",
    }
    assert {k.split("/")[0] for k in generated} == {"raw", "dims", "truth"}


def test_phase_3_expected_hash_is_unchanged() -> None:
    """The Phase 4 re-freeze added expected/ontime_rate_daily.csv and moved
    nothing: the attribution golden's hash is the Phase 3 one, and the
    manifest is exactly the Phase 3 lines plus one."""
    lines = manifest.parse((TINY / manifest.NAME).read_text())
    assert len(lines) == pins.PHASE3_MANIFEST_LINES + 1
    assert (
        lines["expected/attribution.csv"]
        == "d90f26c4ef5b88b73b34aa8b7e872ba0fc2577e08461bca6d932896eae275b9a"
    )
