"""Generated blocks in committed docs (Phase 6). The bytes between a
begin/end marker pair belong to the program that renders them; everything
outside is the author's. The writer never creates a file, never appends: a
missing pair is a refusal."""

from __future__ import annotations

import difflib
from pathlib import Path


def find_block(text: str, begin: str, end: str) -> str | None:
    """The bytes between the line after `begin` and the line of `end`, or
    None when either marker is missing (or out of order)."""
    i = text.find(begin + "\n")
    j = text.find(end)
    if i < 0 or j < 0 or j < i:
        return None
    return text[i + len(begin) + 1 : j]


def replace_block(text: str, begin: str, end: str, block: str) -> str:
    """`text` with the marked bytes replaced by `block`; everything else
    byte-identical. Raises on a missing pair."""
    if find_block(text, begin, end) is None:
        raise ValueError(f"no marker pair {begin!r} … {end!r}")
    i = text.find(begin + "\n") + len(begin) + 1
    j = text.find(end)
    return text[:i] + block + text[j:]


def write_block(path: Path, begin: str, end: str, block: str) -> None:
    path.write_text(replace_block(path.read_text(), begin, end, block))


def diff_block(current: str, rendered: str) -> list[str]:
    """Unified diff of the committed block vs the freshly rendered one; empty
    when byte-identical."""
    return list(
        difflib.unified_diff(
            current.splitlines(),
            rendered.splitlines(),
            "committed",
            "rendered",
            lineterm="",
        )
    )
