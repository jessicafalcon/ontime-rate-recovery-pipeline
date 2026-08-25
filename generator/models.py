"""Schema source of truth (ARCHITECTURE §2.1–2.4). Every record the generator
writes is one of these, validated on construction; the raw DDL and dbt sources
are generated from them (Phase 2), never hand-edited."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

# Client clock skew beyond this many minutes is `unattributed` (§2.1). Phase 3
# pins the same value as a dbt var; the generator's skew injector exceeds it.
SKEW_MAX_MIN = 5

AMPLITUDE_TS = "%Y-%m-%d %H:%M:%S.%f"


class EventType(StrEnum):
    prompt_sent = "prompt_sent"
    prompt_delivered = "prompt_delivered"
    prompt_opened = "prompt_opened"
    capture_started = "capture_started"
    upload_started = "upload_started"
    upload_failed = "upload_failed"
    upload_completed = "upload_completed"
    response_recorded = "response_recorded"
    app_opened = "app_opened"


# Exactly the property keys §2.2 names per event type.
PROPERTY_KEYS: dict[EventType, frozenset[str]] = {
    EventType.prompt_sent: frozenset({"prompt_id", "cohort_id", "window_minutes"}),
    EventType.prompt_delivered: frozenset({"prompt_id"}),
    EventType.prompt_opened: frozenset({"prompt_id"}),
    EventType.capture_started: frozenset({"prompt_id"}),
    EventType.upload_started: frozenset({"prompt_id", "attempt", "error_code"}),
    EventType.upload_failed: frozenset({"prompt_id", "attempt", "error_code"}),
    EventType.upload_completed: frozenset({"prompt_id", "attempt", "error_code"}),
    EventType.response_recorded: frozenset({"prompt_id", "response_id"}),
    EventType.app_opened: frozenset(),
}

ENVELOPE_COLUMNS = (
    "insert_id",
    "event_type",
    "user_id",
    "device_id",
    "client_event_time",
    "server_received_time",
    "server_upload_time",
    "event_properties",
)


class Cause(StrEnum):
    on_time = "on_time"
    upload_fault = "upload_fault"
    delivery_fault = "delivery_fault"
    timing_gap = "timing_gap"
    unattributed = "unattributed"


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
        raise ValueError("timestamps must be tz-aware UTC")
    return v


class Event(BaseModel):
    """One row of the Amplitude raw export."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    insert_id: str
    event_type: EventType
    user_id: str
    device_id: str
    client_event_time: datetime
    server_received_time: datetime
    server_upload_time: datetime
    event_properties: dict[str, Any]

    _check_utc = field_validator(
        "client_event_time", "server_received_time", "server_upload_time"
    )(_utc)

    @field_validator("event_properties")
    @classmethod
    def _keys_match_type(cls, v: dict[str, Any], info: Any) -> dict[str, Any]:
        et = info.data.get("event_type")
        if et is not None and set(v) != PROPERTY_KEYS[et]:
            raise ValueError(
                f"{et}: properties {sorted(v)} != {sorted(PROPERTY_KEYS[et])}"
            )
        return v

    @field_serializer("client_event_time", "server_received_time", "server_upload_time")
    def _fmt(self, v: datetime) -> str:
        return v.strftime(AMPLITUDE_TS)


class DimUserRow(BaseModel):
    """One SCD2 row of `dim_user` (§2.3). `valid_to` None = current row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    tz: str
    cohort_id: str
    signup_date: date
    valid_from: datetime
    valid_to: datetime | None

    _check_utc = field_validator("valid_from", "valid_to")(
        lambda v: v if v is None else _utc(v)
    )

    @field_serializer("valid_from", "valid_to")
    def _fmt(self, v: datetime | None) -> str:
        return "" if v is None else v.strftime(AMPLITUDE_TS)


class LatentUser(BaseModel):
    """Per-user latent reachable window (§2.4 side-file record; only `eval/`
    reads it). Named for what it is so generation code never names the file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    cohort_id: str
    reachable_center_local_hour: float
    reachable_width_hours: float


class PromptCause(BaseModel):
    """Per-prompt assigned cause (§2.4 side-file record)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    user_id: str
    cause: Cause
    local_send_hour: float
