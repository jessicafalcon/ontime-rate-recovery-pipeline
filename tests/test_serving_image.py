"""fix/composer-cosmos: the serving+landing image ships the SERVING side only and
bakes no credential (invariant 1). A static check on the Dockerfile + its ignore
file — the build/push runs live in 7b; 7a pins the context's shape."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "orchestration" / "images" / "serving"
DOCKERFILE = IMG / "Dockerfile"
IGNORE = IMG / "Dockerfile.dockerignore"


def _directives() -> list[str]:
    """The Dockerfile's instruction lines only — comments (`# …`) stripped, since
    the header comments legitimately name credentials and truth to say they are
    EXCLUDED. The invariants are about what the image DOES, not what it documents."""
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


def _copy_targets() -> list[str]:
    """Every SOURCE path of every `COPY` in the Dockerfile — all args except the
    final destination (a COPY may list several sources into one dir); `--from=`
    build-stage copies of the uv binary are skipped."""
    out: list[str] = []
    for line in DOCKERFILE.read_text().splitlines():
        m = re.match(r"\s*COPY\s+(?!--from=)(.+)", line)
        if not m:
            continue
        args = m.group(1).split()
        out.extend(args[:-1])  # all but the destination
    return out


def test_image_context_is_serving_and_landing_only() -> None:
    """The image COPYs the serving + landing packages (and their imports), never a
    broad `COPY .`, and NEVER any `truth/` path — truth isolation for the serving
    image."""
    targets = _copy_targets()
    # the two required packages are copied
    assert "landing/" in targets
    assert "serving/" in targets
    # no broad copy of the whole tree, no fixtures-wholesale copy
    assert "." not in targets and "./" not in targets
    assert not any(t.rstrip("/") == "fixtures/tiny" for t in targets)
    # no truth anywhere in a COPY (nor named in any instruction — comments aside)
    assert not any("truth" in t for t in targets)
    assert not any("truth" in d.lower() for d in _directives())
    # only the two staged fixture subtrees are shipped (never truth/)
    assert "fixtures/tiny/raw/" in targets
    assert "fixtures/tiny/dims/" in targets
    assert not any("fixtures/tiny/truth" in t for t in targets)


def test_image_ships_no_generation_logic_or_truth_writer() -> None:
    """Invariant 1 (code-review finding): the serving image copies ONLY the
    manifest hasher from `generator/` (what the landing needs to verify
    MANIFEST.sha256), never the generation logic or the truth writer — a broad
    `COPY generator/` would ship `generator/truth.py` and `generate.py` into the
    serving layer. Assert the exact generator files copied, and that no
    generation/truth module is anywhere in the build context (a directory COPY of
    `generator/` reddens)."""
    targets = _copy_targets()
    gen = sorted(t for t in targets if t.startswith("generator"))
    assert gen == ["generator/__init__.py", "generator/manifest.py"], gen
    # a whole-directory `generator/` copy (or any generation/truth module) is banned
    banned = (
        "generator/",  # the broad directory copy the finding named
        "generate.py",
        "response.py",
        "dims.py",
        "writer.py",
        "truth.py",
        "models.py",
        "profiles.py",
    )
    for t in targets:
        assert t != "generator/", "broad COPY generator/ ships the truth writer"
        assert not any(b in t for b in banned[1:]), t


def test_image_bakes_no_credential() -> None:
    """No keyfile / ADC / GOOGLE_APPLICATION_CREDENTIALS is baked; auth is
    Workload Identity at run (Credential standard)."""
    directives = _directives()
    joined = "\n".join(directives)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in joined
    assert not re.search(
        r"COPY\s+\S*(key|credential|\.env|service-account)", joined, re.I
    )
    # no ENV/ARG smuggling a secret path
    assert "application_default_credentials" not in joined.lower()


def test_dockerignore_excludes_truth_data_and_secrets() -> None:
    """The image ignore file drops truth/, data/, .git, the venv and the secret
    globs — defence-in-depth beside the targeted COPYs."""
    lines = {ln.strip() for ln in IGNORE.read_text().splitlines() if ln.strip()}
    for required in (
        "**/truth",
        "data",
        ".git",
        ".venv",
        "**/*.tfvars",
        "**/*-key.json",
    ):
        assert required in lines, required
