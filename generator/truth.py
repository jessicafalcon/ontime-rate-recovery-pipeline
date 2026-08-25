"""The ONLY writer of the truth side-file (ARCHITECTURE §2.4). Two files under
`<out>/truth/`: `users.jsonl` (latent reachable window) and `prompts.jsonl`
(assigned cause). Never a dbt source; only `eval/` reads them."""

from __future__ import annotations

from pathlib import Path

from generator.generate import Output
from generator.writer import write_jsonl

TRUTH_DIR = "truth"


def write_truth(out: Path, output: Output) -> int:
    n = write_jsonl(out / TRUTH_DIR / "users.jsonl", output.latent_users)
    n += write_jsonl(out / TRUTH_DIR / "prompts.jsonl", output.prompt_causes)
    return n
