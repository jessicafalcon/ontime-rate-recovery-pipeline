"""The committed raw DDL and sources.yml equal a fresh render from the
contract (spec Phase 2 invariants 6, 7)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gen_dbt_sources as gen  # noqa: E402


def test_committed_sources_equal_regeneration() -> None:
    for path, rendered in gen.render().items():
        assert path.read_text() == rendered, (
            f"{path.relative_to(ROOT)} is stale: run `make gen-sources`"
        )
    assert gen.main(["--check"]) == 0


def test_hand_edit_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ddl = tmp_path / "ddl.sql"
    src = tmp_path / "sources.yml"
    monkeypatch.setattr(gen, "DDL_PATH", ddl)
    monkeypatch.setattr(gen, "SOURCES_PATH", src)
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0
    src.write_text(src.read_text().replace("- not_null\n", "", 1))
    assert gen.main(["--check"]) == 1
    ddl.write_text(ddl.read_text().replace(" not null", "", 1))
    assert gen.main(["--check"]) == 1


def test_no_unique_test_on_raw_insert_id() -> None:
    text = gen.SOURCES_PATH.read_text()
    assert "unique" not in text  # duplicates are the export's contract
    assert "freshness:" not in text  # reads the clock


def test_source_tests_cover_every_required_column() -> None:
    for _, model in gen.TABLES:
        for name, _typ, nullable, values in gen.columns(model):
            tests = gen.column_tests(nullable, values)
            assert ("not_null" in tests) == (not nullable), name
            assert (any(t.startswith("accepted_values") for t in tests)) == (
                values is not None
            ), name
    assert gen.duckdb_type(dict[str, object]) == "json"
    assert gen.duckdb_type(gen.Event.model_fields["event_type"].annotation) == "varchar"
    assert gen.render_ddl().count("create or replace table") == 2
