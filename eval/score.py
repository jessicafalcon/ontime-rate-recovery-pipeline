"""Label accuracy vs the generator's assigned causes (truth/prompts.jsonl)."""

from __future__ import annotations

import json
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
