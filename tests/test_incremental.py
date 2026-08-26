"""Phase 7 (specs/phase-7-incremental.md): the incremental event-level models.
A real dbt build of fixtures/tiny into a tmp DuckDB, in-process, split into two
landings. No service, no network. Convergence (two landings == one), idempotence
(landing 2 twice == once), final-never-changes, dedupe across landings, the
lookback identity, no clock (a Tokyo build), and a planted row in a closed
partition surviving a landing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

from loader import load as loader
from tests import pins

ROOT = Path(__file__).parent.parent
DBT = ROOT / "dbt"
MODELS = (
    "main_staging.stg_events",
    "main_staging.stg_prompts",
    "main_attribution.attribution",
)


def run_landing(db: Path, through: str | None = None) -> None:
    """Load the fixture (files uploaded on or before `through`, all when None)
    and `dbt run` into `db` — models only, so a partial landing is not judged by
    the data tests (they assume the whole set)."""
    os.environ.setdefault("DO_NOT_TRACK", "1")
    from dbt.cli.main import dbtRunner

    loader.load("tiny", db, through=through)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OTR_DUCKDB_PATH", str(db))
        res = dbtRunner().invoke(
            [
                "run",
                "--project-dir",
                str(DBT),
                "--profiles-dir",
                str(DBT),
                "--target",
                "duckdb",
                "--quiet",
                "--target-path",
                str(db.parent / "t"),
            ]
        )
    assert res.success, "dbt run failed"


def q(db: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def table_hash(db: Path, relation: str) -> str:
    name = relation.split(".")[1]
    return q(
        db,
        f"select md5(string_agg(r::varchar, '|' order by r::varchar)) "
        f"from (select {name} as r from {relation})",
    )[0][0]


def hashes(db: Path) -> dict[str, str]:
    return {m: table_hash(db, m) for m in MODELS}


def labels(db: Path, status: str | None = None) -> dict[str, str]:
    where = f" where status = '{status}'" if status else ""
    return dict(
        q(db, f"select prompt_id, label from main_attribution.attribution{where}")
    )


def status_counts(db: Path) -> dict[str, int]:
    return dict(
        q(
            db,
            "select status, count(*) from main_attribution.attribution group by status",
        )
    )


@dataclass
class TwoLanding:
    one: Path  # single whole-set build
    two: Path  # bulk landing, then the late tail
    h_one: dict[str, str]
    h_two: dict[str, str]
    h_two_again: dict[str, str]
    landing1_final: dict[str, str]
    landing1_status: dict[str, int]
    landing2_status: dict[str, int]
    landing2_labels: dict[str, str]


@pytest.fixture(scope="module")
def landings(tmp_path_factory: pytest.TempPathFactory) -> TwoLanding:
    base = tmp_path_factory.mktemp("incr")
    one = base / "one.duckdb"
    run_landing(one)  # whole set at once
    two = base / "two.duckdb"
    run_landing(two, through=pins.LANDING_SPLIT_TINY)  # bulk landing
    landing1_final = labels(two, "final")
    landing1_status = status_counts(two)
    run_landing(two)  # the late tail (full set)
    landing2_status = status_counts(two)
    landing2_labels = labels(two)
    h_two = hashes(two)
    run_landing(two)  # again — idempotence
    return TwoLanding(
        one=one,
        two=two,
        h_one=hashes(one),
        h_two=h_two,
        h_two_again=hashes(two),
        landing1_final=landing1_final,
        landing1_status=landing1_status,
        landing2_status=landing2_status,
        landing2_labels=landing2_labels,
    )


def test_two_landings_equal_one_landing(landings: TwoLanding) -> None:
    """Convergence (invariant 1): a split landing builds to the same rows."""
    assert landings.h_two == landings.h_one


def test_two_landing_attribution_matches_the_frozen_golden(
    landings: TwoLanding,
) -> None:
    """The frozen expected/attribution.csv (four columns) is byte-identical after
    the two-landing build — the convergence golden (status is not exported)."""
    from eval import golden

    rows = golden.export_rows(landings.two, golden.ATTRIBUTION)
    frozen = golden.parse(
        (ROOT / "fixtures" / "tiny" / "expected" / "attribution.csv").read_text(),
        golden.ATTRIBUTION,
    )
    assert golden.diff_rows(rows, frozen, golden.ATTRIBUTION.key_width) == []


def test_landing_two_twice_is_a_noop(landings: TwoLanding) -> None:
    """Idempotence (invariant 2): the partition-overwrite converges on a re-run."""
    assert landings.h_two_again == landings.h_two


def test_final_labels_never_change(landings: TwoLanding) -> None:
    """Invariant 3: every prompt final after landing 1 has the same label after
    landing 2 (its partition is closed and never reprocessed)."""
    assert landings.landing1_final, "no final partitions after landing 1"
    for prompt_id, label in landings.landing1_final.items():
        assert landings.landing2_labels[prompt_id] == label, prompt_id


def test_status_advances_provisional_to_final_only(landings: TwoLanding) -> None:
    """Status is monotone: the final count grows over landings, never shrinks."""
    assert landings.landing1_status == {
        "final": pins.LANDING1_FINAL_PROMPTS_TINY,
        "provisional": pins.STG_PROMPT_ROWS - pins.LANDING1_FINAL_PROMPTS_TINY,
    }
    assert landings.landing2_status == {
        "final": pins.FINAL_PROMPTS_TINY,
        "provisional": pins.PROVISIONAL_PROMPTS_TINY,
    }
    assert landings.landing2_status["final"] >= landings.landing1_status["final"]


def test_single_landing_final_count_matches_pin(landings: TwoLanding) -> None:
    """The whole-set build (horizon 2026-01-13) closes prompt_date <= 2026-01-08."""
    assert status_counts(landings.one) == {
        "final": pins.FINAL_PROMPTS_TINY,
        "provisional": pins.PROVISIONAL_PROMPTS_TINY,
    }
    finals = q(
        landings.one,
        "select distinct cast(prompt_date as varchar) "
        "from main_attribution.attribution where status = 'final' order by 1",
    )
    assert [r[0] for r in finals] == [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
    ]


def test_identity_lookback_exceeds_late_arrival() -> None:
    """Invariant 4: lookback_days * 24 > late_arrival_max_hours on every profile,
    so a late event never lands on a closed partition."""
    for pj in sorted((ROOT / "generator" / "profiles").glob("*.json")):
        profile = json.loads(pj.read_text())
        assert pins.LOOKBACK_DAYS * 24 > profile["late_arrival_max_hours"], pj.name


def test_duplicate_straddling_a_landing_dedupes_to_one(
    tmp_path: Path,
) -> None:
    """Invariant 5: a duplicate whose copies land on different upload dates
    (e-0000259: 2026-01-05, 2026-01-06) stages to one row, the earliest upload —
    split so copy A is in landing 1 and copy B arrives in landing 2."""
    db = tmp_path / "dedupe.duckdb"
    run_landing(db, through="2026-01-05")  # copy A only (upload 2026-01-05)
    run_landing(db)  # copy B arrives (upload 2026-01-06)
    rows = q(
        db,
        "select count(*), min(cast(server_upload_time as varchar)) "
        "from main_staging.stg_events "
        f"where insert_id = '{pins.STRADDLING_DUPLICATE_TINY}'",
    )
    raw_min = q(
        db,
        "select min(cast(server_upload_time as varchar)) from raw.events "
        f"where insert_id = '{pins.STRADDLING_DUPLICATE_TINY}'",
    )[0][0]
    assert rows[0][0] == 1, "the duplicate did not dedupe across landings"
    assert rows[0][1] == raw_min, "the kept copy is not the earliest upload"


def test_planted_row_in_a_closed_partition_survives_a_landing(tmp_path: Path) -> None:
    """Invariant 7: a landing never rewrites a closed partition. Plant a row in a
    closed prompt_date (2026-01-05), run the late tail, the row is untouched."""
    db = tmp_path / "planted.duckdb"
    run_landing(db, through=pins.LANDING_SPLIT_TINY)
    con = duckdb.connect(str(db))
    try:
        cols = [
            r[0]
            for r in con.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'main_attribution' "
                "and table_name = 'attribution' order by ordinal_position"
            ).fetchall()
        ]
        vals = []
        for c in cols:
            if c == "prompt_id":
                vals.append("'planted-1'")
            elif c == "prompt_date":
                vals.append("date '2026-01-05'")
            elif c == "label":
                vals.append("'on_time'")
            elif c == "status":
                vals.append("'final'")
            else:
                vals.append("null")
        con.execute(
            f"insert into main_attribution.attribution ({', '.join(cols)}) "
            f"values ({', '.join(vals)})"
        )
    finally:
        con.close()
    run_landing(db)  # the late tail — must not touch the closed 2026-01-05 partition
    got = q(
        db,
        "select label, status from main_attribution.attribution "
        "where prompt_id = 'planted-1'",
    )
    assert got == [("on_time", "final")], "a closed partition was rewritten"


def test_build_under_tokyo_is_identical(tmp_path: Path, landings: TwoLanding) -> None:
    """Invariant 6: no clock — a build under a non-UTC host zone is identical
    (the session zone is pinned UTC; local time uses the tz macro, not the host)."""
    script = (
        "import os,sys; from pathlib import Path; from loader import load as loader;"
        "os.environ['DO_NOT_TRACK']='1';"
        "db=Path(sys.argv[1]); loader.load('tiny', db);"
        "os.environ['OTR_DUCKDB_PATH']=str(db);"
        "from dbt.cli.main import dbtRunner;"
        "r=dbtRunner().invoke(['run','--project-dir',sys.argv[2],'--profiles-dir',sys.argv[2],"
        "'--target','duckdb','--quiet','--target-path',str(db.parent/'t')]);"
        "sys.exit(0 if r.success else 1)"
    )
    db = tmp_path / "tokyo.duckdb"
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-c", script, str(db), str(DBT)],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert hashes(db) == landings.h_one
