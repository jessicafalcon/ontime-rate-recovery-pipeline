"""Shared helpers for the generator tests (not a test file)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from generator import profiles
from generator.generate import Output, generate
from generator.models import Event


def tiny(**overrides: Any) -> profiles.Profile:
    return profiles.load("tiny").model_copy(update=overrides)


def gen(**overrides: Any) -> Output:
    return generate(tiny(**overrides))


def by_prompt(events: list[Event]) -> dict[str, list[Event]]:
    out: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        pid = ev.event_properties.get("prompt_id")
        if pid:
            out[pid].append(ev)
    return out


def types(evs: list[Event]) -> set[str]:
    return {e.event_type.value for e in evs}
