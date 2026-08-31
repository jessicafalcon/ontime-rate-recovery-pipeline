"""dbt conventions the code-reviewer would otherwise check by eye (spec
Phase 2 invariants 3, 4, 6; Phase 9b invariants 1, 5, 8): no clock on a data
path, exactly five dispatch macros each with a duckdb body and a bigquery body
(Phase 9b — they raised until then), no default body, `generate_schema_name`
collapsing on the bigquery target only, a dialect-safe partition config, no
freshness config, no dbt package."""

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


# Dialect functions that must not appear inline in a MODEL (they belong to a
# dispatch macro body or to an ANSI rewrite). Unit-test fixtures in schema.yml
# may type their rows with them; macros are the seam by definition. The `%`
# alternative is SQL modulo (Phase 5 denylist); it excludes a `%` adjacent to a
# Jinja brace (`{% … %}`), which Phase 7's incremental blocks use — that is a
# statement delimiter, not modulo.
DIALECT = re.compile(
    r"(\bbool_or\b|\blogical_or\b|\bdate_diff\b|\btimezone\(|->>|::|(?<!\{)%(?!\}))",
    re.I,
)


def dialect_hits(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root))
        for p in (root / "models").rglob("*.sql")
        if DIALECT.search(p.read_text())
    )


def test_no_dialect_function_in_any_model(tmp_path: Path) -> None:
    """Review round 1, Phase 3: bool_or sat inline in attribution.sql — the
    seam the five macros exist for was bypassed with no test noticing."""
    assert dialect_hits(DBT) == []
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "bad.sql").write_text("select bool_or(x) as y from t")
    (tmp_path / "models" / "mod.sql").write_text("select x % 24 as y from t")  # Phase 5
    (tmp_path / "models" / "ok.sql").write_text(
        "select max(case when x then 1 else 0 end) = 1 as y from t"
    )
    assert dialect_hits(tmp_path) == ["models/bad.sql", "models/mod.sql"]


def test_no_freshness_block() -> None:
    for p in sql_files(DBT):
        assert "freshness:" not in p.read_text(), p


# Macro files that are dbt HOOK overrides, not dispatch macros (Phase 9b).
HOOKS = ("generate_schema_name",)


def test_exactly_five_dispatch_macros() -> None:
    """Exactly five macro files dispatch; the macro dir may also hold dbt HOOK
    overrides (`generate_schema_name`, Phase 9b) that dispatch nothing — the
    Evidence id is kept across three specs, so the name stays."""
    files = sorted(p.stem for p in (DBT / "macros").glob("*.sql"))
    assert files == sorted(MACROS + HOOKS)
    for hook in HOOKS:
        assert "adapter.dispatch" not in (DBT / "macros" / f"{hook}.sql").read_text()
    for name in MACROS:
        text = (DBT / "macros" / f"{name}.sql").read_text()
        assert f"adapter.dispatch('{name}', 'ontime')" in text
    # no dispatch anywhere else
    for p in sql_files(DBT):
        if p.parent.name != "macros":
            assert "adapter.dispatch" not in p.read_text(), p


def _body(name: str, dialect: str) -> str:
    text = (DBT / "macros" / f"{name}.sql").read_text()
    m = re.search(
        rf"{{% macro {dialect}__{name}\((.*?)%}}(.*?){{% endmacro %}}", text, re.S
    )
    assert m, (name, dialect)
    return m.group(2)


# The one BigQuery body that raises BY DESIGN (Phase 9b, Amendment U): dbt-bigquery
# rejects a custom incremental strategy, so the models select its native
# insert_overwrite on that dialect and this seam must not be reached there.
UNREACHABLE_ON_BIGQUERY = ("partition_overwrite",)


def test_each_macro_has_duckdb_and_bigquery_bodies() -> None:
    """Phase 9b (invariant 5): both bodies exist; four BigQuery bodies are real
    SQL (the Phase 2–8 stubs are gone) and partition_overwrite's raises with the
    Amendment U message (an unreachable path fails loudly)."""
    for name in MACROS:
        for dialect in ("duckdb", "bigquery"):
            body = _body(name, dialect)
            assert body.strip(), (name, dialect)
            if dialect == "bigquery" and name in UNREACHABLE_ON_BIGQUERY:
                assert "raise_compiler_error" in body and "Amendment U" in body
            else:
                assert "raise_compiler_error" not in body, (name, dialect)


