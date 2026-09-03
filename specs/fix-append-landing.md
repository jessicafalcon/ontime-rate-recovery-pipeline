# fix/append-landing — append-only, upload-date-partitioned raw landing (PROPOSED)

Contract for the `fix/append-landing` branch. Source: `docs/ROADMAP.md` item 6
(a post-plan `fix/` branch, cited as a BACKLOG row — **"the incremental re-run
re-scans all of raw"**, measured on `fix/large-profile` at 19.45 GB). Depends on
`fix/large-profile` and `fix/holdout-eval` merged (both are on `main`).

**Status: PROPOSED — do not start until approved.** No new dependencies (Python
`gzip` is stdlib; DuckDB and BigQuery both read gzipped JSON natively). This
branch RE-FREEZES `fixtures/tiny/raw` (the writer's packaging changes), so it is
a spec, not a bare amendment (CLAUDE.md → Fix amendments; ROADMAP item 6). It
also changes a write path and who-writes-what in the landing, so the opening
commit carries this spec **and** a DECISIONS entry, alone, before any code.

**Freeze: fixtures/tiny/MANIFEST.sha256** — the review gate admits the fixture
change only with this line (the new hourly-gzip `raw/` files and their manifest
lines). `make freeze PROFILE=tiny CONFIRM=yes` is the only writer.

## Why

`fix/large-profile` published the item-6 workload as a measured number, not a
guess (`docs/RESULTS.md` → "Incremental re-run is NOT cheaper here"): a second,
incremental BigQuery build scanned **19.45 GB** — *more* than the full build —
because `raw.events` has no partitioning, so the incremental models' lookback
cannot prune the SOURCE scan; every run re-reads all of raw. The landing also
recreates the whole raw table on every load (DuckDB) / `WRITE_TRUNCATE`s it
(BigQuery), and emits one plain daily `events_<date>.jsonl` — neither the append
semantics nor the file shape a real Amplitude export uses (§2.10).

This is a fix branch, not a phase: it changes the landing write path and the
fixture packaging to close a measured cost row, touching no metric, label, or
served number. It is one branch because the three changes are one mechanism —
the export's file shape, the partition it lands in, and the predicate that
prunes it are the same upload-date key seen at three layers.

## The central constraint

**The warehouse content does not move — only the packaging of raw and the bytes
scanned to read it.** The generator emits the same events; the writer repackages
them (gzipped hourly instead of plain daily), the landing appends them per
upload-date partition instead of recreating the table, and BigQuery prunes the
source scan to a superset window. Every DuckDB golden (`attribution`,
`ontime_rate_daily`, `scores_send_time`), every warehouse-derived pin
(label accuracy, MAE, coverage, the simulation, the holdout, `HOLDOUT_CUTS`),
and every `send_schedule` hash is byte-identical. Only the raw file-structure
pins move (`RAW_FILES` and the file-count / `_file_date` assertions), and only
the new frozen `MANIFEST.sha256`.

## DONE command

```
make test && make lint && \
make seed PROFILE=tiny && \
make dbt-build PROFILE=tiny && \
make attribution-golden PROFILE=tiny && make scores-golden PROFILE=tiny && \
make report PROFILE=tiny && make eval PROFILE=tiny && \
make holdout PROFILE=tiny
```

- `make test` — the offline suite: the new writer determinism (gzip mtime=0
  reproducible), the new landing idempotency (double-land writes 0 net rows),
  the THROUGH-rolls-up-to-the-partition equivalence, the dedupe-straddle
  safety property, and every unchanged pin.
- `make lint` — ruff (read-only in the gate).
- `make seed PROFILE=tiny` — regenerates the hourly-gzip `raw/` and prints
  `manifest match` against the **re-frozen** `fixtures/tiny/MANIFEST.sha256`
  (the writer is byte-deterministic).
- `make dbt-build … attribution-golden … scores-golden … report … eval …
  holdout` — the three goldens `0 differ`, the label-accuracy / MAE / coverage
  pins hold, the holdout block matches — the warehouse is unchanged through the
  new DuckDB landing.
- **Live cloud proof (ask-first, cents, run as the SA):**
  `make test-int-bigquery PROJECT=<id> CONFIRM=yes` — the three goldens read
  back from BigQuery byte-for-byte + the pins (Evidence rows 4, 6). The prune's
  own bytes-scanned number is a hand-recorded `docs/RESULTS.md` line (not a
  pinned block; job facts are non-deterministic, §Determinism carve-out).

## Done-when

1. **Hourly-gzip export shape.** The writer emits `raw/events_<date>_<HH>.jsonl.gz`
   (one gzip per upload-date hour that has ≥1 event), byte-identical on a
   re-seed of the same SEED (gzip written with `mtime=0`, fixed compresslevel).
   The old plain daily `events_<date>.jsonl` is gone. *Evidence: rows 1, 2.*
2. **THROUGH rolls up to the upload-date partition.** For every cut, the set of
   raw rows landed with `THROUGH=<upload-date>` is unchanged by the daily→hourly
   split — hourly files of a date land iff the date is `<= through`; the
   downstream build and `HOLDOUT_CUTS` are byte-identical. *Evidence: rows 3, 6.*
3. **Append-only, idempotent per upload-date partition.** The landing keeps the
   raw table across loads and lands each upload-date partition by
   delete-and-insert (DuckDB) / partition-decorator `WRITE_TRUNCATE` (BigQuery),
   never a whole-table recreate; re-landing an already-landed partition leaves
   `raw.events` content-identical and adds **0 net rows**. *Evidence: row 5.*
4. **BigQuery prunes the source scan; output is byte-identical.** `raw.events`
   is DAY-partitioned on `server_upload_time`; the incremental `stg_events`
   bounds its source read to a superset upload-time window (BigQuery only —
   DuckDB's SQL is unchanged). The incremental re-run scans a window, not all of
   raw, and every built table is byte-identical to the full-scan build.
   *Evidence: rows 4, 6.*
5. **The dedupe is never split by the prune.** For every duplicate `insert_id`,
   the two copies' `server_upload_time` span is within the prune window, so the
   pruned source read always sees both copies together and the earliest-copy
   dedupe (invariant 1 of `stg_events`) is unchanged. *Evidence: row 7.*
6. **Records re-frozen and updated.** `fixtures/tiny/MANIFEST.sha256`,
   `tests/pins.py` (raw-structure pins only), and the record files in §Record
   updates. *Evidence: row 8.*

(6 items. `docs/PHASES.md` is the phase plan and is not touched by a `fix/`
branch; `docs/ROADMAP.md` item 6 carries the "as landed" note at exit.)

## Evidence (REQUIRED)

| Done-when | Proof (test file / `make` target / command output) |
|---|---|
| 1 | `tests/test_generator.py::test_raw_files_are_hourly_gzip` — the seeded `raw/` matches `events_<date>_<HH>.jsonl.gz`, no plain `.jsonl` remains |
| 1, 6 | `tests/test_generator.py::test_reseed_is_byte_identical` / `make seed PROFILE=tiny` → `manifest match` — gzip `mtime=0` reproducibility |
| 2 | `tests/test_landing.py::test_through_rolls_hourly_files_to_upload_date` — landing `≤ cut` yields the same raw row multiset as the equivalent day-boundary selection |
| 4, 6 | `make test-int-bigquery PROJECT=<id> CONFIRM=yes` — the three `Golden` specs diff byte-for-byte off BigQuery + pins re-asserted (`tests/integration/test_int_bigquery.py`) |
| 5 | `tests/test_landing.py::test_double_land_partition_writes_zero_new_rows` — a second `load(profile, db, through)` over the same db leaves `count(*)` and content unchanged |
| 6 | `make holdout PROFILE=tiny` → `block matches`; `make attribution-golden`/`scores-golden`/`report` → `0 differ`; `make eval PROFILE=tiny` → the LABEL_ACCURACY / MAE / coverage pins |
| 7 | `tests/test_landing.py::test_duplicate_upload_span_within_lookback` — the injected duplicate copies (`STRADDLING_DUPLICATE_TINY`) span ≤ the prune window; a data property of every profile's raw |
| 8 | `git diff main...HEAD` over the §Record-updates list; `tests/test_pins.py` (structure pins re-read) |

## Invariants (REQUIRED)

| Invariant ("for all …, … holds") | Falsified by (scenario test) |
|---|---|
| For all SEED+profile, the seeded `raw/` bytes are identical on a re-seed — gzip carries no mtime/OS entropy. | `tests/test_generator.py::test_reseed_is_byte_identical` — seed twice, diff `raw/*.jsonl.gz` byte-for-byte |
| For all upload dates D and hours H, every event in `events_D_H.jsonl.gz` has `cast(server_upload_time as date) = D` — the file name is the partition key. | `tests/test_landing.py::test_file_date_equals_partition` — each hourly file's rows all fall on its named date |
| For all cuts, `load(profile, db, through)` yields the same raw row multiset as the pre-change daily landing did for that cut — hourly packaging never moves a row across the `through` boundary. | `tests/test_landing.py::test_through_rolls_hourly_files_to_upload_date` |
| For all partitions, re-landing an already-landed upload-date partition leaves `raw.events` content-identical and adds 0 net rows (idempotent, not table-recreate). | `tests/test_landing.py::test_double_land_partition_writes_zero_new_rows` |
| For all incremental BigQuery re-runs, the built tables are byte-identical to the full-scan build (the pruned source window is a superset of every row the full scan keeps). | `tests/integration/test_int_bigquery.py` (byte parity) + `make test-int-bigquery` |
| For all duplicate `insert_id`s, the two copies' `server_upload_time` span is within the prune window, so the dedupe sees both copies together (the earliest-copy rule is unchanged). | `tests/test_landing.py::test_duplicate_upload_span_within_lookback` |

Rules — the source-scan prune is upheld only in dbt SQL, which has no mutation
operator (BACKLOG); it names its dbt/integration test in the table instead. The
Python invariants get mutation lines:

```mutations
generator/writer.py::write_gzip_jsonl              delete-call
generator/cli.py::write_output                     swap-sort-key
landing/load.py::partition_overwrite_events        delete-call
landing/load.py::event_files                       invert-guard
landing/load.py::_file_date                        constant-return:'2026-01-04'
```

(Function names are the planned shape; the mutation block is reconciled to the
landed functions during implementation — `make mutate` runs at review, after the
code exists. Each line, applied to HEAD, must turn the offline suite red:
dropping the gzip write empties the seed → manifest mismatch; swapping the
write_output group key mis-orders / mis-buckets events → golden drift; dropping
the partition delete → double-land duplicates rows; inverting the THROUGH guard
→ wrong file set; pinning `_file_date` to one date → THROUGH and partition
wrong.)

## Pinned decisions (do not re-litigate)

- **The writer emits gzipped hourly files, written with `gzip.GzipFile(mtime=0)`
  and a fixed compresslevel** — satisfies the reseed-byte-identical invariant and
  the §2.10 export-shape realism. Rejected: `gzip.open` at defaults (embeds an
  mtime → non-reproducible bytes → breaks the frozen manifest and the
  determinism policy); daily plain files (not the export shape item 6 names).
- **Filename `events_<YYYY-MM-DD>_<HH>.jsonl.gz`; `_file_date` returns the first
  10 characters, `event_files` globs `events_*.jsonl.gz`, THROUGH filters on the
  date** — satisfies the THROUGH-rolls-up invariant with the smallest change to
  the existing name-is-the-key predicate. Rejected: encoding the hour in the
  THROUGH predicate (item 6 partitions by upload DATE; the hour is packaging
  only); a manifest of loaded objects (a second source of truth off the name).
- **The events landing is partition-overwrite per upload-date partition, one
  layer up from the dbt models** — DuckDB delete-then-insert per
  `cast(server_upload_time as date)`; BigQuery a DAY-partitioned table loaded
  through the `raw.events$YYYYMMDD` partition decorator with `WRITE_TRUNCATE`;
  the table persists across loads — satisfies the append-only-idempotent
  invariant and mirrors the `partition_overwrite` strategy the incremental
  models already use. Rejected: insert-only skip-if-present (cannot absorb a
  date that later gains an hour; a re-land of a changed partition would be
  silently dropped); whole-table recreate / `WRITE_TRUNCATE` (the status quo the
  cost row indicts). `raw.dim_user` is the one seed file and stays a full replace
  each load — it has no upload-date partition.
- **`stg_events` bounds its `source('raw','events')` read to a superset
  upload-time window inside `is_incremental()` and `target.type == 'bigquery'`
  only** — `server_upload_time >= horizon_ts − (lookback_days + margin) days`,
  a window proven wide enough to include every row whose `event_date` is in the
  reprocess window AND to co-locate every duplicate — satisfies the
  prune-is-byte-identical and dedupe-not-split invariants. Rejected: pruning on
  DuckDB too (no partitions, no benefit, and it would risk the DuckDB goldens for
  nothing — DuckDB SQL stays unchanged, so its goldens are unchanged for free);
  a tight `− lookback_days` window (the cross-clock offset between `event_date`
  (client-local) and `server_upload_time` (server) can put an in-window row just
  below it — the margin is the safety, the parity run is the proof).
- **DuckDB THROUGH is monotonic-forward within a warehouse.** The append-only
  landing accumulates partitions; landing `≤ through` into a FRESH db yields
  exactly those files (a `drop-db` resets), and a backfill lands forward
  (Phase 7/8b go forward). Landing a smaller `through` after a larger one is not
  a supported reset (it appends, it does not remove out-of-scope partitions) —
  documented in the `make load` contract; every test lands forward. Rejected:
  computing and deleting out-of-scope partitions on every load (real append-only
  warehouses do not, and no path needs it).
- **Re-freeze `fixtures/tiny/raw` and move only the raw-structure pins.**
  `RAW_FILES` (10 → the hourly-gzip count) and any `_file_date` / file-count
  assertion are re-read off the first green freeze; `RAW_EVENT_ROWS`,
  `STG_*`, the label counts, MAE/coverage, `HOLDOUT_*` and every golden hash are
  unchanged (the warehouse is unchanged). Rejected: rewriting any warehouse pin
  by hand (a drift there is a red test, never a rewritten constant).

## Scope (files)

- `generator/writer.py` — the gzip writer (`write_gzip_jsonl`; the streaming
  `JsonlAppender` → a gzip appender), `mtime=0`, still refuses `fixtures/`.
- `generator/cli.py` — `write_output` / `write_output_streaming` group events by
  `(upload date, hour)` and write `.jsonl.gz`.
- `landing/load.py` — `event_files` glob + `_file_date` slice; the partition-
  overwrite events landing (table persists; per-date delete+insert); gz read.
- `landing/bq.py` — DAY-partition config on `raw.events`; per-partition load
  through the partition decorator; gz source format.
- `dbt/models/staging/stg_events.sql` — the superset source-scan predicate under
  `is_incremental()` + `target.type == 'bigquery'`.
- `tests/pins.py` — raw-structure pins only (`RAW_FILES`, file-count assertions).
- `tests/test_generator.py`, `tests/test_landing.py`, `tests/integration/
  test_int_bigquery.py` — the new invariant tests + parity.
- `fixtures/tiny/raw/*.jsonl.gz`, `fixtures/tiny/MANIFEST.sha256` — the re-freeze
  (via `make freeze`).

## Record updates (REQUIRED)

- [ ] `DECISIONS.md` — the `fix/append-landing` entry (append-only partitioned
      landing; hourly gzip; the superset source-scan prune; each alternative)
- [ ] `CLAUDE.md` — Current status; Repo map (`landing/`, `generator/writer.py`
      lines); the `make load` / `make bq-load` / `make dbt-build` command docs
      (append-only, partition-overwrite, gzip hourly, THROUGH is a partition
      predicate on BigQuery); Event model facts if the file-unit sentence moves;
      BACKLOG count
- [ ] `docs/ARCHITECTURE.md` — §2.1/§2.10 (the export file unit is a gzipped
      hourly file rolling up to an upload-date partition); §2.7 (the source-scan
      prune and its dedupe-superset condition); §3.1/§3.3 (the landing writes
      per-partition, not table-recreate); §8 Gotchas (gzip determinism; the
      prune's cross-clock margin; the multi-warehouse-build note already covers
      isolation)
- [ ] `docs/RESULTS.md` — the "Large profile" section gains the measured pruned
      re-run bytes (hand-filled, beside the 19.45 GB full-scan line it fixes)
- [ ] `docs/ROADMAP.md` — item 6 marked landed with the "as landed" note
- [ ] `BACKLOG.md` — strike **"the incremental re-run re-scans all of raw"**
      (item 6) with "DONE fix/append-landing"; open any deferred finding
- [ ] `PHASES.md` — none (a `fix/` branch does not touch the phase plan)
- [ ] `README.md` — none (no demo or first-screen number changes)
- [ ] `METRICS.md` / `DEPLOYMENT.md` / `AB_DESIGN.md` — none

## Threat model (REQUIRED)

None — no new Makefile target takes a variable, deletes, touches cloud, or reads
input. `make load` / `make bq-load` / `make dbt-build` keep their existing
variable handling (PROFILE `[a-z0-9_]+`, THROUGH an `YYYY-MM-DD` upload date
validated in Python, PROJECT a GCP project-id, CONFIRM `$(origin)`-gated on the
cloud targets); this branch changes what they DO with an already-validated
THROUGH (a partition predicate, not a new input), not how they validate it. The
existing `tests/test_makefile.py` cells for these targets are unchanged. The
partition decorator `raw.events$YYYYMMDD` is derived from the validated file
name inside Python (the date is `_file_date`, `\d{4}-\d{2}-\d{2}`), never a
user string — pinned in `tests/test_landing.py`.

## Review & stack risk

- **code-reviewer** (triggered — `generator/`, `landing/`, `dbt/**`,
  `tests/`, `fixtures/`): determinism (gzip mtime=0; the reseed manifest match);
  truth isolation (the landing still names no truth); the dedupe invariant
  (earliest-copy unchanged); the source-scan predicate is a superset, gated to
  bigquery; read-only fixtures (only `make freeze` writes, with the `Freeze:`
  line); the partition-overwrite mirrors the dispatch macro's semantics.
- **security-reviewer** (mandatory — the BigQuery load path and the
  `CONFIRM`/cloud `TARGET` surface are touched): the partition decorator is
  derived from a validated date, not interpolated user input; no credential
  reaches a file/log; the cloud-cost gates are unchanged.
- **functionality-tester** (triggered): the DONE command + the negative tests —
  double-land 0 rows, THROUGH roll-up equivalence, dedupe-straddle span, gzip
  reseed identity; confirms every named Evidence test exists.
- **coherence-auditor** at exit (mandatory, `fix/` branch exit): the stale
  sentences gone — "one plain daily `events_<date>.jsonl`", "tables recreated",
  "`WRITE_TRUNCATE`s the whole table", "raw is unpartitioned so the re-run
  re-scans all of raw"; the §2.10 file-unit and §2.7 prune clauses land; the
  Record-updates list matches the diff; Spanner/Composer clean at exit.
- **Stack risk (verify in the first hour, STOP + report before any workaround;
  findings → ARCHITECTURE §8):** (a) gzip byte-reproducibility across the
  platform — `mtime=0` and a fixed compresslevel must reproduce the frozen
  manifest on this machine and in CI; (b) DuckDB `read_json` over `.gz` (auto
  vs explicit `compression='gzip'`); (c) the BigQuery partition-decorator load
  through the injectable `Clients` factory — the offline fake must exercise the
  same per-partition call shape (adapter contract — a fake stands under the
  thinnest adapter, tested on the call shape it wraps); (d) the cross-clock
  margin width — proven only by the live byte-parity run, so the margin is
  generous by design.

## Out of scope (deferred, recorded)

- The append-only landing on the **Spanner** dims target — `dim_user` is the one
  SCD2 seed, not upload-date-partitioned; unchanged (BACKLOG if a case appears).
- Finer-than-day partition pruning / clustering on `raw.events` — the day
  partition closes the measured cost row; a clustering pass is a later BACKLOG
  row if a number motivates it.
- Composer running the append landing on a schedule — ROADMAP item 7
  (`fix/composer-cosmos`) shapes the image that would; this branch only makes the
  landing append-only.
