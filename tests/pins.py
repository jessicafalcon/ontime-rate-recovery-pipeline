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
