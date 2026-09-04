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
    """The source paths of every `COPY` in the Dockerfile (the first arg of each,
    ignoring `--from=` build-stage copies of the uv binary)."""
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        m = re.match(r"\s*COPY\s+(?!--from=)(\S+)", line)
        if m:
            out.append(m.group(1))
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
