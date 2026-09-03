"""Every pinned number the offline suite asserts (CLAUDE.md → Repo map).
Read off the first green build of the committed fixture; a drift is a red
test, never a rewritten constant (spec Phase 2 invariant 5)."""

# fixtures/tiny — Phase 2 (staging)
RAW_FILES = 10  # events_2026-01-04 (Tokyo day 1) … events_2026-01-13 (late arrivals)
RAW_EVENT_ROWS = 970  # includes the duplicate injector's copies
DIM_USER_ROWS = 22  # 20 users, two with a tz change (u-000008, u-000010)
DIM_USER_CLOSED_ROWS = 2  # rows with valid_to set
STG_EVENT_ROWS = 926  # distinct insert_id
DEDUPE_COUNT = RAW_EVENT_ROWS - STG_EVENT_ROWS  # 44
STG_PROMPT_ROWS = 140  # 20 users × 7 days
STG_PROMPTS_UNDELIVERED = 17  # no prompt_delivered receipt (delivery faults)
STG_NEGATIVE_DELAY_ROWS = 24  # client clock ahead (the skew injector); min −5155 s
STG_DELAY_RANGE_SECONDS = (-5155, 22090)
RAW_UPLOAD_ERROR_CODE_NULLS = 190  # upload_started/completed carry error_code: null
STG_UPLOAD_ERROR_CODE_NULLS = 180  # the same after dedupe (10 copies were duplicates)

# fixtures/tiny — Phase 3 (attribution). Read off the first green build; the
# truth counts are the generator's assigned causes (eval is the only reader).
ATTRIBUTION_ROWS = STG_PROMPT_ROWS  # exactly one label per prompt
TRUTH_LABEL_COUNTS = {
    "on_time": 75,
    "upload_fault": 8,
    "delivery_fault": 17,
    "timing_gap": 34,
    "unattributed": 6,
}
ATTRIBUTION_LABEL_COUNTS = TRUTH_LABEL_COUNTS  # every label recovered on tiny
LABEL_ACCURACY = 1.0  # `make eval PROFILE=tiny`; a drift is a red test
UNATTRIBUTED_SHARE = 6 / 140  # 0.043 < var unattributed_max (0.10)
SKEW_MAX_MIN = 5  # == generator/models.py::SKEW_MAX_MIN == dbt var skew_max_min
# The Phase 1 manifest lines (raw/dims/truth) — the Phase 3 re-freeze added
# expected/attribution.csv and moved none of these.
PHASE1_MANIFEST_LINES = 13

# fixtures/tiny — Phase 4 (marts). Read off the first green build.
COHORT_DAYS = 14  # 2 cohorts × 7 local prompt dates (2026-01-05 … 01-11)
PROMPTS_DELIVERED = STG_PROMPT_ROWS - TRUTH_LABEL_COUNTS["delivery_fault"]  # 123
ONTIME_RATE = TRUTH_LABEL_COUNTS["on_time"] / PROMPTS_DELIVERED  # 75 / 123
LOCAL_DATE_DIFFERS_FROM_UTC = 34  # prompts whose local date is not the UTC date
RETENTION_ROWS = 20  # one per user; every `retained` NULL (7 days < retention_days)
ORGANIC_OPEN_ROWS = 211  # staged app_opened — the column no label reads (BACKLOG)
RETENTION_DAYS = 28  # == dbt var retention_days (ARCHITECTURE §2.6)
PHASE3_MANIFEST_LINES = PHASE1_MANIFEST_LINES + 1  # + expected/attribution.csv

# fixtures/tiny + data/out/medium — Phase 5 (send-time model). Read off the
# first green build. medium is seeded (`seed: 2`), never frozen: the pins ARE
# its manifest (specs/phase-5-send-time.md, reconciliation item 1).
SCORES_ROWS = 20  # one per user
COHORT_HOUR_TINY = {
    "c-morning": 3,
    "c-evening": 16,
}  # c-morning: bins 3 and 10 tie at 12 → 3
COMPUTED_AS_OF_TINY = (
    "2026-01-12 00:47:00"  # max client_event_time of opens in the window
)
MAE_TINY = 0.81620145  # reachable-centre MAE, circular hours (the regression pin)
COVERAGE_TINY = 0.6  # served time inside centre ± width/2
MEDIUM_USERS = 2000
MAE_MEDIUM = 0.352353856  # the proof: 2,000 users, ~36 opens each
COVERAGE_MEDIUM = 0.7345
SEND_TIME_PINS = {
    "tiny": (MAE_TINY, COVERAGE_TINY),
    "medium": (MAE_MEDIUM, COVERAGE_MEDIUM),
}
FEATURE_WINDOW_DAYS = 30  # == dbt var feature_window_days
MAX_USER_SHIFT_MIN = 120  # == dbt var max_user_shift_min
SHRINKAGE_PSEUDO_COUNT = 5  # == dbt var shrinkage_pseudo_count
PHASE4_MANIFEST_LINES = PHASE3_MANIFEST_LINES + 1  # + expected/ontime_rate_daily.csv

