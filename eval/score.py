"""Label accuracy vs the generator's assigned causes (truth/prompts.jsonl), and
reachable-centre MAE / coverage vs the latent windows (truth/users.jsonl).
Measurement only: every centre and send time is read off the model's own
columns — Python never derives a score (CLAUDE.md model-is-a-model)."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import duckdb

LABELS = ("on_time", "upload_fault", "delivery_fault", "timing_gap", "unattributed")


def truth_labels(prompts_jsonl: Path) -> dict[str, str]:
    """prompt_id → cause, from the side-file only eval may read."""
    out: dict[str, str] = {}
    for line in prompts_jsonl.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["prompt_id"]] = rec["cause"]
    return out


def built_labels(db: Path) -> dict[str, str]:
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            "select prompt_id, label from main_attribution.attribution"
        ).fetchall()
    finally:
        con.close()
    return {pid: label for pid, label in rows}


def label_accuracy(built: dict[str, str], truth: dict[str, str]) -> float:
    """Share of truth prompts whose built label equals the cause; a prompt
    missing from `built` counts as wrong, never as absent."""
    if not truth:
        raise ValueError("no truth prompts")
    hits = sum(built.get(pid) == cause for pid, cause in truth.items())
    return hits / len(truth)


def label_counts(labels: dict[str, str]) -> dict[str, int]:
    c = Counter(labels.values())
    return {label: c.get(label, 0) for label in LABELS}


# ------------------------------------------------- Phase 5: the send-time model


def truth_windows(users_jsonl: Path) -> dict[str, tuple[float, float]]:
    """user_id → (reachable_center_local_hour, reachable_width_hours)."""
    out: dict[str, tuple[float, float]] = {}
    for line in users_jsonl.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["user_id"]] = (
                float(rec["reachable_center_local_hour"]),
                float(rec["reachable_width_hours"]),
            )
    return out


def built_scores(db: Path) -> dict[str, tuple[float, float]]:
    """user_id → (center_hour_local, served hour as a fraction), off the model."""
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            "select user_id, center_hour_local, "
            "send_hour_local + send_minute_local / 60.0 "
            "from main_scores.scores_send_time"
        ).fetchall()
    finally:
        con.close()
    return {uid: (float(c), float(s)) for uid, c, s in rows}


def circular_abs_diff_hours(a: float, b: float) -> float:
    """Short-arc distance on the 24-hour circle, in [0, 12]."""
    d = a - b
    return abs(d - 24 * math.floor((d + 12) / 24))


def reachable_center_mae(
    built: dict[str, tuple[float, float]], truth: dict[str, tuple[float, float]]
) -> float:
    """Mean circular |center_hour_local − latent centre| over truth users; a
    user missing from `built` counts the worst case (12 h), never as absent."""
    if not truth:
        raise ValueError("no truth users")
    total = 0.0
    for uid, (center, _width) in truth.items():
        total += (
            circular_abs_diff_hours(built[uid][0], center) if uid in built else 12.0
        )
    return total / len(truth)


def coverage(
    built: dict[str, tuple[float, float]], truth: dict[str, tuple[float, float]]
) -> float:
    """Share of truth users whose SERVED time lies inside centre ± width / 2
    (circular); a missing user is outside."""
    if not truth:
        raise ValueError("no truth users")
    inside = 0
    for uid, (center, width) in truth.items():
        if uid in built and circular_abs_diff_hours(built[uid][1], center) <= width / 2:
            inside += 1
    return inside / len(truth)
