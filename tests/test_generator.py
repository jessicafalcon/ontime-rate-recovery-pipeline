"""Invariant 1 (byte-identical, arrival order), 4 (events consistent with the
assigned cause), 7 (seed never writes under fixtures/)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from generator import cli, manifest, writer
from generator.generate import arrival_order
from generator.models import SKEW_MAX_MIN, Cause, Event, EventType
from tests._gen import by_prompt, gen, types

ROOT = Path(__file__).parent.parent


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    cli.write_output(a, gen())
    cli.write_output(b, gen())
    assert manifest.compute(a) == manifest.compute(b)
    for rel in manifest.compute(a):
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_two_processes_under_different_tz_are_byte_identical(tmp_path: Path) -> None:
    code = (
        "import sys; from pathlib import Path; from generator import cli, profiles; "
        "from generator.generate import generate; "
        "cli.write_output(Path(sys.argv[1]), generate(profiles.load('tiny')))"
    )
    for tz in ("UTC", "America/New_York"):
        subprocess.run(
            [sys.executable, "-c", code, str(tmp_path / tz)],
            cwd=ROOT,
            env={**os.environ, "TZ": tz, "PYTHONHASHSEED": "0" if tz == "UTC" else "7"},
            check=True,
        )
    assert manifest.compute(tmp_path / "UTC") == manifest.compute(
        tmp_path / "America/New_York"
    )


def _ev(insert_id: str, upload: datetime) -> Event:
    t = datetime(2026, 1, 5, tzinfo=UTC)
    return Event(
        insert_id=insert_id,
        event_type=EventType.app_opened,
        user_id="u-000001",
        device_id="d-000001",
        client_event_time=t,
        server_received_time=t,
        server_upload_time=upload,
        event_properties={},
    )


def test_emit_order_is_arrival_order_with_insert_id_tiebreak() -> None:
    t = datetime(2026, 1, 5, 1, tzinfo=UTC)
    evs = [_ev("e-3", t), _ev("e-1", t + timedelta(hours=1)), _ev("e-2", t)]
    assert [e.insert_id for e in arrival_order(evs)] == ["e-2", "e-3", "e-1"]
    assert [e.insert_id for e in arrival_order(list(reversed(evs)))] == [
        "e-2",
        "e-3",
        "e-1",
    ]


def _delay_min(e: Event) -> float:
    return (e.server_received_time - e.client_event_time).total_seconds() / 60


def test_events_are_consistent_with_assigned_cause() -> None:
    out = gen()
    groups = by_prompt(out.events)
    seen: set[Cause] = set()
    for pc in out.prompt_causes:
        evs = groups[pc.prompt_id]
        seen.add(pc.cause)
        sent = next(e for e in evs if e.event_type is EventType.prompt_sent)
        window_end = sent.client_event_time + timedelta(
            minutes=sent.event_properties["window_minutes"]
        )
        t = types(evs)
        if pc.cause is Cause.delivery_fault:
            assert t == {"prompt_sent"}
            continue
        assert "prompt_delivered" in t
        if pc.cause is Cause.timing_gap:
            assert t == {"prompt_sent", "prompt_delivered"}
        elif pc.cause is Cause.on_time:
            resp = next(e for e in evs if e.event_type is EventType.response_recorded)
            assert resp.client_event_time <= window_end
            assert resp.server_received_time <= window_end
            assert all(-SKEW_MAX_MIN <= _delay_min(e) <= SKEW_MAX_MIN for e in evs)
        elif pc.cause is Cause.upload_fault:
            cap = next(e for e in evs if e.event_type is EventType.capture_started)
            assert cap.client_event_time <= window_end
            if "response_recorded" in t:
                resp = next(
                    e for e in evs if e.event_type is EventType.response_recorded
                )
                assert resp.server_received_time > window_end
            else:
                assert "upload_failed" in t and "upload_completed" not in t
        else:
            assert pc.cause is Cause.unattributed
            assert any(_delay_min(e) < -SKEW_MAX_MIN for e in evs)
    assert seen == set(Cause)  # tiny exercises every cause


def test_seed_never_writes_under_fixtures(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(writer.FixtureWriteRefused):
        writer.write_jsonl(ROOT / "fixtures" / "tiny" / "raw" / "x.jsonl", [])
    with pytest.raises(writer.FixtureWriteRefused):
        writer.write_csv(ROOT / "fixtures" / "x" / "dims" / "d.csv", [])
    frozen = ROOT / "fixtures" / "tiny"
    before = manifest.compute(frozen)
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    assert cli.seed("tiny") == 0
    assert manifest.compute(frozen) == before
    assert (tmp_path / "out" / "tiny" / "raw").is_dir()