def test_bigquery_bodies_are_the_named_forms() -> None:
    """Phase 9b pinned decision: the five BigQuery forms and their casts —
    json_value (NULL on a JSON null / missing key); timestamp_diff END first,
    both sides cast to timestamp (DATE/DATETIME callers); safe_divide with a
    float64 numerator (integer/integer would truncate); datetime(ts, tz) (the
    naive wall time); the overwrite's BigQuery half is the adapter's native
    insert_overwrite (Amendment U), so its dispatch body names that and raises."""
    assert re.search(
        r"json_value\(\{\{ col \}\}, '\$\.\{\{ key \}\}'\)",
        _body("json_extract", "bigquery"),
    )
    td = _body("timestamp_diff", "bigquery")
    assert re.search(
        r"timestamp_diff\(cast\(\{\{ end_ts \}\} as timestamp\), "
        r"cast\(\{\{ start_ts \}\} as timestamp\), \{\{ unit \}\}\)",
        td,
    )
    assert re.search(
        r"safe_divide\(cast\(\{\{ numerator \}\} as float64\), \{\{ denominator \}\}\)",
        _body("safe_divide", "bigquery"),
    )
    assert re.search(
        r"datetime\(\{\{ ts_utc \}\}, \{\{ tz \}\}\)",
        _body("to_local_time", "bigquery"),
    )
    assert "native insert_overwrite" in _body("partition_overwrite", "bigquery")


def test_no_default_dispatch_body() -> None:
    for name in MACROS:
        assert f"default__{name}" not in (DBT / "macros" / f"{name}.sql").read_text()


def test_partition_overwrite_renders_delete_and_insert_on_duckdb() -> None:
    """Phase 7 (closes the BACKLOG row): the DuckDB body is delete-AND-insert (the
    name's promise), and the custom incremental strategy routes through the one
    dispatched seam — no second dispatch macro, so the count stays five."""
    text = (DBT / "macros" / "partition_overwrite.sql").read_text()
    body = re.search(
        r"{% macro duckdb__partition_overwrite\(.*?%}(.*?){% endmacro %}", text, re.S
    ).group(1)
    assert "delete from" in body
    assert "insert into" in body and "select" in body
    # the strategy macro is plumbing that CALLS the dispatched macro, not a sixth
    assert "get_incremental_partition_overwrite_sql" in text
    assert "partition_overwrite(" in text
    assert text.count("adapter.dispatch('partition_overwrite', 'ontime')") == 1


def test_generate_schema_name_collapses_only_on_bigquery() -> None:
    """Phase 9b invariant 1: on the bigquery target every model resolves to
    target.schema (the `ontime` dataset — two datasets is 9a's pin); any other
    target keeps dbt's default `<target.schema>_<custom>` (the `main_<folder>`
    names every DuckDB reader hard-codes). Keyed on target.type — a second
    duckdb-typed target would collapse under a target.name key. Not
    generate_schema_name_for_env (collapses every non-prod target)."""
    text = (DBT / "macros" / "generate_schema_name.sql").read_text()
    assert "{% macro generate_schema_name(custom_schema_name, node)" in text
    assert re.search(
        r"if target\.type == 'bigquery'.*?\{\{ target\.schema \}\}", text, re.S
    )
    body = text.split("-#}", 1)[1]  # after the leading comment
    assert "target.name" not in body
    assert "generate_schema_name_for_env" not in body
    assert "{{ target.schema }}_{{ custom_schema_name | trim }}" in text
    assert "adapter.dispatch" not in text


def test_incremental_models_partition_config_is_dialect_safe() -> None:
    """Phase 9b invariant 8 (ARCHITECTURE §8): dbt-bigquery parses `partition_by`
    as its native dict and dbt-duckdb rejects a dict there, so the overwrite
    column lives under `meta.overwrite_partition_col` (a key neither adapter reads)
    and the native dict is set on the bigquery target only; the strategy reads
    the neutral key."""
    for stem in ("stg_events", "stg_prompts", "attribution"):
        path = next((DBT / "models").rglob(f"{stem}.sql"))
        text = path.read_text()
        col = re.search(r"meta=\{'overwrite_partition_col': '(\w+)'\}", text)
        assert col, stem
        assert re.search(
            r"partition_by=\(\{'field': '"
            + col.group(1)
            + r"', 'data_type': 'date'\} if target\.type == 'bigquery' else none\)",
            text,
        ), stem
        assert not re.search(r"partition_by='", text), stem
    strategy = (DBT / "macros" / "partition_overwrite.sql").read_text()
    assert 'config.get("meta", {}).get("overwrite_partition_col")' in strategy
    assert 'config.require("partition_by")' not in strategy


