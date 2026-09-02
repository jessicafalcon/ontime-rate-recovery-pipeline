"""Canonical serialization. Refuses to write under `fixtures/` — `freeze` is
the only writer there."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


class FixtureWriteRefused(PermissionError):
    pass


def _under_fixtures(path: Path) -> bool:
    resolved = path.resolve()
    return resolved == FIXTURES or FIXTURES in resolved.parents


def line(record: BaseModel) -> str:
    return (
        json.dumps(
            record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        + "\n"
    )


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    if _under_fixtures(path):
        raise FixtureWriteRefused(f"refusing to write under fixtures/: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="\n") as f:
        for r in records:
            f.write(line(r))
            n += 1
    return n


class JsonlAppender:
    """A JSONL file opened once and written across many calls, so a large,
    sharded run never holds every record in memory. Same canonical bytes as
    `write_jsonl` (`line`), same fixtures refusal. Caller closes it."""

    def __init__(self, path: Path) -> None:
        if _under_fixtures(path):
            raise FixtureWriteRefused(f"refusing to write under fixtures/: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("w", newline="\n")
        self.n = 0

    def write_one(self, record: BaseModel) -> None:
        self._f.write(line(record))
        self.n += 1

    def write(self, records: Iterable[BaseModel]) -> None:
        for r in records:
            self.write_one(r)

    def close(self) -> None:
        self._f.close()


def write_csv(path: Path, records: list[BaseModel]) -> int:
    if _under_fixtures(path):
        raise FixtureWriteRefused(f"refusing to write under fixtures/: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r.model_dump(mode="json") for r in records]
    columns = list(type(records[0]).model_fields) if records else []
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows)
