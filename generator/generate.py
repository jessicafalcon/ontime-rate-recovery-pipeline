"""The seeded generator. `generate(profile)` is a pure function of the profile
(which carries the seed): every draw derives from `profile.seed`.

Sharding (fix/large-profile): the event stream is drawn from `profile.shards`
independent streams — shard `s` is `Random(profile.seed + s·P_SHARD)`, users
partitioned into `shards` contiguous blocks. Each shard is drawn in the same
day-major / user-major order as a single stream would be over its users (cohort
choice → latent draw → the per-day loop → the three injectors), so **emit order
is preserved within a shard**; counter ids thread across shards in shard order.
At `shards == 1` there is one block, its seed is `profile.seed + 0·P_SHARD =
profile.seed`, and every draw, emit and id is identical to the old single
`Random(profile.seed)` — so `tiny` and `medium` reproduce byte-for-byte
(DECISIONS, fix/large-profile). The `dim_user` stream is separate and never
sharded (`profile.seed*7919+1` in `dims.py`). Fixed `SIM_START`, counter ids,
sorted iteration everywhere.

Cause-first: for every prompt×user the cause is drawn, then the events that
cause implies are emitted; injectors run after and never change it (skew sets
`unattributed` by definition)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any
from zoneinfo import ZoneInfo

from generator.dims import build_dims, tz_at
from generator.models import (
    Cause,
    DimUserRow,
    Event,
    EventType,
    LatentUser,
    PromptCause,
)
from generator.profiles import Profile
from generator.response import responds

SIM_START = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday; fixed forever
# The per-shard seed offset: shard s draws from Random(seed + s·P_SHARD). At
# s == 0 this is Random(seed) exactly, which is what preserves byte-identity at
# shards == 1 (Knuth's multiplicative constant, an arbitrary large odd stride).
P_SHARD = 2_654_435_761
CLIENT_SIDE = {
    EventType.prompt_opened,
    EventType.capture_started,
    EventType.upload_started,
    EventType.upload_failed,
    EventType.upload_completed,
    EventType.app_opened,
}


@dataclass
class Output:
    events: list[Event]
    dims: list[DimUserRow]
    latent_users: list[LatentUser]
    prompt_causes: list[PromptCause]


@dataclass
class _Ctx:
    profile: Profile
    rng: Random
    events: list[Event] = field(default_factory=list)
    n_insert: int = 0
    n_prompt: int = 0
    n_response: int = 0

    def emit(
        self,
        et: EventType,
        uid: str,
        client: datetime,
        received: datetime,
        upload: datetime | None = None,
        **props: Any,
    ) -> Event:
        self.n_insert += 1
        if upload is None:
            upload = received + _secs(self.rng, 60, 1800)
        ev = Event(
            insert_id=f"e-{self.n_insert:07d}",
            event_type=et,
            user_id=uid,
            device_id=uid.replace("u-", "d-"),
            client_event_time=client,
            server_received_time=received,
            server_upload_time=upload,
            event_properties=props,
        )
        self.events.append(ev)
        return ev


def _secs(rng: Random, lo: float, hi: float) -> timedelta:
    return timedelta(seconds=int(rng.uniform(lo, hi)))


def local_to_utc(day: datetime, local_hour: float, tz: str) -> datetime:
    minutes = int(round(local_hour * 60)) % (24 * 60)
    local = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz)) + timedelta(
        minutes=minutes
    )
    return local.astimezone(UTC)


def local_hour_of(ts: datetime, tz: str) -> float:
    lt = ts.astimezone(ZoneInfo(tz))
    return lt.hour + lt.minute / 60.0 + lt.second / 3600.0


def assign_cause(
    profile: Profile, user: LatentUser, local_hour: float, rng: Random
) -> Cause:
    if rng.random() < profile.delivery_fault_rate:
        return Cause.delivery_fault
    if rng.random() < profile.clock_skew_rate:
        return Cause.unattributed
    if not responds(local_hour, user, profile.window_minutes, rng):
        return Cause.timing_gap
    if rng.random() < profile.upload_fault_rate:
        return Cause.upload_fault
    return Cause.on_time


def _send_time(
    rows: list[DimUserRow], day: datetime, hour: int
) -> tuple[datetime, str]:
    """Send instant for the cohort hour on `day`, in the tz valid AT that instant
    (a tz change at UTC midnight can fall between the day's start and the send)."""
    tz = tz_at(rows, day)
    for _ in range(2):
        send = local_to_utc(day, hour, tz)
        tz2 = tz_at(rows, send)
        if tz2 == tz:
            break
        tz = tz2
    return send, tz


def _prompt(
    ctx: _Ctx, uid: str, cohort: str, pid: str, send: datetime, cause: Cause
) -> None:
    p = ctx.profile
    rng = ctx.rng
    window = timedelta(minutes=p.window_minutes)
    window_end = send + window
    ctx.emit(
        EventType.prompt_sent,
        uid,
        send,
        send,
        prompt_id=pid,
        cohort_id=cohort,
        window_minutes=p.window_minutes,
    )
    if cause is Cause.delivery_fault:
        return
    delivered = send + _secs(rng, 5, 120)
    ctx.emit(EventType.prompt_delivered, uid, delivered, delivered, prompt_id=pid)
    if cause is Cause.timing_gap:
        return
    opened = delivered + _secs(rng, 60, window.total_seconds() / 3)
    ctx.emit(
        EventType.prompt_opened, uid, opened, opened + _secs(rng, 5, 120), prompt_id=pid
    )
    capture = opened + _secs(rng, 30, 180)
    if cause is Cause.upload_fault and rng.random() < 0.5:
        # Device offline from capture until after the window: client times
        # inside, received times outside — the three-clock signal.
        online = window_end + _secs(rng, 600, 6 * 3600)
        ctx.emit(
            EventType.capture_started,
            uid,
            capture,
            online + _secs(rng, 1, 5),
            prompt_id=pid,
        )
        started = capture + _secs(rng, 20, 120)
        ctx.emit(
            EventType.upload_started,
            uid,
            started,
            online + _secs(rng, 5, 10),
            prompt_id=pid,
            attempt=1,
            error_code=None,
        )
        done = online + _secs(rng, 10, 60)
        ctx.emit(
            EventType.upload_completed,
            uid,
            done,
            done + _secs(rng, 5, 60),
            prompt_id=pid,
            attempt=1,
            error_code=None,
        )
        _response(ctx, uid, pid, ctx.events[-1].server_received_time)
        return
    ctx.emit(
        EventType.capture_started,
        uid,
        capture,
        capture + _secs(rng, 5, 120),
        prompt_id=pid,
    )
    if cause is Cause.upload_fault:
        t = capture + _secs(rng, 20, 120)
        for attempt in range(1, rng.randint(2, 3) + 1):
            ctx.emit(
                EventType.upload_started,
                uid,
                t,
                t + _secs(rng, 5, 120),
                prompt_id=pid,
                attempt=attempt,
                error_code=None,
            )
            t += _secs(rng, 10, 90)
            ctx.emit(
                EventType.upload_failed,
                uid,
                t,
                t + _secs(rng, 5, 120),
                prompt_id=pid,
                attempt=attempt,
                error_code="E_NET",
            )
            t += _secs(rng, 30, 300)
        return
    started = capture + _secs(rng, 20, 120)
    ctx.emit(
        EventType.upload_started,
        uid,
        started,
        started + _secs(rng, 5, 120),
        prompt_id=pid,
        attempt=1,
        error_code=None,
    )
    done = started + _secs(rng, 5, 60)
    ctx.emit(
        EventType.upload_completed,
        uid,
        done,
        done + _secs(rng, 5, 120),
        prompt_id=pid,
        attempt=1,
        error_code=None,
    )
    _response(ctx, uid, pid, ctx.events[-1].server_received_time)


def _response(ctx: _Ctx, uid: str, pid: str, at: datetime) -> None:
    ctx.n_response += 1
    ctx.emit(
        EventType.response_recorded,
        uid,
        at,
        at,
        prompt_id=pid,
        response_id=f"r-{ctx.n_response:06d}",
    )


def _organic(ctx: _Ctx, uid: str, user: LatentUser, day: datetime, tz: str) -> None:
    rate = ctx.profile.organic_opens_per_day
    n = int(rate) + (1 if ctx.rng.random() < rate - int(rate) else 0)
    for _ in range(n):
        hour = (
            user.reachable_center_local_hour
            + ctx.rng.gauss(0, user.reachable_width_hours / 2)
        ) % 24
        t = local_to_utc(day, hour, tz)
        ctx.emit(EventType.app_opened, uid, t, t + _secs(ctx.rng, 5, 120))


def inject_duplicates(events: list[Event], profile: Profile, rng: Random) -> None:
    """Amplitude exports can carry the same `insert_id` twice: same content,
    a later upload batch. Appended, so arrival order is re-derived after."""
    for ev in list(events):
        if rng.random() < profile.duplicate_rate:
            events.append(
                ev.model_copy(
                    update={
                        "server_upload_time": ev.server_upload_time
                        + _secs(rng, 0, 3600)
                    }
                )
            )


def inject_late_arrival(
    events: list[Event], late: set[str], profile: Profile, rng: Random
) -> None:
    """A late export batch: every event of a late prompt lands hours later.
    Received times are untouched, so the cause is untouched."""
    for i, ev in enumerate(events):
        if ev.event_properties.get("prompt_id") in late:
            delay = timedelta(hours=rng.uniform(1, profile.late_arrival_max_hours))
            events[i] = ev.model_copy(
                update={
                    "server_upload_time": ev.server_upload_time
                    + timedelta(seconds=int(delay.total_seconds()))
                }
            )


def inject_clock_skew(
    events: list[Event], skewed: set[str], profile: Profile, rng: Random
) -> None:
    """Forward client-clock skew beyond SKEW_MAX_MIN on the client-side events of
    an `unattributed` prompt: received − client goes negative past the bound."""
    for i, ev in enumerate(events):
        if (
            ev.event_type in CLIENT_SIDE
            and ev.event_properties.get("prompt_id") in skewed
        ):
            skew = timedelta(minutes=profile.clock_skew_min) + _secs(rng, 0, 3600)
            events[i] = ev.model_copy(
                update={"client_event_time": ev.client_event_time + skew}
            )


def arrival_order(events: list[Event]) -> list[Event]:
    """Emit order is arrival order: upload time, then insert_id (the tie-break)."""
    return sorted(events, key=lambda e: (e.server_upload_time, e.insert_id))


@dataclass
class _Counters:
    """The three id counters, threaded across shards in shard order so ids stay
    globally unique and contiguous (shard 0 starts at 0)."""

    n_insert: int = 0
    n_prompt: int = 0
    n_response: int = 0


@dataclass
class Prepared:
    """The per-run, cross-shard state built before any shard is generated: the
    user blocks, one `Random` per shard, the cohort assignment, and the dims
    (built once over all users from the separate dim stream)."""

    user_ids: list[str]
    blocks: list[list[str]]
    rngs: list[Random]
    cohort_of: dict[str, str]
    dims: list[DimUserRow]
    rows_of: dict[str, list[DimUserRow]]


@dataclass
class ShardOutput:
    events: list[Event]  # this shard's events, in emit order (not yet arrival-sorted)
    latent_users: list[LatentUser]
    prompt_causes: list[PromptCause]


def _partition(user_ids: list[str], shards: int) -> list[list[str]]:
    """`shards` contiguous, near-equal blocks; block s is `[s·N/shards,
    (s+1)·N/shards)`. At shards == 1 the one block is all users."""
    n = len(user_ids)
    return [
        user_ids[(s * n) // shards : ((s + 1) * n) // shards] for s in range(shards)
    ]


def _prepare(profile: Profile) -> Prepared:
    user_ids = [f"u-{i:06d}" for i in range(1, profile.users + 1)]
    blocks = _partition(user_ids, profile.shards)
    cohorts = sorted(profile.cohorts)
    rngs = [Random(profile.seed + s * P_SHARD) for s in range(profile.shards)]
    cohort_of: dict[str, str] = {}
    for s, block in enumerate(blocks):
        rng = rngs[s]
        for uid in block:  # cohort choice is each shard's first draw
            cohort_of[uid] = rng.choice(cohorts)
    dims = build_dims(profile, user_ids, cohort_of, SIM_START)
    rows_of: dict[str, list[DimUserRow]] = {uid: [] for uid in user_ids}
    for r in dims:
        rows_of[r.user_id].append(r)
    return Prepared(user_ids, blocks, rngs, cohort_of, dims, rows_of)


def _generate_shard(
    profile: Profile,
    block: list[str],
    rng: Random,
    prep: Prepared,
    counters: _Counters,
) -> ShardOutput:
    """One shard, drawn in the same order a single stream would use over `block`:
    latent draw → the per-day loop → the three injectors. Mutates `counters`."""
    ctx = _Ctx(
        profile=profile,
        rng=rng,
        n_insert=counters.n_insert,
        n_prompt=counters.n_prompt,
        n_response=counters.n_response,
    )
    latent: dict[str, LatentUser] = {}
    for uid in block:
        latent[uid] = LatentUser(
            user_id=uid,
            cohort_id=prep.cohort_of[uid],
            reachable_center_local_hour=round(
                (profile.cohorts[prep.cohort_of[uid]] + rng.gauss(0, 4)) % 24, 3
            ),
            reachable_width_hours=profile.reachable_width_hours,
        )
    causes: list[PromptCause] = []
    late: set[str] = set()
    skewed: set[str] = set()
    for d in range(profile.days):
        day = SIM_START + timedelta(days=d)
        for uid in block:
            user = latent[uid]
            send, tz = _send_time(
                prep.rows_of[uid], day, profile.cohorts[user.cohort_id]
            )
            local_hour = local_hour_of(send, tz)
            cause = assign_cause(profile, user, local_hour, rng)
            ctx.n_prompt += 1
            pid = f"p-{ctx.n_prompt:06d}"
            causes.append(
                PromptCause(
                    prompt_id=pid,
                    user_id=uid,
                    cause=cause,
                    local_send_hour=round(local_hour, 3),
                )
            )
            _prompt(ctx, uid, user.cohort_id, pid, send, cause)
            if cause is Cause.unattributed:
                skewed.add(pid)
            if rng.random() < profile.late_arrival_rate:
                late.add(pid)
            _organic(ctx, uid, user, day, tz)
    inject_clock_skew(ctx.events, skewed, profile, rng)
    inject_late_arrival(ctx.events, late, profile, rng)
    inject_duplicates(ctx.events, profile, rng)
    counters.n_insert = ctx.n_insert
    counters.n_prompt = ctx.n_prompt
    counters.n_response = ctx.n_response
    return ShardOutput(ctx.events, [latent[u] for u in block], causes)


def iter_shards(profile: Profile, prep: Prepared) -> Iterator[ShardOutput]:
    """Each shard in shard order, threading the id counters. The streaming
    writer (`generator/cli.py`) consumes this so no run holds every event."""
    counters = _Counters()
    for s, block in enumerate(prep.blocks):
        yield _generate_shard(profile, block, prep.rngs[s], prep, counters)


def generate(profile: Profile) -> Output:
    """In-memory over all shards: events are shard-major, each shard internally
    in arrival order. At shards == 1 that is the whole stream in arrival order —
    byte-identical to the old single-`Random` generator."""
    prep = _prepare(profile)
    events: list[Event] = []
    latent_users: list[LatentUser] = []
    causes: list[PromptCause] = []
    for so in iter_shards(profile, prep):
        events.extend(arrival_order(so.events))
        latent_users.extend(so.latent_users)
        causes.extend(so.prompt_causes)
    return Output(
        events=events,
        dims=prep.dims,
        latent_users=latent_users,
        prompt_causes=causes,
    )
