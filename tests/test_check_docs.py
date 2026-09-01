"""Pins for the docs guard (scripts/check_docs.py). Offline, no services.
Runs the trace / target / count checks under `make test` on purpose, so a
code change that breaks a doc citation fails here, not only in the lint job."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_docs", Path(__file__).parent.parent / "scripts" / "check_docs.py"
)
check_docs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_docs)


def test_partial_rename_is_a_failure() -> None:
    renamed = "def label_accuracy_v2(rows):\n    ...\n"
    assert "label_accuracy" in renamed  # a substring check would have passed
    assert not check_docs.token_present("label_accuracy", renamed)


def test_exact_token_still_matches() -> None:
    src = "from eval.score import label_accuracy, mae\nlabel_accuracy(rows)\n"
    assert check_docs.token_present("label_accuracy", src)
    assert not check_docs.token_present("label_accurac", src)


def test_every_trace_resolves_today() -> None:
    errors: list[str] = []
    check_docs.check_traces(errors)
    assert errors == []


def test_every_named_make_target_exists_today() -> None:
    errors: list[str] = []
    check_docs.check_make_targets(errors)
    assert errors == []


def test_plans_set_is_exact_and_every_plan_exists() -> None:
    """The plans — link-checked only, free to name targets not built yet — are
    exactly these three, each on disk (a vanished plan would drop silently from
    `_docs()` and `_LINK_ONLY`); a living doc joining them is a visible edit."""
    assert [p.relative_to(check_docs.ROOT).as_posix() for p in check_docs._PLANS] == [
        "docs/ARCHITECTURE.md",
        "docs/PHASES.md",
        "PROJECT_BRIEF.md",
    ]
    assert all(p.exists() for p in check_docs._PLANS)


def test_plans_are_link_checked() -> None:
    """Every plan is in the link-checked set — dropping the `_PLANS` splat from
    `_LINK_ONLY` left the suite green (functionality-tester, fix/roadmap)."""
    assert all(p in check_docs._LINK_ONLY for p in check_docs._PLANS)


def test_future_targets_set_is_exact_and_every_pair_is_live() -> None:
    """The (doc, target) pairs a living doc may name before the target exists:
    exactly this set, and RED in both stale directions — the target landed in
    the Makefile, or the doc no longer names it — so an entry lives exactly as
    long as its citation (round 3: the one-sided pin let a dead entry linger)."""
    assert check_docs.FUTURE_TARGETS == frozenset(
        {("docs/ROADMAP.md", "tf-migrate-state")}
    )
    built = check_docs.make_targets(check_docs.ROOT)
    living = {p.relative_to(check_docs.ROOT).as_posix(): p for p in check_docs._docs()}
    for doc, target in check_docs.FUTURE_TARGETS:
        assert target not in built, f"{target} is built: remove the entry"
        assert doc in living, f"{doc} is not a living doc"
        named = check_docs.named_targets(check_docs._living(living[doc].read_text()))
        assert target in named, f"{doc} no longer names `make {target}`: remove it"


def test_backticked_link_text_is_still_a_link() -> None:
    assert check_docs._links("see [`docs/X.md`](docs/X.md#a) now") == ["docs/X.md#a"]
    assert check_docs._links("[plain](docs/X.md)") == ["docs/X.md"]
    assert check_docs._links("a `f[8](x)` span") == []
    assert check_docs._links("[ext](https://x.y) [same](#anchor)") == []


def test_link_outside_the_repo_is_rejected() -> None:
    root = check_docs.ROOT
    assert not check_docs._inside_root(
        "../../../outside.md", (root / "docs" / "../../../outside.md").resolve()
    )
    assert not check_docs._inside_root("/etc/passwd", Path("/etc/passwd"))
    assert check_docs._inside_root(
        "../CLAUDE.md", (root / "docs" / "../CLAUDE.md").resolve()
    )


def test_heading_inside_a_fence_is_not_an_anchor(tmp_path) -> None:
    md = tmp_path / "x.md"
    md.write_text("## Real\n\n```bash\n## not a heading\n# comment\n```\n")
    assert check_docs._anchors(md) == {"real"}


def test_historical_mentions_are_skipped_by_the_target_scan() -> None:
    text = (
        "| ~~`make gone-target`~~ DONE |\n"
        "row `make also-gone` <!-- historical -->\n"
        "live `make still-here`\n"
    )
    living = check_docs._living(text)
    assert "gone-target" not in living
    assert "also-gone" not in living
    assert "still-here" in living


def test_open_backlog_rows_counts_only_unstruck_bold_rows() -> None:
    text = "| **open** | s | t |\n| ~~**done**~~ DONE | s | t |\n| Item | Source |\n"
    assert check_docs.open_backlog_rows(text) == 1


def test_backlog_count_matches_today() -> None:
    errors: list[str] = []
    check_docs.check_backlog_count(errors)
    assert errors == []


# ---- negative pins (review round 1: every check could be disabled unnoticed)


def _tree(tmp_path: Path, monkeypatch, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    monkeypatch.setattr(check_docs, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(check_docs, "README", tmp_path / "README.md")
    monkeypatch.setattr(check_docs, "CLAUDE", tmp_path / "CLAUDE.md")
    monkeypatch.setattr(check_docs, "BACKLOG", tmp_path / "BACKLOG.md")
    monkeypatch.setattr(check_docs, "_PLANS", [])
    monkeypatch.setattr(check_docs, "_LINK_ONLY", [])


def test_check_links_reports_a_broken_link_and_anchor(tmp_path, monkeypatch) -> None:
    _tree(
        tmp_path,
        monkeypatch,
        {
            "CLAUDE.md": (
                "[a](docs/X.md) [b](docs/gone.md) [c](docs/X.md#nope) [d](#zzz)\n"
            ),
            "docs/X.md": "## Real\n",
        },
    )
    errors: list[str] = []
    check_docs.check_links(errors)
    assert any("broken link" in e and "gone.md" in e for e in errors)
    assert any("broken anchor" in e and "#nope" in e for e in errors)
    assert any("broken anchor #zzz" in e for e in errors)
    assert len(errors) == 3


def test_future_target_exemption_is_the_named_doc_alone(tmp_path, monkeypatch) -> None:
    _tree(
        tmp_path,
        monkeypatch,
        {
            "CLAUDE.md": "run `make real`, `make planned` and `make nope`\n",
            "README.md": "also `make planned`\n",
            "Makefile": "real:\n\techo\n",
        },
    )
    monkeypatch.setattr(
        check_docs, "FUTURE_TARGETS", frozenset({("CLAUDE.md", "planned")})
    )
    errors: list[str] = []
    check_docs.check_make_targets(errors)
    assert sorted(errors) == [
        "CLAUDE.md: names `make nope` but the Makefile has no such target",
        "README.md: names `make planned` but the Makefile has no such target",
    ]  # the pair admits planned in CLAUDE.md only; README's citation still fails


def test_check_links_reports_a_vanished_record(tmp_path, monkeypatch) -> None:
    _tree(tmp_path, monkeypatch, {"CLAUDE.md": "no links\n"})
    monkeypatch.setattr(check_docs, "_LINK_ONLY", [tmp_path / "GONE.md"])
    errors: list[str] = []
    check_docs.check_links(errors)
    assert errors == ["GONE.md: missing — a checked doc or record vanished"]


def test_check_make_targets_reports_an_unknown_target(tmp_path, monkeypatch) -> None:
    _tree(
        tmp_path,
        monkeypatch,
        {
            "CLAUDE.md": "run `make real` then `make nope`; make sure it works\n"
            "```\nmake fenced-gone\n```\n",
            "Makefile": "real:\n\techo\n",
        },
    )
    errors: list[str] = []
    check_docs.check_make_targets(errors)
    assert sorted(errors) == sorted(
        [
            "CLAUDE.md: names `make nope` but the Makefile has no such target",
            "CLAUDE.md: names `make fenced-gone` but the Makefile has no such target",
        ]
    )  # prose "make sure" is not a target


def test_check_traces_reports_a_renamed_token(tmp_path, monkeypatch) -> None:
    _tree(tmp_path, monkeypatch, {"x.py": "def label_accuracy_v2():\n    pass\n"})
    monkeypatch.setattr(
        check_docs, "TRACES", [("x.py", "label_accuracy"), ("missing.py", "f")]
    )
    errors: list[str] = []
    check_docs.check_traces(errors)
    assert len(errors) == 2
    assert "no longer contains the token 'label_accuracy'" in errors[0]
    assert "does not exist" in errors[1]


def test_check_backlog_count_reports_a_mismatch(tmp_path, monkeypatch) -> None:
    _tree(
        tmp_path,
        monkeypatch,
        {
            "CLAUDE.md": "Open BACKLOG rows: **3**.\n",
            "BACKLOG.md": "| **one** | s | t |\n| ~~**done**~~ | s | t |\n",
        },
    )
    errors: list[str] = []
    check_docs.check_backlog_count(errors)
    assert errors == [
        "CLAUDE.md says Open BACKLOG rows: **3** but BACKLOG.md has 1 un-struck rows"
    ]


def test_absolute_link_inside_the_repo_is_still_rejected() -> None:
    root = check_docs.ROOT
    inside = root / "CLAUDE.md"
    assert not check_docs._inside_root(str(inside), inside)
