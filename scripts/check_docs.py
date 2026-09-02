#!/usr/bin/env python3
"""The one docs guard. Standalone, no pytest, no services — `make check-docs`
(the CI lint job runs it too). Not a pytest file, so a docs-only edit does not
re-trigger the full suite.

Five checks (1 over the living docs, records and plans; 2 over the living docs
= CLAUDE.md + README + docs/*.md minus the plans; 3 over the source files
TRACES names; 4 over CLAUDE.md/BACKLOG.md; 5 over the tracked records):
  1. Links/anchors — every relative markdown link points at a real file inside
     the repo, and a `#anchor` resolves to a heading there (GitHub-style slug).
  2. Make targets — every `make <target>` the LIVING docs name exists in the
     Makefile (a removed target must be removed from the docs in the same PR).
     ARCHITECTURE.md, PHASES.md and PROJECT_BRIEF.md are plans — they describe
     targets not built yet by design (DECISIONS "Plans are link-checked only")
     — so they are link-checked only, like the records. A LIVING doc that names
     the next branch's target before it is built (docs/ROADMAP.md) lists the
     (doc, target) pair in FUTURE_TARGETS, an exact closed set that goes red the
     day the target lands or the citation goes.
  3. Traces — every (file, token) in TRACES exists in source as an EXACT token
     (a partial rename such as `label_accuracy` → `label_accuracy_v2` FAILS).
     Starts with the tooling's own guards; add a row when a doc cites a symbol.
  4. BACKLOG count — CLAUDE.md's "Open BACKLOG rows: **N**" equals the
     un-struck rows in BACKLOG.md (the sentence two branches always rewrite).
  5. Live identifiers — in every tracked record (RECORD_GLOBS, via
     `git ls-files`), every VALUE POSITION a project, repository or account
     identifier can occupy (VALUE_POSITIONS: `NAME=value`, `--flag value`,
     `gs://` buckets, `<x>.ontime` qualifiers, addresses) holds a placeholder
     SHAPE (DECISIONS, fix/public-release). Every set is closed and pinned;
     an allowlist of shapes, never a denylist of ids; a failure prints
     file:line and the position, never the value.
"""

from __future__ import annotations

import re
import subprocess
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
    ("scripts/check_docs.py", "FUTURE_TARGETS"),
    ("scripts/review_common.py", "resolve_spec"),
    ("scripts/mutate.py", "OPERATORS"),
    ("eval/simulate.py", "simulate:begin"),
    ("eval/power.py", "power:begin"),
    ("scripts/review_gate.py", "check_fixtures"),
    ("scripts/review_gate.py", "freeze_declarations"),
    ("generator/manifest.py", "MANIFEST.sha256"),
    ("generator/writer.py", "FixtureWriteRefused"),
    ("landing/cli.py", "validate_name"),
    ("scripts/gen_dbt_sources.py", "column_tests"),
    ("dbt/macros/to_local_time.sql", "duckdb__to_local_time"),
    ("tests/test_dbt_conventions.py", "test_exactly_five_dispatch_macros"),
    ("scripts/mutate.py", "SQL_OPERATORS"),
    ("dbt/tests/assert_cohort_day_partition.sql", "prompts_delivered"),
    ("eval/golden.py", "ONTIME_RATE_DAILY"),
    ("generator/cli.py", "missing_from_output"),
    # Phase 13 — the docs name these guards/targets/symbols by identity.
    ("eval/readme.py", "readme:begin"),
    ("eval/readme.py", "first_screen_rows"),
    ("eval/readme.py", "render_svg"),
    ("generator/response.py", "open_probability"),
    # fix/process-doc — docs/PROCESS.md names the truth guard's derived list.
    ("tests/test_truth_isolation.py", "pipeline_dirs"),
]


# Plans: allowed to name targets that do not exist yet. Link-checked only.
_PLANS = [DOCS / "ARCHITECTURE.md", DOCS / "PHASES.md", ROOT / "PROJECT_BRIEF.md"]

# (doc, target) pairs a LIVING doc may name before the target is built
# (docs/ROADMAP.md names the next branch's target by nature, and stays living so
# its real citations keep rename detection; the exemption is that doc's alone).
# An exact closed set, pinned by tests/test_check_docs.py, which is also RED in
# both stale directions — the target exists in the Makefile, or the doc no
# longer names it — so an entry lives exactly as long as its citation.
FUTURE_TARGETS: frozenset[tuple[str, str]] = frozenset(
    {("docs/ROADMAP.md", "tf-migrate-state")}
)


def _docs() -> list[Path]:
    """The living docs: CLAUDE.md, README (if present), docs/*.md minus the plans."""
    out = [CLAUDE, *(p for p in sorted(DOCS.glob("*.md")) if p not in _PLANS)]
    if README.exists():
        out.append(README)
    return out


