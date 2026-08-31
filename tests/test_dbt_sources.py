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
    bq = tmp_path / "bq_schema.json"
    monkeypatch.setattr(gen, "DDL_PATH", ddl)
    monkeypatch.setattr(gen, "SOURCES_PATH", src)
    monkeypatch.setattr(gen, "BQ_SCHEMA_PATH", bq)
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0
    src.write_text(src.read_text().replace("- not_null\n", "", 1))
    assert gen.main(["--check"]) == 1
    ddl.write_text(ddl.read_text().replace(" not null", "", 1))
    assert gen.main(["--check"]) == 1
    gen.main([])
    bq.write_text(bq.read_text().replace('"REQUIRED"', '"NULLABLE"', 1))
    assert gen.main(["--check"]) == 1


def test_bq_schema_is_generated_from_the_contract() -> None:
    """Phase 9b invariant 3: the BigQuery load schema is the contract, typed by
    the same column walk as the DuckDB DDL (varchar→STRING, timestamp→TIMESTAMP,
    date→DATE, json→JSON; REQUIRED unless Optional), never hand-typed."""
    import json

    schema = json.loads(gen.BQ_SCHEMA_PATH.read_text())
    assert gen.HEADER in schema["_comment"]
    for table, model in gen.TABLES:
        cols = gen.columns(model)
        assert [f["name"] for f in schema[table]] == [c[0] for c in cols]
        for f, (name, typ, nullable, _) in zip(schema[table], cols, strict=True):
            assert f["type"] == gen.BQ_TYPES[typ], name
            assert f["mode"] == ("NULLABLE" if nullable else "REQUIRED"), name
    assert {f["type"] for f in schema["events"]} == {"STRING", "TIMESTAMP", "JSON"}
    assert [f["mode"] for f in schema["dim_user"]][-1] == "NULLABLE"  # valid_to


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


# --------------------------------------------------- Phase 10: the Spanner shapes


def test_sources_dim_user_identifier_is_the_swap_var() -> None:
    """Phase 10 (§3.3's source swap): the dim_user SOURCE resolves through the
    `dim_user_identifier` var (default = the landed table, every existing build
    unchanged); events carry no identifier — nothing else swaps."""
    text = gen.SOURCES_PATH.read_text()
    assert "identifier: \"{{ var('dim_user_identifier', 'dim_user') }}\"" in text
    assert text.count("identifier:") == 1


def test_spanner_tf_matches_the_contract_renders() -> None:
    """The module's dim_user DDL and federation view are the contract's renders
    verbatim (generated-never-hand-edited, pinned INSIDE the .tf so tf-freeze's
    manifest covers them); a hand edit on either side fails here."""
    tf = (ROOT / "infra" / "modules" / "spanner" / "main.tf").read_text()
    assert gen.spanner_dim_user_ddl() in tf
    assert gen.federation_view_sql() in tf
    ddl = gen.spanner_dim_user_ddl()
    assert ddl.endswith("primary key (user_id, valid_from)")  # SCD2 key
    assert "valid_to timestamp\n" in ddl + "\n"  # nullable: the open row
    view = gen.federation_view_sql()
    assert view.count("external_query") == 1
    names = [c[0] for c in gen.columns(gen.DimUserRow)]
    assert f"'select {', '.join(names)} from dim_user'" in view


def test_spanner_send_schedule_ddl_matches_serving_columns() -> None:
    """The module's send_schedule DDL carries the §2.9 nine columns in order
    with primary key user_id — the same list serving/ddl.sql and
    serving/spanner.py::COLUMNS serve."""
    from serving.spanner import COLUMNS

    tf = (ROOT / "infra" / "modules" / "spanner" / "main.tf").read_text()
    ddl = tf.split("create table send_schedule (")[1].split("EOT")[0]
    body, key = ddl.split(") primary key ")
    declared = [ln.strip().split(" ")[0] for ln in body.strip().splitlines()]
    assert declared == list(COLUMNS)
    assert key.strip() == "(user_id)"
    assert body.count("not null") == len(COLUMNS)  # every serving column required
