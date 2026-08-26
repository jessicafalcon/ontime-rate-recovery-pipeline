"""Invariant 8 (Phase 4): every metric column of both marts has exactly one
`### ` block in docs/METRICS.md, and the marts' schema.yml links to the doc
instead of restating a formula. Phase 5 extends it to the score columns and
the feature count."""

from __future__ import annotations

import re
from pathlib import Path

from eval import golden

ROOT = Path(__file__).parent.parent
METRICS = ROOT / "docs" / "METRICS.md"
SCHEMA = ROOT / "dbt" / "models" / "marts" / "schema.yml"
SCORES_SCHEMA = ROOT / "dbt" / "models" / "scores" / "schema.yml"
DAILY_METRICS = golden.ONTIME_RATE_DAILY.columns[1:]  # cohort_id is a key, not a metric
RETENTION_METRICS = ("retained",)
# user_id / cohort_id are keys; model_version and computed_as_of are the
# write-back's version pair, not metrics (ARCHITECTURE §2.9).
SCORE_METRICS = golden.SCORES_SEND_TIME.columns[2:7]
FEATURE_METRICS = ("n_opens",)
REQUIRED_FIELDS = ("Grain", "Numerator", "Denominator", "Null policy", "Pinned by")


def headings(text: str) -> list[str]:
    return re.findall(r"^### `([a-z_]+)`", text, re.M)


def test_every_mart_metric_has_exactly_one_definition() -> None:
    text = METRICS.read_text()
    found = headings(text)
    for metric in (
        *DAILY_METRICS,
        *RETENTION_METRICS,
        *SCORE_METRICS,
        *FEATURE_METRICS,
    ):
        assert found.count(metric) == 1, metric
        block = text.split(f"### `{metric}`", 1)[1].split("\n### ", 1)[0]
        for field in REQUIRED_FIELDS:
            assert f"- **{field}:**" in block, (metric, field)
    schema = SCHEMA.read_text()
    assert schema.count("docs/METRICS.md") >= len(DAILY_METRICS)
    assert not re.search(
        r"on_time\s*/\s*prompts_delivered", schema
    )  # link, never restate
    scores = SCORES_SCHEMA.read_text()
    assert scores.count("docs/METRICS.md") >= len(SCORE_METRICS)
    assert "atan2" not in scores and "floor(" not in scores  # link, never restate
