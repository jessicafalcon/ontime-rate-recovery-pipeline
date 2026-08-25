"""The pipeline NEVER reads truth (CLAUDE.md determinism policy).

Structural guard: no pipeline directory may even mention truth or the truth
path. Pipeline directories are DERIVED from the tree — every top-level package
not in EXEMPT — so a new package is guarded the day it appears (review round 1:
a hand-maintained list was vacuously green on day one). Inside `generator/` only
the writer (`truth.py`), the record types (`models.py`) and the entry point that
invokes the writer (`cli.py`) may name it; generation logic never does. `eval/`
and tests may.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SOURCE_SUFFIXES = {".py", ".sql", ".yml", ".yaml"}
# Not pipeline code: tooling, docs, plans, fixtures, infra-as-text, the two
# sanctioned readers/writers, and every dot-directory.
EXEMPT = {
    "tests",
    "scripts",
    "eval",
    "generator",
    "docs",
    "specs",
    "fixtures",
    "infra",
    "data",
}


def pipeline_dirs(root: Path) -> list[str]:
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in EXEMPT
    )


def offenders(root: Path, dirname: str) -> list[str]:
    return sorted(
        str(path.relative_to(root))
        for path in (root / dirname).rglob("*")
        if path.is_file()  # dbt/target/ compiles schema.yml into a DIRECTORY
        and path.suffix in SOURCE_SUFFIXES
        and "truth" in path.read_text().lower()
    )


def test_pipeline_dirs_never_mention_truth() -> None:
    hits = [o for d in pipeline_dirs(REPO_ROOT) for o in offenders(REPO_ROOT, d)]
    assert not hits, f"pipeline code references truth: {hits}"


def test_pipeline_dirs_are_derived_from_the_tree(tmp_path: Path) -> None:
    for d in ("dbt", "serving", "tests", "eval", ".claude", "newpkg"):
        (tmp_path / d).mkdir()
    (tmp_path / "file.py").write_text("")
    assert pipeline_dirs(tmp_path) == ["dbt", "newpkg", "serving"]


def test_a_planted_truth_reference_is_found(tmp_path: Path) -> None:
    """Positive control: the grep itself finds a reference (and reports sorted)."""
    (tmp_path / "dbt" / "models").mkdir(parents=True)
    (tmp_path / "dbt" / "models" / "z.sql").write_text("select * from truth_users")
    (tmp_path / "dbt" / "models" / "a.yml").write_text("source: TRUTH")
    (tmp_path / "dbt" / "models" / "ok.sql").write_text("select 1")
    (tmp_path / "dbt" / "notes.md").write_text("truth is fine in prose")
    assert offenders(tmp_path, "dbt") == ["dbt/models/a.yml", "dbt/models/z.sql"]


GENERATOR_MAY_NAME_TRUTH = {"truth", "models", "cli"}


def generator_offenders(root: Path) -> list[str]:
    gen = root / "generator"
    return sorted(
        str(p.relative_to(root))
        for p in gen.rglob("*.py")
        if "truth" in p.read_text().lower() and p.stem not in GENERATOR_MAY_NAME_TRUTH
    )


def test_generator_truth_writer_is_confined() -> None:
    """The generator may name truth only in truth.py (writer), models.py (record
    types) and cli.py (the entry point that calls the writer)."""
    assert (REPO_ROOT / "generator" / "truth.py").is_file()  # no longer vacuous
    hits = generator_offenders(REPO_ROOT)
    assert not hits, f"generator references truth outside the sanctioned files: {hits}"


def test_generator_confinement_is_not_vacuous(tmp_path: Path) -> None:
    """Positive control: a planted reference in generation code is found."""
    (tmp_path / "generator").mkdir()
    (tmp_path / "generator" / "generate.py").write_text("x = load_truth()\n")
    (tmp_path / "generator" / "truth.py").write_text("TRUTH = 1\n")
    (tmp_path / "generator" / "response.py").write_text("y = 2\n")
    assert generator_offenders(tmp_path) == ["generator/generate.py"]
