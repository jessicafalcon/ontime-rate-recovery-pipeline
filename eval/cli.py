"""`make attribution-golden PROFILE=<p> [WRITE=yes]` and `make eval PROFILE=<p>`.

golden — diff the built attribution table (data/<p>.duckdb) against
         fixtures/<p>/expected/attribution.csv; exit 1 on any differing row.
         With --write yes: write data/out/<p>/expected/attribution.csv instead
         (never fixtures/ — `make freeze` is the only writer there).
score  — label accuracy vs fixtures/<p>/truth/prompts.jsonl; exit 1 below the
         pin (tests/pins.py::LABEL_ACCURACY)."""

from __future__ import annotations

import argparse
import sys

from eval import golden, score
from loader import load as loader
from loader.cli import die, validate_name

DATA_OUT = loader.ROOT / "data" / "out"
FIXTURES = loader.ROOT / "fixtures"
EXPECTED = "expected/attribution.csv"


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


def golden_cmd(profile: str, write: str = "") -> int:
    validate_name("PROFILE", profile)
    if write not in ("", "yes"):
        die("attribution-golden: refused — WRITE takes only the literal `yes`")
    rows = golden.export_rows(_db(profile))
    if write == "yes":
        out = DATA_OUT / profile / EXPECTED
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(golden.render(rows))
        print(f"attribution-golden WROTE: {_rel(out)}, {len(rows)} rows")
        return 0
    frozen_path = FIXTURES / profile / EXPECTED
    if not frozen_path.is_file():
        rel = frozen_path.relative_to(loader.ROOT)
        die(f"attribution-golden: refused — no {rel} (WRITE=yes, then `make freeze`)")
    diff = golden.diff_rows(rows, golden.parse(frozen_path.read_text()))
    for line in diff[:20]:
        print(f"    {line}")
    verdict = "FAIL" if diff else "OK"
    print(
        f"attribution-golden {verdict}: {profile}, {len(rows)} rows, {len(diff)} differ"
    )
    return 1 if diff else 0


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
    a = ap.parse_args(argv)
    if a.cmd == "golden":
        return golden_cmd(a.profile, a.write)
    return score_cmd(a.profile)


if __name__ == "__main__":
    sys.exit(main())