# fixtures/tiny + data/out/medium — Phase 6 (counterfactual simulation and
# the A/B power table). Read off the first green run; the committed blocks in
# docs/RESULTS.md and docs/AB_DESIGN.md are the byte-level pins.
SIMULATE_SEED = 6  # the common-random-numbers stream; a parameter, never a knob
SIMULATED_TINY = {  # docs/RESULTS.md tiny block; "data" == ATTRIBUTION_LABEL_COUNTS
    "baseline": {
        "on_time": 66,
        "upload_fault": 18,
        "delivery_fault": 20,
        "timing_gap": 33,
        "unattributed": 3,
    },
    "cohort": {
        "on_time": 37,
        "upload_fault": 7,
        "delivery_fault": 20,
        "timing_gap": 73,
        "unattributed": 3,
    },
    "recommended": {
        "on_time": 62,
        "upload_fault": 17,
        "delivery_fault": 20,
        "timing_gap": 38,
        "unattributed": 3,
    },
}  # tiny's c-morning anchor is the bin-3/10 tie (Phase 5), so its lift is
# negative: a regression pin, not a proof
SIMULATED_MEDIUM_ONTIME_RATE = (
    0.460920,
    0.457732,
    0.623291,
)  # baseline, cohort, recommended
ONTIME_MEDIUM = 25498  # sum(on_time) over the medium mart
PROMPTS_SENT_MEDIUM = 60000  # 2,000 users × 30 days
PROMPTS_DELIVERED_MEDIUM = 55293  # sum(prompts_delivered) over the medium mart
ONTIME_RATE_MEDIUM = ONTIME_MEDIUM / PROMPTS_DELIVERED_MEDIUM  # 0.461143
# (profile, MDE pp, delivered prompts per arm, days at half the users per arm)
POWER_TABLE = [
    ("tiny", 1, 37174, 4232),
    ("tiny", 2, 9245, 1053),
    ("tiny", 5, 1452, 166),
    ("medium", 1, 39061, 43),
    ("medium", 2, 9775, 11),
    ("medium", 5, 1565, 2),
]

# fixtures/tiny — Phase 7 (incrementality and late arrival). Read off the first
# green two-landing build; the horizon is data-derived (max(server_upload_time)).
LOOKBACK_DAYS = 5  # == dbt var lookback_days; lookback_days * 24 (120 h) > the
# late_arrival_max_hours of every profile (tiny 48 h, medium 72 h)
LATE_FILE_TINY = "2026-01-13"  # the upload date the late arrivals land on
LANDING_SPLIT_TINY = "2026-01-12"  # bulk landing <= this, then the late tail
# After the full landing (horizon 2026-01-13): final = prompt_date <= 2026-01-08
# (2026-01-05 .. 08), the four closed local send dates; the rest provisional.
FINAL_PROMPTS_TINY = 80
PROVISIONAL_PROMPTS_TINY = STG_PROMPT_ROWS - FINAL_PROMPTS_TINY  # 60
# After the bulk landing alone (horizon 2026-01-12): final = prompt_date <= 07.
LANDING1_FINAL_PROMPTS_TINY = 60
# The one tiny duplicate whose copies land on different upload dates (2026-01-05,
# 2026-01-06): the dedupe keeps the earliest upload across landings.
STRADDLING_DUPLICATE_TINY = "e-0000259"

# fixtures/tiny — Phase 8a (write-back to send_schedule). One send_schedule row
# per scored user (the open dim_user row); tz is the CURRENT zone (the open SCD2
# row), written_at = computed_as_of (data-derived). The hash is over the nine
# §2.9 columns rendered as canonical CSV (eval/golden.render), sorted by user_id.
SEND_SCHEDULE_ROWS_TINY = 20  # == SCORES_ROWS
SEND_SCHEDULE_SHA256_TINY = (
    "4dab2540765a776cca8b41634861b34c5e0978a9db19b81dcc7405abc08e491e"
)

# fixtures/tiny — Phase 8b (Airflow DAG, backfill≡union). The three-interval
# backfill cut: 2026-01-07, then LANDING_SPLIT_TINY (2026-01-12), then
# LATE_FILE_TINY (2026-01-13 = the union). Consecutive gaps (5, 1) are ≤
# LOOKBACK_DAYS (5), so the incremental landings converge to a single union build
# (the Phase 7 `<=` reprocess-window boundary makes gap = lookback work); the
# final send_schedule == SEND_SCHEDULE_SHA256_TINY.
BACKFILL_THROUGHS_TINY = ("2026-01-07", LANDING_SPLIT_TINY, LATE_FILE_TINY)

# fix/holdout-eval (ROADMAP item 4) — the temporal holdout (ARCHITECTURE §7
# report (d)). The served schedule is trained on data landed with the upload-date
# cut (THROUGH); the RAW organic app_opened opens uploaded AFTER the cut are the
# held-out set the model never saw. Two measures per arm (recommended served hour,
# cohort band anchor): the share of held-out opens inside ±HOLDOUT_WINDOW_HOURS of
# the served hour, and the mean circular distance from the served hour to a user's
# nearest held-out open. The cut and window are parameters (like SIMULATE_SEED);
# the numbers below are read off the first green run, the committed docs/RESULTS.md
# blocks are the byte-level pins.
HOLDOUT_WINDOW_HOURS = 1.0  # a fixed ±1 h window, independent of the profile
HOLDOUT_CUTS = {
    "tiny": "2026-01-08",  # 04..08 train, opens uploaded 09..13 held out
    "medium": "2026-01-25",  # ~22 days train, opens uploaded after the cut held out
}
# (in_window_share, mean_nearest_hours) per arm, rounded to the block's 6 decimals
# — read off the first green run. recommended (per-user shift) beats cohort (the
# band anchor) on BOTH measures, on opens the model never saw: the non-circular
# signal (a higher share, a shorter nearest distance).
HOLDOUT_TINY = {
    "recommended": (0.180851, 1.127500),
    "cohort": (0.148936, 1.448333),
}
HOLDOUT_MEDIUM = {
    "recommended": (0.229350, 0.612883),
    "cohort": (0.164057, 1.096425),
}
