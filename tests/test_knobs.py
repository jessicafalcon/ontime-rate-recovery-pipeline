"""Invariant 5: every knob moves the output its documented way and nothing
else. Injector knobs never change a prompt's cause."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from generator.models import SKEW_MAX_MIN, Cause, EventType
from tests._gen import by_prompt, gen

NO_FAULTS = dict(delivery_fault_rate=0.0, clock_skew_rate=0.0, upload_fault_rate=0.0)


def _causes(out) -> Counter:
    return Counter(pc.cause for pc in out.prompt_causes)


def test_users_knob() -> None:
    out = gen(users=5)
    assert {r.user_id for r in out.dims} == {f"u-{i:06d}" for i in range(1, 6)}
    assert len(out.prompt_causes) == 5 * 7
    assert len(out.latent_users) == 5


def test_days_knob() -> None:
    assert len(gen(days=3).prompt_causes) == 20 * 3
    assert len(gen(days=10).prompt_causes) == 20 * 10


def test_tz_mix_knob() -> None:
    out = gen(tz_mix={"Asia/Tokyo": 1.0}, tz_change_rate=0.0)
    assert {r.tz for r in out.dims} == {"Asia/Tokyo"}
    assert len(out.dims) == 20  # one row each
    out = gen(tz_change_rate=1.0)
    assert Counter(r.user_id for r in out.dims) == {r.user_id: 2 for r in out.dims}


def test_upload_fault_rate_knob() -> None:
    zero = gen(upload_fault_rate=0.0)
    assert Cause.upload_fault not in _causes(zero)
    assert not any(e.event_type is EventType.upload_failed for e in zero.events)
    full = gen(**{**NO_FAULTS, "upload_fault_rate": 1.0})
    assert set(_causes(full)) <= {Cause.upload_fault, Cause.timing_gap}
    assert Cause.upload_fault in _causes(full)


def test_delivery_fault_rate_knob() -> None:
    zero = gen(delivery_fault_rate=0.0)
    groups = by_prompt(zero.events)
    assert all(
        any(e.event_type is EventType.prompt_delivered for e in evs)
        for evs in groups.values()
    )
    full = gen(delivery_fault_rate=1.0)
    assert set(_causes(full)) == {Cause.delivery_fault}


def test_reachable_width_knob() -> None:
    wide = _causes(gen(reachable_width_hours=24.0))[Cause.timing_gap]
    narrow = _causes(gen(reachable_width_hours=0.5))[Cause.timing_gap]
    assert narrow > wide
    assert {
        u.reachable_width_hours for u in gen(reachable_width_hours=3.0).latent_users
    } == {3.0}


def test_duplicate_injector_knob() -> None:
    zero = gen(duplicate_rate=0.0)
    ids = [e.insert_id for e in zero.events]
    assert len(ids) == len(set(ids))
    half = gen(duplicate_rate=0.5)
    dupes = [k for k, n in Counter(e.insert_id for e in half.events).items() if n > 1]
    assert dupes
    by_id = {}
    for e in half.events:
        by_id.setdefault(e.insert_id, []).append(e)
    for k in dupes:
        a, b = by_id[k][:2]
        assert a.model_dump(exclude={"server_upload_time"}) == b.model_dump(
            exclude={"server_upload_time"}
        )
    assert half.prompt_causes == zero.prompt_causes  # never changes a cause


def _lag(e) -> timedelta:
    return e.server_upload_time - e.server_received_time


def test_late_arrival_injector_knob() -> None:
    zero = gen(late_arrival_rate=0.0, duplicate_rate=0.0)
    assert all(_lag(e) < timedelta(hours=1) for e in zero.events)
    full = gen(late_arrival_rate=1.0, duplicate_rate=0.0, late_arrival_max_hours=48)
    prompt_events = [e for e in full.events if "prompt_id" in e.event_properties]
    assert all(
        timedelta(hours=1) <= _lag(e) <= timedelta(hours=49) for e in prompt_events
    )
    assert full.prompt_causes == zero.prompt_causes  # received times untouched
    assert [e.server_received_time for e in full.events] != [
        e.server_upload_time for e in full.events
    ]


def test_clock_skew_injector_knob() -> None:
    zero = gen(clock_skew_rate=0.0)
    assert Cause.unattributed not in _causes(zero)
    assert all(e.server_received_time >= e.client_event_time for e in zero.events)
    full = gen(clock_skew_rate=1.0, delivery_fault_rate=0.0, clock_skew_min=30)
    assert set(_causes(full)) == {Cause.unattributed}
    for evs in by_prompt(full.events).values():
        lags = [(e.server_received_time - e.client_event_time) for e in evs]
        assert min(lags) < -timedelta(minutes=SKEW_MAX_MIN)