# Link/anchor-checked only: the records name removed targets on purpose
# (history) and the plans name targets not built yet (by design). A missing
# file here is an ERROR in check_links, never a silent skip.
_LINK_ONLY = [ROOT / "DECISIONS.md", BACKLOG, *_PLANS]


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
        where = md.relative_to(ROOT)
        if not md.exists():
            errors.append(f"{where}: missing — a checked doc or record vanished")
            continue
        text = md.read_text()
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
        where = md.relative_to(ROOT).as_posix()
        for t in sorted(named_targets(text)):
            n += 1
            if t not in known and (where, t) not in FUTURE_TARGETS:
                errors.append(
                    f"{where}: names `make {t}` but the Makefile has no such target"
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


# ------------------------------------------------------ 5. live identifiers

# INVARIANT: in every tracked record, every VALUE POSITION a project, repository
# or account identifier can occupy holds a placeholder. Both halves are closed
# sets pinned exactly by tests/test_check_docs.py — the records scanned, the
# positions inspected, the shapes accepted. An ALLOWLIST of shapes, never a
# denylist of ids (a guard that named the live id would re-publish it); a
# failure prints file:line and the position's NAME, never the value (CI logs
# are public). A bare id in prose has no position and is the reviewer's check
# (.claude/agents/security-reviewer.md).

# The tracked files a runbook line can land in — `git ls-files` pathspecs.
RECORD_GLOBS: tuple[str, ...] = (
    "*.md",
    "Makefile",
    ".github/workflows/*.yml",
    "orchestration/*.yml",
    "dbt/profiles.yml",
    "infra/*.example",
)
# The names whose `NAME=value` / `name = value` carries a project, repo or principal.
ARG_NAMES: tuple[str, ...] = (
    "PROJECT",
    "PROJECT_ID",
    "OTR_DAG_PROJECT",
    "OTR_GCP_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUDSDK_CORE_PROJECT",
    "project_id",
    "github_repository",
    "operator_principal",
)
# The flags whose `--flag=value` / `--flag value` does the same.
FLAG_NAMES: tuple[str, ...] = (
    "project",
    "project_id",
    "billing-account",
    "impersonate-service-account",
)
_VALUE = r"([^\s`]+)"
# (position name, pattern with the value as group 1). Closed; pinned exactly.
VALUE_POSITIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("argument", re.compile(r"\b(?:" + "|".join(ARG_NAMES) + r")(?:=|\s=\s)" + _VALUE)),
    ("flag", re.compile(r"--(?:" + "|".join(FLAG_NAMES) + r")(?:=|\s+)" + _VALUE)),
    ("bucket", re.compile(r"gs://([^\s`/]+)")),
    ("dataset qualifier", re.compile(r"([^\s`(]+)\.ontime\b")),
    # Word characters on BOTH sides of the `@`: `<operator>` and
    # `ontime-pipeline@<project_id>.iam…` never match; a live address does.
    ("address", re.compile(r"([\w.+-]+@\w[\w-]*\.[\w.-]+)")),
)
# The accepted VALUE shapes, tested after one leading quote is dropped: a
# `<placeholder>`, an ellipsis, a shell/make/Jinja expansion, Terraform's
# `null`, a `your-…` example value, an address at an RFC 2606 example domain;
# `user:` may prefix any of them (an IAM principal).
_PLACEHOLDER = re.compile(
    r"^(?:user:)?(?:<|…|\$|\{\{|null\b|your-|[\w.+-]+@example\.(?:com|net|org)\b)"
)


def placeholder_shaped(value: str) -> bool:
    """A value is a placeholder by SHAPE; one leading quote does not change that
    (`PROJECT='my-live-project'` is as live as the unquoted form)."""
    if value[:1] in ("'", '"'):
        value = value[1:]
    return bool(_PLACEHOLDER.match(value))


def records() -> list[Path]:
    """The tracked files RECORD_GLOBS select — the index, not the working tree,
    so an untracked scratch note is neither scanned nor publishable."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *RECORD_GLOBS],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return sorted(ROOT / rel for rel in out.decode().split("\0") if rel)


def check_live_identifiers(errors: list[str]) -> int:
    files = records()
    for md in files:
        rel = md.relative_to(ROOT)
        for i, line in enumerate(md.read_text().splitlines(), 1):
            for position, pattern in VALUE_POSITIONS:
                for m in pattern.finditer(line):
                    if not placeholder_shaped(m.group(1)):
                        errors.append(
                            f"live identifier: {rel}:{i} — a {position} value is "
                            "not placeholder-shaped (`<…>`)"
                        )
    return len(files)


def main() -> int:
    errors: list[str] = []
    counts = {
        "links": check_links(errors),
        "make targets": check_make_targets(errors),
        "traces": check_traces(errors),
        "backlog count": check_backlog_count(errors),
        "records": check_live_identifiers(errors),
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
