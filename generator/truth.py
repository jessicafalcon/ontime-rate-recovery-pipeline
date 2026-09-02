"""The ONLY writer of the truth side-file (ARCHITECTURE §2.4). Two files under
`<out>/truth/`: `users.jsonl` (latent reachable window) and `prompts.jsonl`
(assigned cause). Never a dbt source; only `eval/` reads them."""

from __future__ import annotations

from pathlib import Path

from generator.generate import Output, ShardOutput
from generator.writer import JsonlAppender, write_jsonl

TRUTH_DIR = "truth"


def write_truth(out: Path, output: Output) -> int:
    n = write_jsonl(out / TRUTH_DIR / "users.jsonl", output.latent_users)
    n += write_jsonl(out / TRUTH_DIR / "prompts.jsonl", output.prompt_causes)
    return n


class TruthStream:
    """The streaming form of `write_truth`: the two truth files opened once and
    written per shard, so a sharded run never holds every latent user / prompt
    cause. Same files, same canonical bytes; the only truth writer either way."""

    def __init__(self, out: Path) -> None:
        self._users = JsonlAppender(out / TRUTH_DIR / "users.jsonl")
        self._prompts = JsonlAppender(out / TRUTH_DIR / "prompts.jsonl")

    def write_shard(self, shard: ShardOutput) -> None:
        self._users.write(shard.latent_users)
        self._prompts.write(shard.prompt_causes)

    def close(self) -> int:
        n = self._users.n + self._prompts.n
        self._users.close()
        self._prompts.close()
        return n
