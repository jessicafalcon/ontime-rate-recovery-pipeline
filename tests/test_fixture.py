"""Invariant 6: the committed fixture and a fresh regeneration both hash to the
manifest."""

from __future__ import annotations

from pathlib import Path

from generator import cli, manifest, profiles
from generator.generate import generate

ROOT = Path(__file__).parent.parent
TINY = ROOT / "fixtures" / "tiny"


def test_committed_tiny_matches_manifest() -> None:
    assert (TINY / manifest.NAME).is_file()
    assert manifest.diff(TINY, TINY / manifest.NAME) == []
    assert manifest.matches(TINY, TINY / manifest.NAME)
    assert (TINY / manifest.NAME).read_text() == manifest.render(manifest.compute(TINY))


def test_regenerated_tiny_matches_manifest(tmp_path: Path) -> None:
    cli.write_output(tmp_path, generate(profiles.load("tiny")))
    assert manifest.diff(tmp_path, TINY / manifest.NAME) == []


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
    assert manifest.diff(tmp_path, tmp_path / manifest.NAME) == [
        "c.txt: extra",
        "sub/b.txt: changed",
    ]
