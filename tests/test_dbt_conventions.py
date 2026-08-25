"""dbt conventions the code-reviewer would otherwise check by eye (spec
Phase 2 invariants 3, 4, 6): no clock on a data path, exactly five dispatch
macros each with a duckdb body and a bigquery stub that raises, no default
body, no freshness config, no dbt package."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DBT = ROOT / "dbt"
MACROS = (
    "json_extract",
    "timestamp_diff",
    "safe_divide",
    "to_local_time",
    "partition_overwrite",
)
CLOCK = re.compile(
    r"\b(current_timestamp|now|current_date|get_current_timestamp|run_started_at|"
    r"localtime|localtimestamp|today)\b",
    re.IGNORECASE,
)


def sql_files(root: Path) -> list[Path]:
    return sorted(
        p
        for d in ("models", "macros", "tests")
        for p in (root / d).rglob("*")
        if p.suffix in {".sql", ".yml", ".yaml"}
    )


def clock_hits(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in sql_files(root) if CLOCK.search(p.read_text())
    )


def test_no_clock_call_in_any_model_or_macro(tmp_path: Path) -> None:
    assert clock_hits(DBT) == []
    (tmp_path / "models").mkdir()
    (tmp_path / "macros").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "models" / "bad.sql").write_text("select now() as x")
    (tmp_path / "models" / "ok.sql").write_text("select 1 as nowhere")  # not a token
    assert clock_hits(tmp_path) == ["models/bad.sql"]


def test_no_freshness_block() -> None:
    for p in sql_files(DBT):
        assert "freshness:" not in p.read_text(), p


def test_exactly_five_dispatch_macros() -> None:
    files = sorted(p.stem for p in (DBT / "macros").glob("*.sql"))
    assert files == sorted(MACROS)
    for name in MACROS:
        text = (DBT / "macros" / f"{name}.sql").read_text()
        assert f"adapter.dispatch('{name}', 'ontime')" in text
    # no dispatch anywhere else
    for p in sql_files(DBT):
        if p.parent.name != "macros":
            assert "adapter.dispatch" not in p.read_text(), p


def test_each_macro_has_duckdb_body_and_bigquery_stub_that_raises() -> None:
    for name in MACROS:
        text = (DBT / "macros" / f"{name}.sql").read_text()
        assert re.search(rf"{{% macro duckdb__{name}\(", text), name
        stub = re.search(
            rf"{{% macro bigquery__{name}\((.*?)%}}(.*?){{% endmacro %}}", text, re.S
        )
        assert stub, name
        assert "exceptions.raise_compiler_error" in stub.group(2), name


def test_no_default_dispatch_body() -> None:
    for name in MACROS:
        assert f"default__{name}" not in (DBT / "macros" / f"{name}.sql").read_text()


def test_no_dbt_packages() -> None:
    assert not (DBT / "packages.yml").exists()
    assert not (DBT / "package-lock.yml").exists()
    assert not (DBT / "dbt_packages").exists()


def test_every_model_has_description_and_a_test() -> None:
    schema = (DBT / "models" / "staging" / "schema.yml").read_text()
    for model in (p.stem for p in (DBT / "models" / "staging").glob("*.sql")):
        block = schema.split(f"- name: {model}\n", 1)[1]
        assert "description:" in block.split("\n  - name:", 1)[0], model
        assert "data_tests:" in block.split("\n  - name:", 1)[0], model
