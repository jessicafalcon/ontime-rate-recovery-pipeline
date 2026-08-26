"""Counterfactual simulation (Phase 6, ARCHITECTURE §7): every prompt is
re-drawn under three schedules with common random numbers and the
generator's own response rule, and the per-cause counts land in a generated
block of docs/RESULTS.md.

Common random numbers: four uniforms per prompt, drawn once in `prompt_id`
order, applied in the generator's order (delivery → skew → respond →
upload). Every arm sees the same four, so `delivery_fault` and
`unattributed` are identical across arms by construction and only the
response threshold — `open_probability` at the arm's hour — can move a
prompt. No time quantity is drawn: the arms differ in causes, never in
lateness. The recommended arm reads the SERVED pair (`send_hour_local`,
`send_minute_local`), never the unclamped centre."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from random import Random

import duckdb

from eval.score import LABELS, built_labels, label_counts
from generator import profiles
from generator.models import LatentUser
from generator.response import open_probability

ARMS = ("baseline", "cohort", "recommended")
BEGIN = "<!-- simulate:begin {profile} -->"
END = "<!-- simulate:end {profile} -->"
COLUMNS = (*LABELS, "prompts_sent", "prompts_delivered", "ontime_rate")


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    user_id: str
    local_send_hour: float


@dataclass(frozen=True)
class Knobs:
    """The profile's cause rates — the generator's input, not its output."""

    delivery_fault_rate: float
    clock_skew_rate: float
    upload_fault_rate: float
    window_minutes: int


def knobs(profile: str) -> Knobs:
    p = profiles.load(profile)
    return Knobs(
        delivery_fault_rate=p.delivery_fault_rate,
        clock_skew_rate=p.clock_skew_rate,
        upload_fault_rate=p.upload_fault_rate,
        window_minutes=p.window_minutes,
    )


def read_prompts(prompts_jsonl: Path) -> list[Prompt]:
    """Every prompt with the hour the data was generated at, in `prompt_id`
    order (the declared key — uniform i belongs to prompt i)."""
    out: list[Prompt] = []
    for line in prompts_jsonl.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out.append(
                Prompt(rec["prompt_id"], rec["user_id"], float(rec["local_send_hour"]))
            )
    return sorted(out, key=lambda p: p.prompt_id)


def read_latent(users_jsonl: Path) -> dict[str, LatentUser]:
    out: dict[str, LatentUser] = {}
    for line in users_jsonl.read_text().splitlines():
        if line.strip():
            u = LatentUser.model_validate_json(line)
            out[u.user_id] = u
    return out


def built_schedule(db: Path) -> dict[str, tuple[float, int]]:
    """user_id → (served hour as a fraction, cohort band anchor), off the
    served columns of scores_send_time — never center_hour_local."""
    con = duckdb.connect(str(db))  # dbt may hold its own in-process handle
    try:
        rows = con.execute(
            "select user_id, send_hour_local + send_minute_local / 60.0, "
            "cohort_hour_local from main_scores.scores_send_time"
        ).fetchall()
    finally:
        con.close()
    return {uid: (float(served), int(anchor)) for uid, served, anchor in rows}


def draw_uniforms(n: int, seed: int) -> list[tuple[float, float, float, float]]:
    """Four uniforms per prompt from one seeded stream: the common random
    numbers every arm shares."""
    rng = Random(seed)
    return [(rng.random(), rng.random(), rng.random(), rng.random()) for _ in range(n)]


def cause_of(
    u: tuple[float, float, float, float], local_hour: float, user: LatentUser, k: Knobs
) -> str:
    """The generator's draw order (`generate.py::assign_cause`) on fixed
    uniforms instead of a live stream."""
    if u[0] < k.delivery_fault_rate:
        return "delivery_fault"
    if u[1] < k.clock_skew_rate:
        return "unattributed"
    if u[2] >= open_probability(local_hour, user, k.window_minutes):
        return "timing_gap"
    if u[3] < k.upload_fault_rate:
        return "upload_fault"
    return "on_time"


def label_prompts(
    prompts: list[Prompt],
    hour_of: Callable[[Prompt], float],
    uniforms: list[tuple[float, float, float, float]],
    latent: dict[str, LatentUser],
    k: Knobs,
) -> dict[str, str]:
    """prompt_id → simulated cause under the schedule `hour_of`."""
    return {
        p.prompt_id: cause_of(u, hour_of(p), latent[p.user_id], k)
        for p, u in zip(prompts, uniforms, strict=True)
    }


def simulate_arm(
    prompts: list[Prompt],
    hour_of: Callable[[Prompt], float],
    uniforms: list[tuple[float, float, float, float]],
    latent: dict[str, LatentUser],
    k: Knobs,
) -> dict[str, int]:
    return label_counts(label_prompts(prompts, hour_of, uniforms, latent, k))


def ontime_rate(counts: dict[str, int]) -> float | None:
    """`on_time / prompts_delivered` with the METRICS denominator (sent minus
    delivery faults); None only when nothing was delivered."""
    delivered = sum(counts.values()) - counts["delivery_fault"]
    if delivered == 0:
        return None
    return counts["on_time"] / delivered


def schedules(
    schedule: dict[str, tuple[float, int]],
) -> dict[str, Callable[[Prompt], float]]:
    return {
        "baseline": lambda p: p.local_send_hour,
        "cohort": lambda p: float(schedule[p.user_id][1]),
        "recommended": lambda p: schedule[p.user_id][0],
    }


def arm_rows(
    db: Path, truth: Path, profile: str, seed: int
) -> list[tuple[str, dict[str, int]]]:
    """The `data` row (built attribution counts) then one row per arm."""
    prompts = read_prompts(truth / "prompts.jsonl")
    latent = read_latent(truth / "users.jsonl")
    k = knobs(profile)
    uniforms = draw_uniforms(len(prompts), seed)
    hours = schedules(built_schedule(db))
    rows = [("data", label_counts(built_labels(db)))]
    for arm in ARMS:
        rows.append((arm, simulate_arm(prompts, hours[arm], uniforms, latent, k)))
    return rows


def _fmt(rate: float | None) -> str:
    return "NULL" if rate is None else f"{rate:.6f}"


def render_block(profile: str, rows: list[tuple[str, dict[str, int]]]) -> str:
    """The Markdown table plus the lift line; causes in LABELS order, arms in
    the order given (ARMS) — explicit keys, never insertion order."""
    causes = sorted(LABELS, key=lambda c: (LABELS.index(c), c))
    header = ["arm", *causes, "prompts_sent", "prompts_delivered", "ontime_rate"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    by_name = dict(rows)
    for name, counts in rows:
        sent = sum(counts.values())
        delivered = sent - counts["delivery_fault"]
        cells = [name, *(str(counts[c]) for c in causes)]
        cells += [str(sent), str(delivered), _fmt(ontime_rate(counts))]
        lines.append("| " + " | ".join(cells) + " |")
    base, rec = by_name["baseline"], by_name["recommended"]
    r0, r1 = ontime_rate(base), ontime_rate(rec)
    lift = "NULL" if r0 is None or r1 is None else f"{r1 - r0:+.6f}"
    lines.append("")
    lines.append(
        f"Lift, recommended − baseline: ontime_rate {lift}; "
        f"timing_gap {rec['timing_gap'] - base['timing_gap']:+d}; "
        f"on_time {rec['on_time'] - base['on_time']:+d}; "
        f"upload_fault {rec['upload_fault'] - base['upload_fault']:+d}; "
        f"delivery_fault and unattributed unchanged by construction "
        f"({rec['delivery_fault']}, {rec['unattributed']}). "
        f"Profile `{profile}`, {sum(base.values())} prompts, seed {SEED_NOTE}."
    )
    return "\n".join(lines) + "\n"


SEED_NOTE = "`tests/pins.py::SIMULATE_SEED`"


def render(db: Path, truth: Path, profile: str, seed: int) -> str:
    return render_block(profile, arm_rows(db, truth, profile, seed))
