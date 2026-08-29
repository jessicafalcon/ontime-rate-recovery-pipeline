"""`MANIFEST.sha256`: sha256sum-compatible (`<hex>  <path>`), paths sorted and
relative to the fixture root, the manifest itself excluded."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

NAME = "MANIFEST.sha256"


def compute_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


Select = Callable[[Path], bool]


def compute(root: Path, select: Select | None = None) -> dict[str, str]:
    """Every file under root (the manifest itself excluded), or only those
    `select` admits (the infra tree pins `.tf`/`.tf.json` + the lock)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != NAME and (select is None or select(p)):
            out[p.relative_to(root).as_posix()] = compute_file(p)
    return out


def render(m: dict[str, str]) -> str:
    return "".join(f"{m[k]}  {k}\n" for k in sorted(m))


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in text.splitlines():
        if ln.strip():
            digest, _, path = ln.partition("  ")
            out[path] = digest
    return out


def matches(root: Path, manifest_path: Path) -> bool:
    return compute(root) == parse(manifest_path.read_text())


def diff(root: Path, manifest_path: Path, select: Select | None = None) -> list[str]:
    """`<path>: missing|extra|changed` per drifted entry; empty on a match."""
    have, want = compute(root, select), parse(manifest_path.read_text())
    out = []
    for k in sorted(set(have) | set(want)):
        if have.get(k) != want.get(k):
            state = (
                "missing" if k not in have else "extra" if k not in want else "changed"
            )
            out.append(f"{k}: {state}")
    return out
