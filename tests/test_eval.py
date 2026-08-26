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
        for fn in (cli.golden_cmd, cli.score_cmd, cli.report_cmd):
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
