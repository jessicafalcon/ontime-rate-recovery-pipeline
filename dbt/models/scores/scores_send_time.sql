-- One row per user (the open dim_user row): the send time the pipeline
-- serves (docs/METRICS.md § scores_send_time). The model is a dbt model —
-- Python never computes a score (CLAUDE.md).
--
-- Hours live on a circle: each open's bin centre (hour + 0.5) becomes an angle
-- θ = 2π (hour + 0.5) / 24, and a set of opens is summarised by its resultant
-- vector (Σ cos θ, Σ sin θ). The cohort prior is the pooled vector of every
-- open in the cohort, normalised: direction mu_c, mean resultant length
-- rbar_c in [0, 1]. Shrinkage adds the prior as shrinkage_pseudo_count
-- pseudo-opens: the posterior direction is the angle of
--   user vector + k · rbar_c · (cos mu_c, sin mu_c)
-- and confidence is that vector's mean resultant length, |combined| / (n + k).
-- With no opens the centre is mu_c and confidence is rbar_c exactly — an
-- identity, not a branch.
--
-- The cohort moment is the local hour h, over the cohort's OPENED bins (an
-- optimal window can always start at one), whose window [h, h + window_minutes)
-- (circular; window_minutes from the cohort's prompts) holds the most pooled
-- opens — ties to the smaller opened hour (order by mass desc, hour asc). The served
-- time is the posterior centre clamped to ±max_user_shift_min of that moment.
-- Circular arithmetic is ANSI: x − 24·floor(x/24) wraps, and
-- d − 24·floor((d + 12)/24) is the signed short-arc difference in (−12, 12].
-- No percent operator and no mod on floats (BigQuery's MOD is integer-only).

with users as (

    select
        user_id,
        cohort_id
    from {{ source('raw', 'dim_user') }}
    where valid_to is null

),

feats as (

    select
        f.user_id,
        u.cohort_id,
        f.hour_local,
        f.n_opens,
        f.last_open_at,
        cos(2 * acos(-1) * (f.hour_local + 0.5) / 24) as cos_theta,
        sin(2 * acos(-1) * (f.hour_local + 0.5) / 24) as sin_theta
    from {{ ref('features_user_hour') }} as f
    inner join users as u
        on u.user_id = f.user_id

),

as_of as (

    select
        max(last_open_at) as computed_as_of
    from feats

),

user_vec as (

    select
        user_id,
        sum(n_opens) as n_opens,
        sum(n_opens * cos_theta) as ux,
        sum(n_opens * sin_theta) as uy
    from feats
    group by user_id

),

cohort_prior as (

    select
        cohort_id,
        atan2(sum(n_opens * sin_theta), sum(n_opens * cos_theta)) as mu_c,
        sqrt(
            {{ safe_divide('sum(n_opens * cos_theta)', 'sum(n_opens)') }}
            * {{ safe_divide('sum(n_opens * cos_theta)', 'sum(n_opens)') }}
            + {{ safe_divide('sum(n_opens * sin_theta)', 'sum(n_opens)') }}
            * {{ safe_divide('sum(n_opens * sin_theta)', 'sum(n_opens)') }}
        ) as rbar_c
    from feats
    group by cohort_id

),

cohort_window as (

    select
        cohort_id,
        cast(ceil(max(window_minutes) / 60.0) as integer) as window_bins
    from {{ ref('stg_prompts') }}
    group by cohort_id

),

cohort_bins as (

    select
        cohort_id,
        hour_local,
        sum(n_opens) as n_opens
    from feats
    group by
        cohort_id,
        hour_local

),

window_mass as (

    select
        s.cohort_id,
        s.hour_local,
        sum(b.n_opens) as mass
    from cohort_bins as s
    inner join cohort_bins as b
        on b.cohort_id = s.cohort_id
    inner join cohort_window as w
        on w.cohort_id = s.cohort_id
    where mod(b.hour_local - s.hour_local + 24, 24) < w.window_bins
    group by
        s.cohort_id,
        s.hour_local

),

cohort_moment as (

    select
        cohort_id,
        hour_local as cohort_hour_local
    from (
        select
            cohort_id,
            hour_local,
            row_number() over (
                partition by cohort_id
                order by mass desc, hour_local asc
            ) as rank_in_cohort
        from window_mass
    ) as ranked
    where rank_in_cohort = 1

),

posterior as (

    select
        u.user_id,
        u.cohort_id,
        m.cohort_hour_local,
        coalesce(v.n_opens, 0) as n_opens,
        coalesce(v.ux, 0) + {{ var('shrinkage_pseudo_count') }} * p.rbar_c * cos(p.mu_c) as px,
        coalesce(v.uy, 0) + {{ var('shrinkage_pseudo_count') }} * p.rbar_c * sin(p.mu_c) as py
    from users as u
    inner join cohort_prior as p
        on p.cohort_id = u.cohort_id
    inner join cohort_moment as m
        on m.cohort_id = u.cohort_id
    left join user_vec as v
        on v.user_id = u.user_id

),

centred as (

    select
        user_id,
        cohort_id,
        cohort_hour_local,
        n_opens,
        atan2(py, px) * 24 / (2 * acos(-1))
            - 24 * floor(atan2(py, px) * 24 / (2 * acos(-1)) / 24) as center_hour_local,
        sqrt(px * px + py * py) / (n_opens + {{ var('shrinkage_pseudo_count') }}) as confidence
    from posterior

),

shifted as (

    select
        user_id,
        cohort_id,
        cohort_hour_local,
        center_hour_local,
        confidence,
        center_hour_local - cohort_hour_local
            - 24 * floor((center_hour_local - cohort_hour_local + 12) / 24) as shift_hours,
        {{ var('max_user_shift_min') }} / 60.0 as max_shift_hours
    from centred

),

clamped as (

    select
        user_id,
        cohort_id,
        cohort_hour_local,
        center_hour_local,
        confidence,
        case
            when shift_hours > max_shift_hours then cohort_hour_local + max_shift_hours
            when shift_hours < -max_shift_hours then cohort_hour_local - max_shift_hours
            else cohort_hour_local + shift_hours
        end as send_hour_frac
    from shifted

),

minutes as (

    -- whole minutes on the 1440-minute circle (integer mod is ANSI on both
    -- engines; a floor on the float would turn 0.4999… h into 29 min)
    select
        user_id,
        cohort_id,
        cohort_hour_local,
        center_hour_local,
        confidence,
        mod(cast(round(send_hour_frac * 60) as integer) + 1440, 1440) as send_minute_of_day
    from clamped

)

select
    w.user_id,
    w.cohort_id,
    cast(floor(w.send_minute_of_day / 60.0) as integer) as send_hour_local,
    w.send_minute_of_day - 60 * cast(floor(w.send_minute_of_day / 60.0) as integer) as send_minute_local,
    w.cohort_hour_local,
    round(w.center_hour_local, 6) as center_hour_local,
    round(w.confidence, 6) as confidence,
    '{{ var('model_version') }}' as model_version,
    a.computed_as_of
from minutes as w
cross join as_of as a
