-- Hand-written serving DDL (ARCHITECTURE §2.9). NOT generated from
-- generator/models.py: send_schedule is a SERVING contract, not the event schema
-- the generator owns, and it is upserted — `create table if not exists`, never
-- `create or replace` (the table must persist across write-back runs for
-- idempotence). Phase 10 swaps this DuckDB table for a Spanner table behind the
-- write-back TARGET flag (§3.3). The nine columns are §2.9's, in order.
create schema if not exists serving;
create table if not exists serving.send_schedule (
    user_id varchar primary key,
    cohort_id varchar not null,
    send_hour_local integer not null,
    send_minute_local integer not null,
    tz varchar not null,
    confidence double not null,
    model_version varchar not null,
    computed_as_of timestamp not null,
    written_at timestamp not null
);
