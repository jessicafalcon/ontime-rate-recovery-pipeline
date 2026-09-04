"""Phase 7 (specs/phase-7-incremental.md): the incremental event-level models.
A real dbt build of fixtures/tiny into a tmp DuckDB, in-process, split into two
landings. No service, no network. Convergence (two landings == one), idempotence
(landing 2 twice == once), final-never-changes, dedupe across landings, the
lookback identity, no clock (a Tokyo build), and a planted row in a closed
partition surviving a landing."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

from landing import load as landing
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

    landing.load("tiny", db, through=through)
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


def status_by_prompt(db: Path) -> dict[str, str]:
    return dict(q(db, "select prompt_id, status from main_attribution.attribution"))


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
    landing1_status_by_prompt: dict[str, str]
    landing2_status_by_prompt: dict[str, str]


@pytest.fixture(scope="module")
def landings(tmp_path_factory: pytest.TempPathFactory) -> TwoLanding:
    base = tmp_path_factory.mktemp("incr")
    one = base / "one.duckdb"
    run_landing(one)  # whole set at once
    two = base / "two.duckdb"
    run_landing(two, through=pins.LANDING_SPLIT_TINY)  # bulk landing
    landing1_final = labels(two, "final")
    landing1_status = status_counts(two)
    landing1_status_by_prompt = status_by_prompt(two)
    run_landing(two)  # the late tail (full set)
    landing2_status = status_counts(two)
    landing2_labels = labels(two)
    landing2_status_by_prompt = status_by_prompt(two)
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
        landing1_status_by_prompt=landing1_status_by_prompt,
        landing2_status_by_prompt=landing2_status_by_prompt,
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
    # per-prompt (not only the aggregate): no prompt goes final -> provisional
    for prompt_id, s1 in landings.landing1_status_by_prompt.items():
        s2 = landings.landing2_status_by_prompt[prompt_id]
        assert not (s1 == "final" and s2 == "provisional"), prompt_id


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
        "import os,sys; from pathlib import Path; from landing import load as landing;"
        "os.environ['DO_NOT_TRACK']='1';"
        "db=Path(sys.argv[1]); landing.load('tiny', db);"
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


# --- invariant 1 at the exact lookback boundary (the `<=` vs `<` seam) ---------
# tiny/medium can't exercise it: their late tails (48/72 h) land >= 2 days inside
# the boundary, so the reprocess window narrowed to `<` is behaviourally
# equivalent there. This hand-made fixture lands a late row at distance exactly
# lookback_days (a 112 h-late delivery, still < lookback*24 = 120 h so the
# identity holds), which `<` would drop from a closed-looking boundary partition.


def _boundary_event(insert_id: str, event_type: str, upload: str, props: dict) -> dict:
    client = (
        "2026-01-05 08:00:00.000000"
        if event_type == "prompt_sent"
        else ("2026-01-05 08:00:05.000000")
    )
    return {
        "insert_id": insert_id,
        "event_type": event_type,
        "user_id": "u-000001",
        "device_id": "d-000001",
        "client_event_time": client,
        "server_received_time": client,
        "server_upload_time": upload,
        "event_properties": props,
    }


def _boundary_fixture(root: Path) -> None:
    """One prompt p-2 sent 2026-01-05 (partition 2026-01-05), its prompt_delivered
    uploaded 2026-01-10 00:00 (112 h late) — so with the whole set the horizon is
    2026-01-10 and the partition sits at distance lookback_days (5)."""
    fx = root / "fixtures" / "boundary"
    (fx / "raw").mkdir(parents=True)
    (fx / "dims").mkdir()
    files = {
        "events_2026-01-05.jsonl": [
            _boundary_event(
                "e-1",
                "prompt_sent",
                "2026-01-05 08:00:00.000000",
                {"prompt_id": "p-2", "cohort_id": "c-morning", "window_minutes": 60},
            )
        ],
        "events_2026-01-10.jsonl": [
            _boundary_event(
                "e-2",
                "prompt_delivered",
                "2026-01-10 00:00:00.000000",
                {"prompt_id": "p-2"},
            )
        ],
    }
    for name, rows in files.items():
        body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()
        with (fx / "raw" / (name + ".gz")).open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
                gz.write(body)
    (fx / "dims" / "dim_user.csv").write_text(
        "user_id,tz,cohort_id,signup_date,valid_from,valid_to\n"
        "u-000001,UTC,c-morning,2025-12-01,2025-12-01 00:00:00.000000,\n"
    )


def run_staging(db: Path, profile: str, through: str | None = None) -> None:
    """Load `profile` and dbt-run ONLY the staging models into `db`."""
    os.environ.setdefault("DO_NOT_TRACK", "1")
    from dbt.cli.main import dbtRunner

    landing.load(profile, db, through=through)
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
                "--select",
                "stg_events",
                "stg_prompts",
            ]
        )
    assert res.success, "dbt run (staging) failed"


def test_staging_lookback_boundary_reprocesses_a_late_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant 1 at the boundary: a late row whose partition is at distance
    exactly lookback_days must be reprocessed — the window is `<= lookback_days`,
    not `<`. Falsifies stg_events/stg_prompts narrowed to `<`."""
    root = tmp_path / "repo"
    _boundary_fixture(root)
    monkeypatch.setattr(landing, "ROOT", root)
    monkeypatch.setattr(landing, "DATA", root / "data")

    one = tmp_path / "one.duckdb"
    run_staging(one, "boundary")  # whole set: horizon 2026-01-10, distance 5

    two = tmp_path / "two.duckdb"
    run_staging(two, "boundary", through="2026-01-09")  # the late delivery absent
    run_staging(two, "boundary")  # it arrives on the boundary partition

    for rel in ("main_staging.stg_events", "main_staging.stg_prompts"):
        assert table_hash(two, rel) == table_hash(one, rel), rel
    # the late delivery — the row `<` would drop — is present after convergence
    delivered = q(
        two,
        "select delivered_at from main_staging.stg_prompts where prompt_id = 'p-2'",
    )[0][0]
    assert delivered is not None
    assert (
        q(two, "select count(*) from main_staging.stg_events where insert_id = 'e-2'")[
            0
        ][0]
        == 1
    )


def test_source_prune_margin_covers_every_profile() -> None:
    """fix/append-landing invariant 5: the BigQuery source-scan prune margin
    (`var source_prune_margin_days`) is a DECLARED FLOOR that must cover, for
    EVERY profile, the worst-case gap a reprocessed row's `server_upload_time`
    can sit below the lookback window — `ceil(late_arrival_max_hours/24)` days of
    horizon inflation (a late export batch pushes `max(server_upload_time)`
    forward) + 1 day for the client<->server tz offset (< 24 h) + 1 day for the
    <= 1 h duplicate span. If a profile's `late_arrival_max_hours` ever grew past
    the floor, this fails LOUDLY here instead of silently under-covering the
    window on the BigQuery incremental build. This is what makes the margin
    derived-and-pinned, not a tuned constant."""
    import math

    import yaml

    from generator import profiles

    proj = yaml.safe_load((ROOT / "dbt" / "dbt_project.yml").read_text())
    margin = proj["vars"]["source_prune_margin_days"]
    tz_days, dup_days = 1, 1  # any Earth tz offset < 24 h; duplicate copies <= 1 h
    for name in ("tiny", "medium", "large"):
        p = profiles.load(name)
        required = math.ceil(p.late_arrival_max_hours / 24) + tz_days + dup_days
        assert margin >= required, (name, p.late_arrival_max_hours, required, margin)
