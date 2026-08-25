"""The pipeline NEVER reads truth (CLAUDE.md determinism policy).

Structural guard: no pipeline directory may even mention truth or the truth
path. Only the generator (which writes it), `eval/` (which scores against it),
and tests may. Every new top-level package on the pipeline path is added to
PIPELINE_DIRS in the phase that creates it — the predecessor project shipped
two packages without the guard and caught it only at a review gate.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PIPELINE_DIRS = ["dbt", "serving", "orchestration"]
SOURCE_SUFFIXES = {".py", ".sql", ".yml", ".yaml"}


def _offenders(dirname: str) -> list[str]:
    out = []
    for path in (REPO_ROOT / dirname).rglob("*"):
        if path.suffix in SOURCE_SUFFIXES and "truth" in path.read_text().lower():
            out.append(str(path.relative_to(REPO_ROOT)))
    return out


def test_pipeline_dirs_never_mention_truth() -> None:
    offenders = [
        o for d in PIPELINE_DIRS if (REPO_ROOT / d).exists() for o in _offenders(d)
    ]
    assert not offenders, f"pipeline code references truth: {offenders}"


def test_generator_truth_writer_is_confined() -> None:
    """The generator may write truth only from a module named for it."""
    gen = REPO_ROOT / "generator"
    if not gen.exists():
        return
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in gen.rglob("*.py")
        if "truth" in p.read_text().lower() and p.stem not in {"truth", "models"}
    ]
    assert not offenders, (
        f"generator references truth outside truth.py/models.py: {offenders}"
    )