def test_incremental_models_use_the_partition_overwrite_strategy() -> None:
    """The three event-level models are incremental on the one strategy; the
    marts/features/scores stay table (they aggregate the full inputs)."""
    incr = {"stg_events", "stg_prompts", "attribution"}
    strategy = (
        "incremental_strategy=('insert_overwrite' if target.type == 'bigquery' "
        "else 'partition_overwrite')"
    )  # Amendment U: the adapter's native strategy on BigQuery, ours on DuckDB
    for p in (DBT / "models").rglob("*.sql"):
        text = p.read_text()
        if p.stem in incr:
            assert "materialized='incremental'" in text, p.stem
            assert strategy in text, p.stem
        else:
            assert "materialized='incremental'" not in text, p.stem


def test_session_zone_is_pinned_in_profile_and_macro() -> None:
    """Belt and braces for invariant 3: the profile pins the session zone and
    the macro pins its input as UTC (ARCHITECTURE §8: timezone() direction)."""
    assert "TimeZone: UTC" in (DBT / "profiles.yml").read_text()
    macro = (DBT / "macros" / "to_local_time.sql").read_text()
    assert "timezone('UTC', " in macro


def test_schema_event_types_equal_the_contract() -> None:
    """schema.yml's hand-written accepted_values list equals EventType (the
    generated sources.yml is derived from it; this one is not)."""
    from generator.models import EventType

    schema = (DBT / "models" / "staging" / "schema.yml").read_text()
    m = re.search(r"values: \[([^\]]+)\]", schema)
    assert m, "no accepted_values in schema.yml"
    listed = [v.strip().strip('"') for v in m.group(1).split(",")]
    assert listed == [e.value for e in EventType]


SINGULAR = {
    "assert_every_event_matches_one_dim_row.sql": "ref('stg_events')",
    "assert_dim_user_key_unique.sql": "source('raw', 'dim_user')",
    "assert_error_code_only_on_upload_failed.sql": "ref('stg_events')",
    "assert_one_label_per_prompt.sql": "ref('attribution')",
    "assert_unattributed_share_bounded.sql": "ref('attribution')",
    "assert_prompt_cohort_matches_dim.sql": "ref('stg_prompts')",
    "assert_cohort_day_partition.sql": "ref('ontime_rate_daily')",
    "assert_cohort_day_key_unique.sql": "ref('ontime_rate_daily')",
    "assert_send_time_within_band.sql": "ref('scores_send_time')",
    "assert_no_conflicting_duplicates.sql": "source('raw', 'events')",
}


def test_singular_tests_exist_and_target_their_relation() -> None:
    files = {p.name for p in (DBT / "tests").glob("*.sql")}
    assert files == set(SINGULAR)
    for name, relation in SINGULAR.items():
        assert relation in (DBT / "tests" / name).read_text(), name


def test_telemetry_is_off() -> None:
    """No services, no network: dbt's usage tracking is disabled in the
    project, and the two in-process entry points set DO_NOT_TRACK first."""
    assert "send_anonymous_usage_stats: false" in (DBT / "dbt_project.yml").read_text()
    for rel in ("pipeline/cli.py", "tests/test_staging.py"):
        text = (ROOT / rel).read_text()
        assert text.index("DO_NOT_TRACK") < text.index("from dbt.cli.main import"), rel


def test_no_dbt_packages() -> None:
    assert not (DBT / "packages.yml").exists()
    assert not (DBT / "package-lock.yml").exists()
    assert not (DBT / "dbt_packages").exists()


def test_every_model_has_description_and_a_test() -> None:
    for folder in ("staging", "attribution", "marts", "features", "scores"):
        schema = (DBT / "models" / folder / "schema.yml").read_text()
        for model in (p.stem for p in (DBT / "models" / folder).glob("*.sql")):
            block = schema.split(f"- name: {model}\n", 1)[1]
            assert "description:" in block.split("\n  - name:", 1)[0], model
            assert "data_tests:" in block.split("\n  - name:", 1)[0], model


def test_schema_label_values_equal_the_contract() -> None:
    """The five-label set (CLAUDE.md label contract): schema.yml's accepted
    values == generator Cause == eval LABELS. A sixth anywhere is a BLOCKER."""
    from eval.score import LABELS
    from generator.models import Cause

    schema = (DBT / "models" / "attribution" / "schema.yml").read_text()
    m = re.search(r"values: \[([^\]]+)\]", schema)
    assert m
    listed = [v.strip() for v in m.group(1).split(",")]
    assert listed == [c.value for c in Cause] == list(LABELS)


def test_safe_divide_is_called_by_a_model() -> None:
    """Phase 4: the rate seam has a caller (its DuckDB body was untested until
    the marts), and the call is the macro, never an inline division."""
    marts = (DBT / "models" / "marts").glob("*.sql")
    text = "\n".join(p.read_text() for p in marts)
    assert text.count("safe_divide(") == 2  # one rate per mart
    assert not re.search(r"on_time\s*/\s*prompts_delivered", text)
