"""dim_user SCD2 seed: tz drawn from the profile's mix; a `tz_change_rate`
share of users change tz once mid-run (two rows)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from random import Random

from generator.models import DimUserRow
from generator.profiles import Profile


def build_dims(
    profile: Profile,
    user_ids: list[str],
    cohort_of: dict[str, str],
    sim_start: datetime,
) -> list[DimUserRow]:
    rng = Random(profile.seed * 7919 + 1)
    tzs = sorted(profile.tz_mix)
    weights = [profile.tz_mix[t] for t in tzs]
    rows: list[DimUserRow] = []
    for uid in user_ids:
        tz = rng.choices(tzs, weights)[0]
        signup = (sim_start - timedelta(days=rng.randint(1, 90))).date()
        first_from = datetime.combine(signup, datetime.min.time(), sim_start.tzinfo)
        change_wanted = profile.days > 1 and rng.random() < profile.tz_change_rate
        others = [t for t in tzs if t != tz]
        if change_wanted and others:
            new_tz = rng.choice(others)
            change = sim_start + timedelta(days=rng.randint(1, profile.days - 1))
            rows.append(_row(uid, tz, cohort_of[uid], signup, first_from, change))
            rows.append(_row(uid, new_tz, cohort_of[uid], signup, change, None))
        else:
            rows.append(_row(uid, tz, cohort_of[uid], signup, first_from, None))
    return rows


def _row(
    uid: str,
    tz: str,
    cohort: str,
    signup: date,
    valid_from: datetime,
    valid_to: datetime | None,
) -> DimUserRow:
    return DimUserRow(
        user_id=uid,
        tz=tz,
        cohort_id=cohort,
        signup_date=signup,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def tz_at(rows: list[DimUserRow], ts: datetime) -> str:
    """The tz valid at `ts` (valid_from <= ts < valid_to)."""
    for r in rows:
        if r.valid_from <= ts and (r.valid_to is None or ts < r.valid_to):
            return r.tz
    raise LookupError(f"no dim_user row valid at {ts} for {rows[0].user_id}")
