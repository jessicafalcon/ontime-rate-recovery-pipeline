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
