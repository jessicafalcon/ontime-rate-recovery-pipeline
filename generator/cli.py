"""`make seed PROFILE=<p>` and `make freeze PROFILE=<p> CONFIRM=yes`.

seed   — validate the name, generate into data/out/<p>/, hash the output and
         compare to fixtures/<p>/MANIFEST.sha256 when one exists (exit 1 on
         drift). Never writes under fixtures/.
freeze — copy data/out/<p>/ over fixtures/<p>/ and write the manifest; only
         with CONFIRM=yes whose make origin is the command line."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from generator import manifest, profiles, truth
from generator.generate import Output, generate
from generator.writer import ROOT, write_csv, write_jsonl

DATA_OUT = ROOT / "data" / "out"
FIXTURES = ROOT / "fixtures"


def die(msg: str, code: int = 2) -> None:
    print(msg)
    sys.exit(code)


def write_output(out: Path, output: Output) -> int:
    """raw/events_<upload date>.jsonl (the Phase 7 landing unit), dims/, truth/."""
    by_day: dict[str, list] = defaultdict(list)
    for ev in output.events:  # already in arrival order
        by_day[ev.server_upload_time.strftime("%Y-%m-%d")].append(ev)
    n = 0
    for day in sorted(by_day):
        n += write_jsonl(out / "raw" / f"events_{day}.jsonl", by_day[day])
    n += write_csv(out / "dims" / "dim_user.csv", output.dims)
    n += truth.write_truth(out, output)
    return n


def seed(name: str) -> int:
    try:
        profile = profiles.load(name)
    except profiles.BadProfileName as e:
        die(f"seed: refused — {e}")
    out = DATA_OUT / name
    if out.exists():
        shutil.rmtree(out)
    output = generate(profile)
    n = write_output(out, output)
    files = manifest.compute(out)
    frozen = FIXTURES / name / manifest.NAME
    if frozen.exists():
        drift = generated_drift(out, frozen)
        if drift:
            print(
                f"seed DRIFT: {len(drift)} files differ from {frozen.relative_to(ROOT)}"
            )
            for d in drift[:20]:
                print(f"    {d}")
            return 1
        print(f"seed OK: {len(files)} files, {n} records, manifest match")
    else:
        print(f"seed OK: {len(files)} files, {n} records, no manifest to compare")
    return 0


GENERATED = ("raw/", "dims/", "truth/")  # what `seed` writes; expected/ is not


def generated_keys(m: dict[str, str]) -> dict[str, str]:
    """The manifest entries the generator is responsible for. `expected/`
    (Phase 3, written by attribution-golden) is in the manifest but not in a
    seed's output, so seed's self-check must not report it missing."""
    return {k: v for k, v in m.items() if k.startswith(GENERATED)}


def generated_drift(out: Path, frozen: Path) -> list[str]:
    have = generated_keys(manifest.compute(out))
    want = generated_keys(manifest.parse(frozen.read_text()))
    return [
        f"{k}: "
        + ("missing" if k not in have else "extra" if k not in want else "changed")
        for k in sorted(set(have) | set(want))
        if have.get(k) != want.get(k)
    ]


def missing_from_output(out: Path, frozen: Path) -> list[str]:
    """Manifest-listed files absent from data/out/<p>/ — a freeze would silently
    drop them from the fixture (e.g. expected/ after a bare `seed`)."""
    if not frozen.exists():
        return []
    have = manifest.compute(out)
    return sorted(k for k in manifest.parse(frozen.read_text()) if k not in have)


def freeze(name: str, confirm: str, origin: str) -> int:
    try:
        profiles.load(name)
    except profiles.BadProfileName as e:
        die(f"freeze: refused — {e}")
    if origin != "command line" or confirm != "yes":
        die("freeze: refused — pass CONFIRM=yes on the command line")
    src = DATA_OUT / name
    if not src.is_dir():
        die(f"freeze: refused — run `make seed PROFILE={name}` first (no {src})")
    dst = FIXTURES / name
    missing = missing_from_output(src, dst / manifest.NAME)
    if missing:
        die(
            f"freeze: refused — {src.relative_to(ROOT)} lacks {len(missing)} file(s) "
            f"the current manifest lists: {', '.join(missing)}"
        )
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    (dst / manifest.NAME).write_text(manifest.render(manifest.compute(dst)))
    print(f"freeze OK: {dst.relative_to(ROOT)} — {len(manifest.compute(dst))} files")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("profile")
    f = sub.add_parser("freeze")
    f.add_argument("profile")
    f.add_argument("--confirm", default="")
    f.add_argument("--confirm-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        return seed(a.profile)
    return freeze(a.profile, a.confirm, a.confirm_origin)


if __name__ == "__main__":
    sys.exit(main())
