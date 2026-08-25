"""Invariant 3: every record is a validated model whose columns are exactly the
contract's; invariant 4: the cause set is the five labels."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from generator import writer
from generator.models import (
    ENVELOPE_COLUMNS,
    PROPERTY_KEYS,
    Cause,
    Event,
    EventType,
    PromptCause,
)

T = datetime(2026, 1, 5, tzinfo=UTC)


def _event(**kw):
    base = dict(
        insert_id="e-1",
        event_type=EventType.prompt_delivered,
        user_id="u-1",
        device_id="d-1",
        client_event_time=T,
        server_received_time=T,
        server_upload_time=T,
        event_properties={"prompt_id": "p-1"},
    )
    return Event(**{**base, **kw})


def test_envelope_columns_are_exactly_the_contract() -> None:
    assert tuple(Event.model_fields) == ENVELOPE_COLUMNS
    assert set(json.loads(writer.line(_event()))) == set(ENVELOPE_COLUMNS)
    assert set(PROPERTY_KEYS) == set(EventType)
    with pytest.raises(ValidationError):
        _event(extra_column=1)


def test_invalid_event_is_never_written(tmp_path: Path) -> None:
    target = tmp_path / "raw" / "x.jsonl"
    with pytest.raises(ValidationError):
        _event(client_event_time=datetime(2026, 1, 5))  # naive: not UTC
    with pytest.raises(ValidationError):
        writer.write_jsonl(target, [_event(), _event(event_properties={})])
    assert not target.exists()


@pytest.mark.parametrize(
    "et, props",
    [
        (EventType.prompt_sent, {"prompt_id": "p"}),  # missing cohort_id, window
        (EventType.prompt_delivered, {"prompt_id": "p", "attempt": 1}),  # extra
        (EventType.app_opened, {"prompt_id": "p"}),  # organic carries nothing
    ],
)
def test_event_type_and_properties_agree(et, props) -> None:
    with pytest.raises(ValidationError):
        _event(event_type=et, event_properties=props)
    _event(event_type=et, event_properties=dict.fromkeys(PROPERTY_KEYS[et], "x"))


def test_truth_cause_is_one_of_five() -> None:
    assert {c.value for c in Cause} == {
        "on_time",
        "upload_fault",
        "delivery_fault",
        "timing_gap",
        "unattributed",
    }
    with pytest.raises(ValidationError):
        PromptCause(prompt_id="p", user_id="u", cause="late", local_send_hour=8.0)
