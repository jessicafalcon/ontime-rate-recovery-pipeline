"""Profiles: every JSON validates; a missing knob is an error; the name is
validated before any path is derived; `medium` generates and is not committed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from generator import profiles
from generator.generate import generate
from generator.models import Cause

ROOT = Path(__file__).parent.parent


def test_every_profile_validates() -> None:
    names = profiles.available()
    assert {"tiny", "medium"} <= set(names)
    for n in names:
        p = profiles.load(n)
        assert p.clock_skew_min > 5
    tiny = profiles.load("tiny")
    assert (tiny.users, tiny.days) == (20, 7)


def test_a_missing_or_unknown_knob_is_an_error(tmp_path: Path) -> None:
    raw = json.loads((profiles.PROFILES_DIR / "tiny.json").read_text())
    del raw["duplicate_rate"]
    (tmp_path / "x.json").write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        profiles.load("x", tmp_path)
    raw["duplicate_rate"] = 0.1
    raw["bogus"] = 1
    (tmp_path / "x.json").write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        profiles.load("x", tmp_path)


@pytest.mark.parametrize("bad", ["", "../x", "a b", "Tiny", "tiny;", "tiny/"])
def test_profile_name_is_validated(bad: str) -> None:
    with pytest.raises(profiles.BadProfileName):
        profiles.load(bad)


def test_medium_generates_and_is_not_committed() -> None:
    out = generate(profiles.load("medium"))
    assert len(out.prompt_causes) == 2000 * 30
    assert set(c.cause for c in out.prompt_causes) == set(Cause)
    assert not (ROOT / "fixtures" / "medium").exists()
