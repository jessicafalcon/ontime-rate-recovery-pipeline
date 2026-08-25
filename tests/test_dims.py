"""Invariant 8: dim_user is a valid SCD2 history and every event's local time
used the tz valid at its client_event_time."""

from __future__ import annotations

from collections import defaultdict

from generator.dims import tz_at
from generator.generate import local_hour_of
from generator.models import EventType
from tests._gen import gen


def _rows_of(out):
    rows = defaultdict(list)
    for r in out.dims:
        rows[r.user_id].append(r)
    return rows


def test_dim_user_is_valid_scd2() -> None:
    for uid, rows in _rows_of(gen(tz_change_rate=0.5)).items():
        rows = sorted(rows, key=lambda r: r.valid_from)
        assert sum(r.valid_to is None for r in rows) == 1, uid
        for a, b in zip(rows, rows[1:], strict=False):
            assert a.valid_to == b.valid_from  # contiguous, non-overlapping
            assert a.tz != b.tz
        assert len({r.cohort_id for r in rows}) == 1


def test_single_tz_mix_never_changes_tz() -> None:
    """A one-entry tz_mix has no other tz to change to, so every user stays one
    row even at tz_change_rate=1 — no spurious same-tz SCD2 'change' (invariant 8)."""
    rows = _rows_of(gen(tz_mix={"UTC": 1.0}, tz_change_rate=1.0))
    assert rows  # the profile still produced users
    for uid, user_rows in rows.items():
        assert len(user_rows) == 1, uid
        assert user_rows[0].valid_to is None


def test_tz_change_users_have_two_rows_and_events_use_the_right_one() -> None:
    out = gen(tz_change_rate=1.0)
    rows = _rows_of(out)
    assert all(len(r) == 2 for r in rows.values())
    hours = {pc.prompt_id: pc.local_send_hour for pc in out.prompt_causes}
    seen: set[str] = set()  # duplicates repeat a prompt_sent; count prompts
    for e in out.events:
        if e.event_type is EventType.prompt_sent:
            tz = tz_at(rows[e.user_id], e.client_event_time)
            assert (
                round(local_hour_of(e.client_event_time, tz), 3)
                == hours[e.event_properties["prompt_id"]]
            )
            seen.add(e.event_properties["prompt_id"])
    assert seen == set(hours)
    # the two rows really are used: some prompt lands on each side of a change
    sides = set()
    for e in out.events:
        if e.event_type is EventType.prompt_sent:
            r = rows[e.user_id]
            sides.add(tz_at(r, e.client_event_time) == r[0].tz)
    assert sides == {True, False}
