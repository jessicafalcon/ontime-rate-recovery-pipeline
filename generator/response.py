"""The one response function. Pure, caller-seeded; `eval/simulate.py` (Phase 6)
imports it unchanged."""

from __future__ import annotations

import math
from random import Random

from generator.models import LatentUser


def circular_distance_hours(a: float, b: float) -> float:
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def open_probability(local_send_hour: float, user: LatentUser, window_minutes: int):
    """P(open within the window): 0.9 inside the reachable window, decaying
    with the distance outside it; a shorter window lowers it proportionally."""
    d = circular_distance_hours(local_send_hour, user.reachable_center_local_hour)
    outside = max(0.0, d - user.reachable_width_hours / 2.0)
    return 0.9 * math.exp(-outside) * min(1.0, window_minutes / 60.0)


def responds(
    local_send_hour: float, user: LatentUser, window_minutes: int, rng: Random
) -> bool:
    return rng.random() < open_probability(local_send_hour, user, window_minutes)
