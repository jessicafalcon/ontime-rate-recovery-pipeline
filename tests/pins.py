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
