"""eval/ pins (spec Phase 3 invariants 5, 6): accuracy vs truth reproduces the
pin and moves when a label is flipped; the golden diff reports a planted
difference; WRITE is the literal `yes` only. Reuses the module build."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_attribution import built as built  # noqa: PLC0414 — module fixture
from test_staging import q

from eval import cli, golden, report, score
from tests import pins

ROOT = Path(__file__).parent.parent
TRUTH = ROOT / "fixtures" / "tiny" / "truth" / "prompts.jsonl"


def test_label_accuracy_matches_pin(built: Path) -> None:  # noqa: F811
    built_labels = score.built_labels(built)
    truth = score.truth_labels(TRUTH)
    assert len(truth) == pins.ATTRIBUTION_ROWS
    assert score.label_accuracy(built_labels, truth) == pins.LABEL_ACCURACY


def test_truth_label_counts_match_pin() -> None:
    assert score.label_counts(score.truth_labels(TRUTH)) == pins.TRUTH_LABEL_COUNTS


def test_accuracy_drops_when_a_label_is_flipped() -> None:
    truth = {"p-1": "on_time", "p-2": "timing_gap", "p-3": "upload_fault"}
    assert score.label_accuracy(dict(truth), truth) == 1.0
    flipped = {**truth, "p-2": "on_time"}
    assert score.label_accuracy(flipped, truth) == pytest.approx(2 / 3)
    assert score.label_accuracy({"p-1": "on_time"}, truth) == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        score.label_accuracy({}, {})


def test_golden_reports_a_planted_difference(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    rows = golden.export_rows(built)
    parsed = golden.parse(golden.render(rows))
    assert parsed == rows
    changed = [(*r[:3], "timing_gap" if r[3] == "on_time" else r[3]) for r in rows]
    changed = changed[:-1]  # and one row missing
    extra = changed + [("p-999999", "u-1", "c-morning", "on_time")]
    diff = golden.diff_rows(rows, extra)
    assert len(diff) == pins.TRUTH_LABEL_COUNTS["on_time"] + 1 + 1
    assert diff[-1].startswith("p-999999: missing")
    assert sum(d.endswith("frozen=None") for d in diff) == 1  # the extra built row
    # through the CLI: a planted frozen file with one changed label → exit 1
    fix = tmp_path / "fix" / "tiny" / "expected"
    fix.mkdir(parents=True)
    planted = list(rows)
    planted[0] = (*planted[0][:3], "unattributed")
    fix.joinpath("attribution.csv").write_text(golden.render(planted))
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.golden_cmd("tiny") == 1
    assert (
        "attribution-golden FAIL: tiny, 140 rows, 1 differ" in capsys.readouterr().out
    )
    with pytest.raises(ValueError, match="golden header"):
        golden.parse("a,b\n1,2\n")


def test_golden_write_only_on_literal_yes(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    for bad in ("YES", "true", "1", " yes"):
        with pytest.raises(SystemExit) as e:
            cli.golden_cmd("tiny", bad)
        assert e.value.code == 2
    assert not (tmp_path / "out").exists()
    assert cli.golden_cmd("tiny", "yes") == 0
    out = tmp_path / "out" / "tiny" / "expected" / "attribution.csv"
    assert out.read_text() == golden.render(golden.export_rows(built))
    assert not (tmp_path / "fix").exists()  # never fixtures/
    assert "attribution-golden WROTE" in capsys.readouterr().out


def test_cli_refuses_bad_profile_before_any_path(monkeypatch) -> None:
    for name in ("../x", "", "a b", "Tiny"):
        for fn in (
            cli.golden_cmd,
            cli.score_cmd,
            cli.report_cmd,
            cli.scores_golden_cmd,
            cli.simulate_cmd,
        ):
            with pytest.raises(SystemExit) as e:
                fn(name)
            assert e.value.code == 2


def test_score_cli_reproduces_the_pin(built: Path, monkeypatch, capsys) -> None:  # noqa: F811
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.score_cmd("tiny") == 0
    out = capsys.readouterr().out
    assert "eval OK: tiny, accuracy 1.000 (pin 1.000), 140 prompts" in out
    assert "on_time=75" in out and "unattributed=6" in out
    (n,) = q(built, "select count(*) from main_attribution.attribution")[0]
    assert n == pins.ATTRIBUTION_ROWS


# ------------------------------------------------- Phase 4: report


def test_bigquery_rows_render_like_duckdb_rows() -> None:
    """Phase 9b invariant 2: one renderer for both engines. A tz-aware TIMESTAMP
    (what google-cloud-bigquery returns) renders as DuckDB's naive UTC
    timestamp; a DATE, an int, a float, a bool and a NULL render as they do
    from DuckDB; rows sort by the declared key, not arrival order."""
    from datetime import UTC, date, datetime, timedelta, timezone

    aware = datetime(2026, 1, 12, 0, 47, tzinfo=UTC)
    tokyo = datetime(2026, 1, 12, 9, 47, tzinfo=timezone(timedelta(hours=9)))
    naive = datetime(2026, 1, 12, 0, 47)
    assert golden.normalize_cell(aware) == golden.normalize_cell(naive)
    assert golden.normalize_cell(tokyo) == "2026-01-12 00:47:00"
    assert golden.normalize_cell(None) == ""
    assert golden.normalize_cell(date(2026, 1, 5)) == "2026-01-05"
    assert golden.normalize_cell(0.609756) == "0.609756"
    assert golden.normalize_cell(3) == "3" and golden.normalize_cell(True) == "True"
    rows = golden.rows_from([("u-2", "c", aware), ("u-1", "c", None)])
    assert rows == [("u-1", "c", ""), ("u-2", "c", "2026-01-12 00:47:00")]
    sql = golden.select_sql(golden.SCORES_SEND_TIME, "p.ontime.scores_send_time")
    assert sql.endswith("from p.ontime.scores_send_time")


DAILY = golden.ONTIME_RATE_DAILY


def test_report_reports_a_planted_difference(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    rows = golden.export_rows(built, DAILY)
    assert golden.parse(golden.render(rows, DAILY), DAILY) == rows
    fix = tmp_path / "fix" / "tiny" / "expected"
    fix.mkdir(parents=True)
    planted = list(rows)
    planted[0] = (*planted[0][:4], "0", *planted[0][5:])  # one on_time count changed
    fix.joinpath("ontime_rate_daily.csv").write_text(golden.render(planted, DAILY))
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.report_cmd("tiny") == 1
    out = capsys.readouterr().out
    assert f"{rows[0][0]}/{rows[0][1]}: changed" in out
    assert "report FAIL: tiny, 14 cohort-days, 1 differ" in out
    with pytest.raises(ValueError, match="golden header"):
        golden.parse(golden.render(rows), DAILY)  # the attribution header


def test_report_fails_when_the_rate_is_off_the_pin(built: Path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.report_cmd("tiny") == 0
    assert (
        "report OK: tiny, 14 cohort-days, 0 differ, ontime_rate 0.609756 (pin 0.609756)"
        in (capsys.readouterr().out)
    )
    monkeypatch.setattr(report, "overall_rate", lambda db: pins.ONTIME_RATE + 1e-6)
    assert cli.report_cmd("tiny") == 1
    assert "ontime_rate 0.609757 (pin 0.609756)" in capsys.readouterr().out  # distinct
    monkeypatch.setattr(report, "overall_rate", lambda db: None)
    assert cli.report_cmd("tiny") == 1
    assert "ontime_rate undefined (pin 0.609756)" in capsys.readouterr().out


def test_report_write_only_on_literal_yes(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    for bad in ("YES", "true", "1", " yes"):
        with pytest.raises(SystemExit) as e:
            cli.report_cmd("tiny", bad)
        assert e.value.code == 2
    assert not (tmp_path / "out").exists()
    assert cli.report_cmd("tiny", "yes") == 0
    out = tmp_path / "out" / "tiny" / "expected" / "ontime_rate_daily.csv"
    assert out.read_text() == golden.render(golden.export_rows(built, DAILY), DAILY)
    assert not (tmp_path / "fix").exists()  # never fixtures/
    assert "report WROTE" in capsys.readouterr().out


# ------------------------------------------------- Phase 5: MAE, coverage, golden

SCORES = golden.SCORES_SEND_TIME
USERS = ROOT / "fixtures" / "tiny" / "truth" / "users.jsonl"


def test_circular_diff_is_the_short_arc() -> None:
    assert score.circular_abs_diff_hours(23, 1) == 2
    assert score.circular_abs_diff_hours(1, 23) == 2
    assert score.circular_abs_diff_hours(0.5, 12.5) == 12
    assert score.circular_abs_diff_hours(8.25, 8.0) == pytest.approx(0.25)


def test_score_cli_prints_mae_and_coverage(built: Path, monkeypatch, capsys) -> None:  # noqa: F811
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.score_cmd("tiny") == 0
    out = capsys.readouterr().out
    assert "eval truth: fixtures/tiny/truth\n" in out
    assert (
        f"eval OK: tiny, mae {pins.MAE_TINY:.6f} h (pin {pins.MAE_TINY:.6f}), "
        f"coverage {pins.COVERAGE_TINY:.6f} (pin {pins.COVERAGE_TINY:.6f}), 20 users"
    ) in out


def test_planted_center_shift_raises_mae(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    """Every truth centre + 3 h: MAE moves off the pin, exit 1; coverage of
    the served time drops too (a 6 h window shifted by 3 h)."""
    import json

    fix = tmp_path / "fix" / "tiny" / "truth"
    fix.mkdir(parents=True)
    fix.joinpath("prompts.jsonl").write_text(TRUTH.read_text())
    shifted = []
    for line in USERS.read_text().splitlines():
        rec = json.loads(line)
        rec["reachable_center_local_hour"] = (
            rec["reachable_center_local_hour"] + 3
        ) % 24
        shifted.append(json.dumps(rec))
    fix.joinpath("users.jsonl").write_text("\n".join(shifted) + "\n")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.score_cmd("tiny") == 1
    out = capsys.readouterr().out
    assert "accuracy 1.000" in out  # labels untouched
    assert "eval FAIL: tiny, mae" in out
    windows = score.truth_windows(fix / "users.jsonl")
    scores = score.built_scores(built)
    assert score.reachable_center_mae(scores, windows) > pins.MAE_TINY + 1
    assert score.coverage(scores, windows) < pins.COVERAGE_TINY


def test_a_missing_user_counts_as_the_worst_case() -> None:
    truth = {"u-1": (8.0, 6.0), "u-2": (20.0, 6.0)}
    built = {"u-1": (8.0, 8.0)}
    assert score.reachable_center_mae(built, truth) == 6.0  # (0 + 12) / 2
    assert score.coverage(built, truth) == 0.5
    with pytest.raises(ValueError, match="no truth users"):
        score.reachable_center_mae(built, {})


def test_eval_reads_unfrozen_truth_and_says_so(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    """No fixtures/<p>/truth → data/out/<p>/truth, printed `(unfrozen)`; the
    two roots are the only candidates (invariant 10)."""
    out_dir = tmp_path / "out" / "tiny" / "truth"
    out_dir.mkdir(parents=True)
    out_dir.joinpath("prompts.jsonl").write_text(TRUTH.read_text())
    out_dir.joinpath("users.jsonl").write_text(USERS.read_text())
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.truth_dir("tiny") == out_dir
    assert cli.score_cmd("tiny") == 0
    out = capsys.readouterr().out
    assert "(unfrozen)" in out and "eval OK: tiny, mae" in out
    (tmp_path / "fix" / "tiny" / "truth").mkdir(parents=True)
    assert cli.truth_dir("tiny") == tmp_path / "fix" / "tiny" / "truth"  # frozen wins
    with pytest.raises(SystemExit) as e:
        cli.score_cmd("tiny")  # frozen dir exists but is empty: refused, no fallback
    assert e.value.code == 2


def test_scores_golden_reports_a_planted_difference(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    rows = golden.export_rows(built, SCORES)
    assert golden.parse(golden.render(rows, SCORES), SCORES) == rows
    fix = tmp_path / "fix" / "tiny" / "expected"
    fix.mkdir(parents=True)
    planted = list(rows)
    planted[0] = (*planted[0][:2], "23", *planted[0][3:])  # one send hour changed
    fix.joinpath("scores_send_time.csv").write_text(golden.render(planted, SCORES))
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.scores_golden_cmd("tiny") == 1
    out = capsys.readouterr().out
    assert f"{rows[0][0]}: changed" in out
    assert "scores-golden FAIL: tiny, 20 rows, 1 differ" in out


def test_scores_golden_write_only_on_literal_yes(
    built: Path, tmp_path: Path, monkeypatch, capsys
):  # noqa: F811
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    for bad in ("YES", "true", "1", " yes"):
        with pytest.raises(SystemExit) as e:
            cli.scores_golden_cmd("tiny", bad)
        assert e.value.code == 2
    assert not (tmp_path / "out").exists()
    assert cli.scores_golden_cmd("tiny", "yes") == 0
    out = tmp_path / "out" / "tiny" / "expected" / "scores_send_time.csv"
    assert out.read_text() == golden.render(golden.export_rows(built, SCORES), SCORES)
    assert not (tmp_path / "fix").exists()  # never fixtures/


# ------------------------------------------------- Phase 6: the generated blocks


def _tmp_docs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Copies of the two docs the block writers may touch, redirected."""
    res = tmp_path / "docs" / "RESULTS.md"
    ab = tmp_path / "docs" / "AB_DESIGN.md"
    res.parent.mkdir()
    res.write_text(cli.RESULTS.read_text())
    ab.write_text(cli.AB_DESIGN.read_text())
    monkeypatch.setattr(cli, "RESULTS", res)
    monkeypatch.setattr(cli, "AB_DESIGN", ab)
    return res, ab


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_simulate_check_mode_matches_and_exits_1_on_drift(
    built: Path, tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: F811
    res, _ = _tmp_docs(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.simulate_cmd("tiny") == 0
    out = capsys.readouterr().out
    assert "simulate truth: fixtures/tiny/truth\n" in out
    assert "simulate OK: tiny, 140 prompts, 3 arms, block matches" in out
    text = res.read_text()
    res.write_text(text.replace("| data | 75 |", "| data | 74 |"))  # inside the markers
    assert cli.simulate_cmd("tiny") == 1
    out = capsys.readouterr().out
    assert "simulate FAIL: tiny, 140 prompts, 3 arms, block differs" in out
    assert "-| data | 74 |" in out and "+| data | 75 |" in out
    res.write_text(text.replace("## How to read a block", "## How to read"))  # outside
    assert cli.simulate_cmd("tiny") == 0


def test_simulate_write_only_on_literal_yes(
    built: Path, tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: F811
    res, ab = _tmp_docs(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    original = res.read_text()
    text = original.replace("| data | 75 |", "| data | 0 |")
    res.write_text(text)
    for bad in ("YES", "true", "1", " yes"):
        with pytest.raises(SystemExit) as e:
            cli.simulate_cmd("tiny", bad)
        assert e.value.code == 2
    assert res.read_text() == text  # nothing written
    before = _snapshot(tmp_path)
    assert cli.simulate_cmd("tiny", "yes") == 0
    assert "simulate WROTE: " in capsys.readouterr().out
    after = _snapshot(tmp_path)
    assert set(after) == set(before)
    assert [k for k in after if after[k] != before[k]] == ["docs/RESULTS.md"]
    assert res.read_text() == original  # the block is back; prose and medium untouched
    assert ab.read_text() == cli.AB_DESIGN.read_text()


def test_simulate_refuses_a_missing_marker_pair(
    built: Path, tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: F811
    res, _ = _tmp_docs(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    text = res.read_text().replace("<!-- simulate:end tiny -->", "")
    res.write_text(text)
    for write in ("", "yes"):
        with pytest.raises(SystemExit) as e:
            cli.simulate_cmd("tiny", write)
        assert e.value.code == 2
        assert "no marker pair for tiny" in capsys.readouterr().out
    assert res.read_text() == text
    res.unlink()
    with pytest.raises(SystemExit) as e:
        cli.simulate_cmd("tiny", "yes")
    assert e.value.code == 2
    assert not res.exists()  # never created


def test_simulate_says_unfrozen_for_a_data_out_profile(
    built: Path, tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: F811
    _tmp_docs(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "nofix")
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    out_truth = tmp_path / "out" / "tiny" / "truth"
    out_truth.mkdir(parents=True)
    for f in ("prompts.jsonl", "users.jsonl"):
        out_truth.joinpath(f).write_bytes(
            (cli.loader.ROOT / "fixtures" / "tiny" / "truth" / f).read_bytes()
        )
    monkeypatch.setattr(cli.loader, "db_path", lambda p: built)
    assert cli.simulate_cmd("tiny") == 0
    assert "(unfrozen)" in capsys.readouterr().out


def test_power_write_only_on_literal_yes(tmp_path: Path, monkeypatch, capsys) -> None:
    res, ab = _tmp_docs(tmp_path, monkeypatch)
    original = ab.read_text()
    assert cli.power_cmd("") == 0
    assert "power OK: 6 rows, block matches" in capsys.readouterr().out
    planted = original.replace("| tiny | 0.609756 | 1 |", "| tiny | 0.609756 | 9 |")
    ab.write_text(planted)
    assert cli.power_cmd("") == 1
    for bad in ("YES", "true", "1", " yes"):
        with pytest.raises(SystemExit) as e:
            cli.power_cmd(bad)
        assert e.value.code == 2
    assert ab.read_text() == planted
    before = _snapshot(tmp_path)
    assert cli.power_cmd("yes") == 0
    after = _snapshot(tmp_path)
    assert [k for k in after if after[k] != before[k]] == ["docs/AB_DESIGN.md"]
    assert ab.read_text() == original
    ab.write_text(original.replace("<!-- power:begin -->", ""))
    with pytest.raises(SystemExit) as e:
        cli.power_cmd("yes")
    assert e.value.code == 2


def test_blocks_replace_only_the_marked_bytes() -> None:
    from eval import blocks

    text = "head\n<!-- x:begin -->\nold\n<!-- x:end -->\ntail\n"
    assert blocks.find_block(text, "<!-- x:begin -->", "<!-- x:end -->") == "old\n"
    new = blocks.replace_block(text, "<!-- x:begin -->", "<!-- x:end -->", "new\n")
    assert new == "head\n<!-- x:begin -->\nnew\n<!-- x:end -->\ntail\n"
    assert blocks.find_block(text, "<!-- y:begin -->", "<!-- x:end -->") is None
    assert (
        blocks.find_block(
            "<!-- x:end -->\n<!-- x:begin -->\n", "<!-- x:begin -->", "<!-- x:end -->"
        )
        is None
    )
    with pytest.raises(ValueError):
        blocks.replace_block("no markers", "<!-- x:begin -->", "<!-- x:end -->", "z")
    assert blocks.diff_block("a\n", "a\n") == []
    assert blocks.diff_block("a\n", "b\n")
