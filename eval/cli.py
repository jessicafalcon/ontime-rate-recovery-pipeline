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
         tests/pins.py::ONTIME_RATE; console only.
simulate — Phase 6: the counterfactual simulation (eval/simulate.py) rendered
         as the <profile> block of docs/RESULTS.md; check mode diffs the
         block byte-for-byte (exit 1 on drift); --write yes replaces only
         the bytes between the profile's markers (a missing pair refuses).
power  — Phase 6: the A/B power table (eval/power.py) as the block of
         docs/AB_DESIGN.md, same check / --write yes shape.
readme — Phase 13: the README first-screen block (README.md) and the findings
         chart (docs/img/lift.svg), rendered from tests/pins.py + the RESULTS
         blocks; same check / --write yes shape (writes both artifacts)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval import blocks, golden, power, readme, report, score, simulate
from landing import load as landing
from landing.cli import die, validate_name

DATA_OUT = landing.ROOT / "data" / "out"
FIXTURES = landing.ROOT / "fixtures"
EXPECTED = golden.ATTRIBUTION.file
RESULTS = landing.ROOT / "docs" / "RESULTS.md"
AB_DESIGN = landing.ROOT / "docs" / "AB_DESIGN.md"
README = landing.ROOT / "README.md"
LIFT_SVG = landing.ROOT / "docs" / "img" / "lift.svg"


def _rel(path) -> str:
    return (
        str(path.relative_to(landing.ROOT))
        if path.is_relative_to(landing.ROOT)
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
    db = landing.db_path(profile)
    if not db.is_file():
        rel = db.relative_to(landing.ROOT)
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
        rel = frozen_path.relative_to(landing.ROOT)
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


def _block_cmd(
    name: str, write: str, path, begin: str, end: str, rendered: str, what: str
) -> int:
    """Check mode: diff the committed block vs `rendered`, exit 1 on drift;
    `--write yes`: replace exactly the marked bytes of `path`."""
    if write not in ("", "yes"):
        die(f"{name}: refused — WRITE takes only the literal `yes`")
    if not path.is_file():
        die(f"{name}: refused — no {_rel(path)}")
    current = blocks.find_block(path.read_text(), begin, end)
    if current is None:
        die(f"{name}: refused — no marker pair for {what} in {_rel(path)}")
    if write == "yes":
        blocks.write_block(path, begin, end, rendered)
        print(f"{name} WROTE: {_rel(path)}, {what} block")
        return 0
    diff = blocks.diff_block(current, rendered)
    for line in diff[:40]:
        print(f"    {line}")
    verdict = "FAIL" if diff else "OK"
    print(f"{name} {verdict}: {what}, block {'differs' if diff else 'matches'}")
    return 1 if diff else 0


def simulate_cmd(profile: str, write: str = "") -> int:
    validate_name("PROFILE", profile)
    if write not in ("", "yes"):
        die("simulate: refused — WRITE takes only the literal `yes`")
    from tests.pins import SIMULATE_SEED  # the seed is a pin, not a knob

    truth = truth_dir(profile)
    tag = "" if truth.is_relative_to(FIXTURES) else " (unfrozen)"
    for fname in ("prompts.jsonl", "users.jsonl"):
        if not (truth / fname).is_file():
            die(f"simulate: refused — no {_rel(truth / fname)}")
    db = _db(profile)
    rows = simulate.arm_rows(db, truth, profile, SIMULATE_SEED)
    rendered = simulate.render_block(profile, rows)
    n = sum(rows[0][1].values())
    print(f"simulate truth: {_rel(truth)}{tag}")
    what = f"{profile}, {n} prompts, {len(simulate.ARMS)} arms"
    return _block_cmd(
        "simulate",
        write,
        RESULTS,
        simulate.BEGIN.format(profile=profile),
        simulate.END.format(profile=profile),
        rendered,
        what,
    )


def readme_cmd(write: str = "") -> int:
    """The README first-screen block (marker-confined in README.md) AND the
    findings chart docs/img/lift.svg (a wholly generated file). Check mode
    diffs both; --write yes rewrites both. Non-destructive: it only rewrites
    the same generated bytes."""
    if write not in ("", "yes"):
        die("readme: refused — WRITE takes only the literal `yes`")
    rows = readme.first_screen_rows()
    block = readme.render_block(rows)
    svg = readme.render_svg(rows)
    if not README.is_file():
        die(f"readme: refused — no {_rel(README)}")
    current = blocks.find_block(README.read_text(), readme.BEGIN, readme.END)
    if current is None:
        die(f"readme: refused — no marker pair in {_rel(README)}")
    if write == "yes":
        blocks.write_block(README, readme.BEGIN, readme.END, block)
        LIFT_SVG.parent.mkdir(parents=True, exist_ok=True)
        LIFT_SVG.write_text(svg)
        print(f"readme WROTE: {_rel(README)} first-screen block, {_rel(LIFT_SVG)}")
        return 0
    block_diff = blocks.diff_block(current, block)
    svg_current = LIFT_SVG.read_text() if LIFT_SVG.is_file() else ""
    svg_diff = blocks.diff_block(svg_current, svg)
    for line in (block_diff + svg_diff)[:40]:
        print(f"    {line}")
    ok = not block_diff and not svg_diff
    print(
        f"readme {'OK' if ok else 'FAIL'}: first-screen block "
        f"{'matches' if not block_diff else 'differs'}, lift.svg "
        f"{'matches' if not svg_diff else 'differs'}"
    )
    return 0 if ok else 1


def power_cmd(write: str = "") -> int:
    rows = power.table_rows()
    return _block_cmd(
        "power",
        write,
        AB_DESIGN,
        power.BEGIN,
        power.END,
        power.render_block(rows),
        f"{len(rows)} rows",
    )


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
    sm = sub.add_parser("simulate")
    sm.add_argument("profile")
    sm.add_argument("--write", default="")
    sub.add_parser("power").add_argument("--write", default="")
    sub.add_parser("readme").add_argument("--write", default="")
    a = ap.parse_args(argv)
    if a.cmd == "simulate":
        return simulate_cmd(a.profile, a.write)
    if a.cmd == "power":
        return power_cmd(a.write)
    if a.cmd == "readme":
        return readme_cmd(a.write)
    if a.cmd == "golden":
        return golden_cmd(a.profile, a.write)
    if a.cmd == "report":
        return report_cmd(a.profile, a.write)
    if a.cmd == "scores-golden":
        return scores_golden_cmd(a.profile, a.write)
    return score_cmd(a.profile)


if __name__ == "__main__":
    sys.exit(main())
