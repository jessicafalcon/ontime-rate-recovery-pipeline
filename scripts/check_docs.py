#!/usr/bin/env python3
"""The one docs guard. Standalone, no pytest, no services — `make check-docs`
(the CI lint job runs it too). Not a pytest file, so a docs-only edit does not
re-trigger the full suite.

Four checks (1 over the living docs, records and plans; 2-3 over the living docs
= CLAUDE.md + README + docs/*.md minus the plans; 4 over CLAUDE.md/BACKLOG.md):
  1. Links/anchors — every relative markdown link points at a real file inside
     the repo, and a `#anchor` resolves to a heading there (GitHub-style slug).
  2. Make targets — every `make <target>` the LIVING docs name exists in the
     Makefile (a removed target must be removed from the docs in the same PR).
     ARCHITECTURE.md, PHASES.md and PROJECT_BRIEF.md are plans — they describe
     targets not built yet by design (DECISIONS "Plans are link-checked only")
     — so they are link-checked only, like the records.
  3. Traces — every (file, token) in TRACES exists in source as an EXACT token
     (a partial rename such as `label_accuracy` → `label_accuracy_v2` FAILS).
     Starts with the tooling's own guards; add a row when a doc cites a symbol.
  4. BACKLOG count — CLAUDE.md's "Open BACKLOG rows: **N**" equals the
     un-struck rows in BACKLOG.md (the sentence two branches always rewrite).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_common import make_targets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
README = ROOT / "README.md"
CLAUDE = ROOT / "CLAUDE.md"
BACKLOG = ROOT / "BACKLOG.md"

_LINK = re.compile(r"\[[^\]]*\]\((?!https?://)(?!#)([^)]+)\)")
# A target is NAMED only inside backticks or on a fenced-block command line —
# prose "make sure" is not a target (review round 1: the bare `\bmake` form).
_MAKE_TICK = re.compile(r"`make ([a-z][a-z0-9-]*)[^`]*`")
_MAKE_FENCE_LINE = re.compile(r"^\s*make ([a-z][a-z0-9-]*)", re.M)
_FENCE = re.compile(r"```.*?```", re.S)
_TICK = re.compile(r"`([^`\n]*)`")

# (file relative to repo root, token that must be present) — guards, targets and
# source phrases the docs name by identity. Add a row when a doc cites a symbol.
TRACES: list[tuple[str, str]] = [
    ("Makefile", "unexport SPEC BASE DELETED CONFIRM PROFILE TARGET WRITE"),
    ("scripts/review_common.py", "resolve_spec"),
    ("scripts/mutate.py", "OPERATORS"),
    ("eval/simulate.py", "simulate:begin"),
    ("eval/power.py", "power:begin"),
    ("scripts/review_gate.py", "check_fixtures"),
    ("scripts/review_gate.py", "freeze_declarations"),
    ("generator/manifest.py", "MANIFEST.sha256"),
    ("generator/writer.py", "FixtureWriteRefused"),
    ("loader/cli.py", "validate_name"),
    ("scripts/gen_dbt_sources.py", "column_tests"),
    ("dbt/macros/to_local_time.sql", "duckdb__to_local_time"),
    ("tests/test_dbt_conventions.py", "test_exactly_five_dispatch_macros"),
    ("scripts/mutate.py", "SQL_OPERATORS"),
    ("dbt/tests/assert_cohort_day_partition.sql", "prompts_delivered"),
    ("eval/golden.py", "ONTIME_RATE_DAILY"),
    ("generator/cli.py", "missing_from_output"),
]


# Plans: allowed to name targets that do not exist yet. Link-checked only.
_PLANS = [DOCS / "ARCHITECTURE.md", DOCS / "PHASES.md", ROOT / "PROJECT_BRIEF.md"]


def _docs() -> list[Path]:
    """The living docs: CLAUDE.md, README (if present), docs/*.md minus the plans."""
    out = [CLAUDE, *(p for p in sorted(DOCS.glob("*.md")) if p not in _PLANS)]
    if README.exists():
        out.append(README)
    return out


# Link/anchor-checked only: the records name removed targets on purpose
# (history) and the plans name targets not built yet (by design).
_LINK_ONLY = [ROOT / "DECISIONS.md", BACKLOG, *(p for p in _PLANS if p.exists())]


def _slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def _anchors(md: Path) -> set[str]:
    slugs: set[str] = set()
    for line in _FENCE.sub("", md.read_text()).splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            slugs.add(_slug(m.group(1)))
    return slugs


# ---------------------------------------------------------------- 1. links


def _inside_root(path_part: str, target: Path) -> bool:
    return not Path(path_part).is_absolute() and (
        target == ROOT or ROOT in target.parents
    )


def _links(text: str) -> list[str]:
    return _LINK.findall(_TICK.sub("", _FENCE.sub("", text)))


def check_links(errors: list[str]) -> int:
    n = 0
    for md in [*_docs(), *_LINK_ONLY]:
        if not md.exists():
            continue
        text = md.read_text()
        where = md.relative_to(ROOT)
        for raw in _links(text):
            n += 1
            path_part, _, anchor = raw.partition("#")
            target = (md.parent / path_part).resolve()
            if not _inside_root(path_part, target):
                errors.append(f"{where}: link escapes the repo: {raw}")
                continue
            if not target.exists():
                errors.append(f"{where}: broken link {raw} -> missing file {path_part}")
                continue
            if anchor and target.suffix == ".md" and anchor not in _anchors(target):
                errors.append(
                    f"{where}: broken anchor {raw} -> no heading #{anchor} "
                    f"in {path_part}"
                )
        slugs = _anchors(md)
        for anchor in re.findall(r"\]\(#([^)]+)\)", text):
            n += 1
            if anchor not in slugs:
                errors.append(f"{where}: broken anchor #{anchor}")
    return n


# --------------------------------------------------------- 2. make targets


def _living(text: str) -> str:
    """Drop struck-through and `<!-- historical -->` lines: history may name a
    removed target on purpose."""
    return "\n".join(
        ln
        for ln in text.splitlines()
        if "~~" not in ln and "<!-- historical -->" not in ln
    )


def named_targets(text: str) -> set[str]:
    """`make <target>` named as a command in `text`: backticked, or a command line
    inside a fenced block. Prose is never a target."""
    found: set[str] = set(_MAKE_TICK.findall(_FENCE.sub("", text)))
    for block in _FENCE.findall(text):
        found.update(_MAKE_FENCE_LINE.findall(block))
    return found


def check_make_targets(errors: list[str]) -> int:
    n = 0
    known = make_targets(ROOT)
    for md in _docs():
        text = _living(md.read_text())
        for t in sorted(named_targets(text)):
            n += 1
            if t not in known:
                errors.append(
                    f"{md.relative_to(ROOT)}: names `make {t}` but the Makefile "
                    "has no such target"
                )
    return n


# ------------------------------------------------------------- 3. traces


def token_present(needle: str, haystack: str) -> bool:
    """Exact-token match: `needle` must not continue into an identifier on
    either side, so `label_accuracy` does NOT match `label_accuracy_v2`."""
    pattern = r"(?<![\w])" + re.escape(needle) + r"(?![\w])"
    return re.search(pattern, haystack) is not None


def check_traces(errors: list[str]) -> int:
    n = 0
    for rel, needle in TRACES:
        n += 1
        path = ROOT / rel
        if not path.exists():
            errors.append(f"trace: {rel} does not exist (needle {needle!r})")
        elif not token_present(needle, path.read_text()):
            errors.append(f"trace: {rel} no longer contains the token {needle!r}")
    return n


# ----------------------------------------------------------- 4. backlog count


def open_backlog_rows(text: str) -> int:
    """Un-struck rows: table lines whose first cell starts with bold, not `~~`."""
    return sum(1 for ln in text.splitlines() if re.match(r"^\| \*\*", ln))


def check_backlog_count(errors: list[str]) -> int:
    m = re.search(r"Open BACKLOG rows: \*\*(\d+)\*\*", CLAUDE.read_text())
    if not m:
        errors.append('CLAUDE.md: no "Open BACKLOG rows: **N**" sentence')
        return 1
    claimed = int(m.group(1))
    actual = open_backlog_rows(BACKLOG.read_text())
    if claimed != actual:
        errors.append(
            f"CLAUDE.md says Open BACKLOG rows: **{claimed}** but BACKLOG.md has "
            f"{actual} un-struck rows"
        )
    return 1


def main() -> int:
    errors: list[str] = []
    counts = {
        "links": check_links(errors),
        "make targets": check_make_targets(errors),
        "traces": check_traces(errors),
        "backlog count": check_backlog_count(errors),
    }
    for e in errors:
        print(f"FAIL {e}")
    summary = ", ".join(f"{v} {k}" for k, v in counts.items())
    print(f"check-docs {'FAIL' if errors else 'OK'}: {summary}")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # one line, never a traceback
        print(f"check-docs error: {type(e).__name__}: {e}")
        sys.exit(1)
