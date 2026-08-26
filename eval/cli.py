"""`make attribution-golden PROFILE=<p> [WRITE=yes]`, `make eval PROFILE=<p>`
and `make report PROFILE=<p> [WRITE=yes]`.

golden — diff the built attribution table (data/<p>.duckdb) against
         fixtures/<p>/expected/attribution.csv; exit 1 on any differing row.
         With --write yes: write data/out/<p>/expected/attribution.csv instead
         (never fixtures/ — `make freeze` is the only writer there).
score  — label accuracy vs fixtures/<p>/truth/prompts.jsonl; exit 1 below the
         pin (tests/pins.py::LABEL_ACCURACY).
report — the same golden shape over ontime_rate_daily
         (expected/ontime_rate_daily.csv) plus the overall on-time rate vs
         tests/pins.py::ONTIME_RATE; console only."""

from __future__ import annotations

import argparse
import sys

from eval import golden, report, score
from loader import load as loader
from loader.cli import die, validate_name

DATA_OUT = loader.ROOT / "data" / "out"
FIXTURES = loader.ROOT / "fixtures"
EXPECTED = golden.ATTRIBUTION.file


def _rel(path) -> str:
    return (
        str(path.relative_to(loader.ROOT))
        if path.is_relative_to(loader.ROOT)
        else str(path)
    )


def _db(profile: str):
    db = loader.db_path(profile)
    if not db.is_file():
        rel = db.relative_to(loader.ROOT)
        die(f"refused — no {rel}; run `make dbt-build PROFILE={profile}` first")
    return db


def _golden(
    profile: str, write: str, spec: golden.Golden, name: str
) -> tuple[int, int, int]:
    """(exit code, rows, differing rows); on WRITE=yes the last two are (rows, -1)."""
    validate_name("PROFILE", profile)
    if write not in ("", "yes"):
        die(f"{name}: refused — WRITE takes only the literal `yes`")
    rows = golden.export_rows(_db(profile), spec)
    if write == "yes":
        out = DATA_OUT / profile / spec.file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(golden.render(rows, spec))
        print(f"{name} WROTE: {_rel(out)}, {len(rows)} rows")
        return 0, len(rows), -1
    frozen_path = FIXTURES / profile / spec.file
    if not frozen_path.is_file():
        rel = frozen_path.relative_to(loader.ROOT)
        die(f"{name}: refused — no {rel} (WRITE=yes, then `make freeze`)")
    diff = golden.diff_rows(
        rows, golden.parse(frozen_path.read_text(), spec), spec.key_width
    )
    for line in diff[:20]:
        print(f"    {line}")
    return (1 if diff else 0), len(rows), len(diff)


def golden_cmd(profile: str, write: str = "") -> int:
    code, rows, differ = _golden(
        profile, write, golden.ATTRIBUTION, "attribution-golden"
    )
    if differ >= 0:
        verdict = "FAIL" if code else "OK"
        print(f"attribution-golden {verdict}: {profile}, {rows} rows, {differ} differ")
    return code


def report_cmd(profile: str, write: str = "") -> int:
    code, rows, differ = _golden(profile, write, golden.ONTIME_RATE_DAILY, "report")
    if differ < 0:
        return code
    from tests.pins import ONTIME_RATE  # the pin lives with every other pin

    rate = report.overall_rate(_db(profile))
    on_pin = rate is not None and abs(rate - ONTIME_RATE) < 1e-9
    verdict = "OK" if code == 0 and on_pin else "FAIL"
    shown = "undefined" if rate is None else f"{rate:.6f}"
    print(
        f"report {verdict}: {profile}, {rows} cohort-days, {differ} differ, "
        f"ontime_rate {shown} (pin {ONTIME_RATE:.6f})"
    )
    return 0 if verdict == "OK" else 1


def score_cmd(profile: str) -> int:
    validate_name("PROFILE", profile)
    from tests.pins import LABEL_ACCURACY  # the pin lives with every other pin

    truth_path = FIXTURES / profile / "truth" / "prompts.jsonl"
    if not truth_path.is_file():
        die(f"eval: refused — no {truth_path.relative_to(loader.ROOT)}")
    built = score.built_labels(_db(profile))
    truth = score.truth_labels(truth_path)
    acc = score.label_accuracy(built, truth)
    counts = score.label_counts(built)
    print("eval labels: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    verdict = "OK" if acc >= LABEL_ACCURACY else "FAIL"
    print(
        f"eval {verdict}: {profile}, accuracy {acc:.3f} (pin {LABEL_ACCURACY:.3f}), "
        f"{len(truth)} prompts"
    )
    return 0 if verdict == "OK" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("golden")
    g.add_argument("profile")
    g.add_argument("--write", default="")
    sub.add_parser("score").add_argument("profile")
    r = sub.add_parser("report")
    r.add_argument("profile")
    r.add_argument("--write", default="")
    a = ap.parse_args(argv)
    if a.cmd == "golden":
        return golden_cmd(a.profile, a.write)
    if a.cmd == "report":
        return report_cmd(a.profile, a.write)
    return score_cmd(a.profile)


if __name__ == "__main__":
    sys.exit(main())
