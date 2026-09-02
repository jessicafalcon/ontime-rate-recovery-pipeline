"""Profiles: JSON under generator/profiles/, validated by `Profile`. Every knob
is a required field — a missing knob is an error, never a silent default."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from generator.models import SKEW_MAX_MIN

PROFILES_DIR = Path(__file__).parent / "profiles"
NAME_RE = re.compile(r"[a-z0-9_]+")


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int
    users: int = Field(gt=0)
    days: int = Field(gt=0)
    shards: int = Field(
        gt=0
    )  # independent (seed+s·P) streams; 1 == the single-Random path
    tz_mix: dict[str, float]  # IANA tz -> weight
    tz_change_rate: float = Field(ge=0, le=1)  # users with a second SCD2 row
    cohorts: dict[str, int]  # cohort_id -> local send hour
    window_minutes: int = Field(gt=0)
    upload_fault_rate: float = Field(ge=0, le=1)
    delivery_fault_rate: float = Field(ge=0, le=1)
    reachable_width_hours: float = Field(gt=0, le=24)
    duplicate_rate: float = Field(ge=0, le=1)
    late_arrival_rate: float = Field(ge=0, le=1)
    late_arrival_max_hours: float = Field(gt=0)
    clock_skew_rate: float = Field(ge=0, le=1)
    clock_skew_min: float
    organic_opens_per_day: float = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Profile:
        if not self.tz_mix or not self.cohorts:
            raise ValueError("tz_mix and cohorts must be non-empty")
        if self.clock_skew_min <= SKEW_MAX_MIN:
            raise ValueError(f"clock_skew_min must exceed SKEW_MAX_MIN={SKEW_MAX_MIN}")
        return self


class BadProfileName(ValueError):
    pass


def load(name: str, root: Path = PROFILES_DIR) -> Profile:
    """Validate the name first; every path is derived from the validated name."""
    if not NAME_RE.fullmatch(name):
        raise BadProfileName(f"profile name must match [a-z0-9_]+, got {name!r}")
    path = root / f"{name}.json"
    if not path.is_file():
        raise BadProfileName(f"no profile {name!r} under {root}")
    return Profile.model_validate(json.loads(path.read_text()))


def available(root: Path = PROFILES_DIR) -> list[str]:
    return sorted(p.stem for p in root.glob("*.json"))
