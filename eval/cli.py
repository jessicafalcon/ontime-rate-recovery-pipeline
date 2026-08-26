"""`make attribution-golden PROFILE=<p> [WRITE=yes]`, `make eval PROFILE=<p>`
and `make report PROFILE=<p> [WRITE=yes]`.

golden — diff the built attribution table (data/<p>.duckdb) against
         fixtures/<p>/expected/attribution.csv; exit 1 on any differing row.
         With --write yes: write data/out/<p>/expected/attribution.csv instead
         (never fixtures/ — `make freeze` is the only writer there).
score  — label accuracy vs <p>/truth/prompts.jsonl; exit 1 below the pin
         (tests/pins.py::LABEL_ACCURACY). Phase 5: plus reachable-centre MAE
         and coverage vs <p>/truth/users.jsonl against the MAE_*/COVERAGE_*
         pins. truth/ is fixtures/<p>/ when frozen, else data/out/<p>/
         (printed `(unfrozen)`) — the only two roots eval ever reads.
scores-golden — the golden shape over scores_send_time
         (expected/scores_send_time.csv).
report — the same golden shape over ontime_rate_daily
         (expected/ontime_rate_daily.csv) plus the overall on-time rate vs
         tests/pins.py::ONTIME_RATE; console only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def truth_dir(profile: str) -> Path:
    """fixtures/<p>/truth when the profile is frozen, else data/out/<p>/truth
    — the generator's own output, still the side-file only eval reads."""
    frozen = FIXTURES / profile / "truth"
    if frozen.is_dir():
        return frozen
    return DATA_OUT / profile / "truth"


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


def scores_golden_cmd(profile: str, write: str = "") -> int:
    code, rows, differ = _golden(
        profile, write, golden.SCORES_SEND_TIME, "scores-golden"
    )
    if differ >= 0:
        verdict = "FAIL" if code else "OK"
        print(f"scores-golden {verdict}: {profile}, {rows} rows, {differ} differ")
    return code


def score_cmd(profile: str) -> int:
    validate_name("PROFILE", profile)
    from tests import pins  # every pin lives there

    truth = truth_dir(profile)
    tag = "" if truth.is_relative_to(FIXTURES) else " (unfrozen)"
    prompts_path = truth / "prompts.jsonl"
    users_path = truth / "users.jsonl"
    for path in (prompts_path, users_path):
        if not path.is_file():
            die(f"eval: refused — no {_rel(path)}")
    db = _db(profile)
    built = score.built_labels(db)
    truth_labels = score.truth_labels(prompts_path)
    acc = score.label_accuracy(built, truth_labels)
    counts = score.label_counts(built)
    print(f"eval truth: {_rel(truth)}{tag}")
    print("eval labels: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    verdict = "OK" if acc >= pins.LABEL_ACCURACY else "FAIL"
    print(
        f"eval {verdict}: {profile}, accuracy {acc:.3f} "
        f"(pin {pins.LABEL_ACCURACY:.3f}), {len(truth_labels)} prompts"
    )
    windows = score.truth_windows(users_path)
    scores = score.built_scores(db)
    mae = score.reachable_center_mae(scores, windows)
    cov = score.coverage(scores, windows)
    mae_pin, cov_pin = pins.SEND_TIME_PINS.get(profile, (None, None))
    if mae_pin is None:
        print(f"eval FAIL: {profile}, no MAE/coverage pin in tests/pins.py")
        return 1
    on_pin = abs(mae - mae_pin) < 1e-9 and abs(cov - cov_pin) < 1e-9
    verdict2 = "OK" if on_pin else "FAIL"
    print(
        f"eval {verdict2}: {profile}, mae {mae:.6f} h (pin {mae_pin:.6f}), "
        f"coverage {cov:.6f} (pin {cov_pin:.6f}), {len(windows)} users"
    )
    return 0 if verdict == "OK" and verdict2 == "OK" else 1


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
    sg = sub.add_parser("scores-golden")
    sg.add_argument("profile")
    sg.add_argument("--write", default="")
    a = ap.parse_args(argv)
    if a.cmd == "golden":
        return golden_cmd(a.profile, a.write)
    if a.cmd == "report":
        return report_cmd(a.profile, a.write)
    if a.cmd == "scores-golden":
        return scores_golden_cmd(a.profile, a.write)
    return score_cmd(a.profile)


if __name__ == "__main__":
    sys.exit(main())
