"""Invariant 8 (Phase 4): every metric column of both marts has exactly one
`### ` block in docs/METRICS.md, and the marts' schema.yml links to the doc
instead of restating a formula."""

from __future__ import annotations

import re
from pathlib import Path

from eval import golden

ROOT = Path(__file__).parent.parent
METRICS = ROOT / "docs" / "METRICS.md"
SCHEMA = ROOT / "dbt" / "models" / "marts" / "schema.yml"
DAILY_METRICS = golden.ONTIME_RATE_DAILY.columns[1:]  # cohort_id is a key, not a metric
RETENTION_METRICS = ("retained",)
REQUIRED_FIELDS = ("Grain", "Numerator", "Denominator", "Null policy", "Pinned by")


def headings(text: str) -> list[str]:
    return re.findall(r"^### `([a-z_]+)`", text, re.M)


def test_every_mart_metric_has_exactly_one_definition() -> None:
    text = METRICS.read_text()
    found = headings(text)
    for metric in (*DAILY_METRICS, *RETENTION_METRICS):
        assert found.count(metric) == 1, metric
        block = text.split(f"### `{metric}`", 1)[1].split("\n### ", 1)[0]
        for field in REQUIRED_FIELDS:
            assert f"- **{field}:**" in block, (metric, field)
    schema = SCHEMA.read_text()
    assert schema.count("docs/METRICS.md") >= len(DAILY_METRICS)
    assert not re.search(
        r"on_time\s*/\s*prompts_delivered", schema
    )  # link, never restate
