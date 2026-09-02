"""Pins for the docs guard (scripts/check_docs.py). Offline, no services.
Runs the trace / target / count checks under `make test` on purpose, so a
code change that breaks a doc citation fails here, not only in the lint job."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

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


# ---- check 5: live identifiers (fix/public-release) — every set pinned exactly


def test_no_live_identifier_in_any_record_today() -> None:
    errors: list[str] = []
    check_docs.check_live_identifiers(errors)
    assert errors == []


def test_record_scope_is_the_index_over_the_pinned_globs() -> None:
    """The records are what `git ls-files` returns for RECORD_GLOBS — the
    runbook surfaces (markdown, Makefile, CI, compose, the dbt profile, the
    tfvars example) and never a code file or an untracked scratch note."""
    assert check_docs.RECORD_GLOBS == (
        "*.md",
        "Makefile",
        ".github/workflows/*.yml",
        "orchestration/*.yml",
        "dbt/profiles.yml",
        "infra/*.example",
    )
    rels = {p.relative_to(check_docs.ROOT).as_posix() for p in check_docs.records()}
    for must in (
        "CLAUDE.md",
        "README.md",
        "BACKLOG.md",
        "DECISIONS.md",
        "docs/DEPLOYMENT.md",
        "docs/RESULTS.md",
        "specs/phase-12-live-run.md",
        ".claude/agents/security-reviewer.md",
        "Makefile",
        ".github/workflows/ci.yml",
        "orchestration/docker-compose.cloud.yml",
        "dbt/profiles.yml",
        "infra/terraform.tfvars.example",
    ):
        assert must in rels, must
    assert not any(r.endswith((".py", ".sql", ".tf")) for r in rels)
    tracked = subprocess.run(
        ["git", "ls-files", "--", *check_docs.RECORD_GLOBS],
        cwd=check_docs.ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert sorted(rels) == sorted(tracked)


def test_value_positions_and_names_are_pinned_exactly() -> None:
    assert check_docs.ARG_NAMES == (
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
    assert check_docs.FLAG_NAMES == (
        "project",
        "project_id",
        "billing-account",
        "impersonate-service-account",
    )
    assert [name for name, _ in check_docs.VALUE_POSITIONS] == [
        "argument",
        "flag",
        "bucket",
        "dataset qualifier",
        "address",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "<id>",
        "<project_id>",
        "'<project_id>'",
        '"<owner>/<repo>"',
        "…",
        "'…'",
        "$(PROJECT)",
        "${OTR_DAG_PROJECT}",
        "{{ var('x') }}",
        "null",
        "user:<you>",
        '"user:you@example.com"',
        '"your-org/your-repo"',
        "you@example.com",
    ],
)
def test_placeholder_shapes_accepted(value: str) -> None:
    assert check_docs.placeholder_shaped(value)


@pytest.mark.parametrize(
    "value",
    [
        "my-live-project",
        "'my-live-project'",
        '"my-live-project"',
        "owner/repo",
        "user:someone@gmail.com",
        "someone@example.co",  # not an RFC 2606 domain
        "nullable-project",
        "yours-truly",
    ],
)
def test_live_shapes_refused(value: str) -> None:
    assert not check_docs.placeholder_shaped(value)


def _git_tree(tmp_path: Path, monkeypatch, files: dict[str, str]) -> None:
    """A scratch REPOSITORY (records() reads the index), with the given files
    tracked and one untracked scratch note that must not be scanned."""
    _tree(tmp_path, monkeypatch, files)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    (tmp_path / "SCRATCH.md").write_text("someone@example.co PROJECT=live-one\n")


def test_check_live_identifiers_reports_each_position_never_the_value(
    tmp_path, monkeypatch
) -> None:
    """One red line per live value position — argument (quoted too), spaced
    flag, bucket, dataset qualifier, address — the file:line and position named,
    the value never echoed; every placeholder shape green; the untracked
    scratch note and a code file outside RECORD_GLOBS not scanned."""
    _git_tree(
        tmp_path,
        monkeypatch,
        {
            "docs/X.md": (
                "`make tf-plan PROJECT=my-real-project`\n"
                "`PROJECT='my-real-project'`\n"
                "`gcloud storage ls --project my-real-project`\n"
                "`gs://my-real-project-ontime/landing/`\n"
                "`writeback OK: my-real-project.ontime → spanner`\n"
                "operator `someone@gmail.com`\n"
                "`-var github_repository=owner/repo`\n"
            ),
            "docs/OK.md": (
                "operator `<operator>`; `PROJECT=<id>` `--project=<project_id>` "
                "`--project $(PROJECT)` `OTR_DAG_PROJECT=<project_id>` `project_id=…` "
                "`PROJECT='…'` `github_repository=<owner>/<repo>` "
                '`github_repository = "your-org/your-repo"` '
                "`operator_principal = null` "
                "`operator_principal=user:<you>'` `gs://<project_id>-ontime` "
                "`writeback OK: <project_id>.ontime → spanner` `PROJECT= CONFIRM=` "
                "`ontime-pipeline@<project_id>.iam.gserviceaccount.com` "
                "`service-<number>@gcp-sa-bigqueryconnection.iam.gserviceaccount.com` "
                "`--impersonate-service-account=<sa>` `--billing-account=<acct>`\n"
            ),
            "infra/terraform.tfvars.example": (
                'project_id = "your-gcp-project-id"\n'
                '# operator_principal = "user:you@example.com"\n'
            ),
            "landing/cli.py": "PROJECT = 'my-real-project'  # code, not a record\n",
        },
    )
    errors: list[str] = []
    n = check_docs.check_live_identifiers(errors)
    assert n == 3  # X.md, OK.md, the tfvars example — not SCRATCH.md, not cli.py
    positions = [
        (e.split(":")[2].split(" ")[0], e.split(" a ")[1].split(" value")[0])
        for e in errors
    ]
    assert positions == [
        ("1", "argument"),
        ("2", "argument"),
        ("3", "flag"),
        ("4", "bucket"),
        ("5", "dataset qualifier"),
        ("6", "address"),
        ("7", "argument"),
    ], errors
    assert all(e.startswith("live identifier: docs/X.md:") for e in errors)
    for secret in ("my-real-project", "gmail", "owner/repo", "live-one"):
        assert not any(secret in e for e in errors), secret
