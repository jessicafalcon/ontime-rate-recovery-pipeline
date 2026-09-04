"""Invariant 1 (byte-identical, arrival order), 4 (events consistent with the
assigned cause), 6 (seed reports drift and exits 1; freeze writes the fixture),
7 (seed never writes under fixtures/)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from generator import cli, manifest, profiles, truth, writer
from generator.generate import _partition, arrival_order, generate, iter_shards, prepare
from generator.models import SKEW_MAX_MIN, Cause, Event, EventType
from tests._gen import by_prompt, gen, tiny, types

ROOT = Path(__file__).parent.parent


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    """Reseed byte-identity (invariant 1), now over the gzipped raw: gzip is
    written mtime=0 + fixed level + no embedded name, so the bytes reproduce."""
    a, b = tmp_path / "a", tmp_path / "b"
    cli.write_output(a, gen())
    cli.write_output(b, gen())
    assert manifest.compute(a) == manifest.compute(b)
    for rel in manifest.compute(a):
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_raw_files_are_hourly_gzip(tmp_path: Path) -> None:
    """Done-when 1: raw lands as events_<date>_<HH>.jsonl.gz and nothing else;
    every event in a file falls on the file's named upload date and hour."""
    import gzip
    import json
    import re

    cli.write_output(tmp_path, gen())
    raw = sorted((tmp_path / "raw").glob("*"))
    pat = re.compile(r"^events_\d{4}-\d{2}-\d{2}_\d{2}\.jsonl\.gz$")
    assert raw and all(pat.match(f.name) for f in raw), [f.name for f in raw]
    for f in raw:
        date, hour = f.name[len("events_") : -len(".jsonl.gz")].split("_")
        for ln in gzip.open(f, "rt"):
            ut = json.loads(ln)["server_upload_time"]
            assert ut[:10] == date and ut[11:13] == hour, (f.name, ut)


def test_duplicate_upload_span_bounded() -> None:
    """fix/append-landing invariant 5: `inject_duplicates` offsets a copy by
    `_secs(rng, 0, 3600)`, so a duplicate's two copies span < 1 h in
    `server_upload_time` for EVERY seed (not just tiny's fixture). The
    source-scan prune margin exceeds this, so a duplicate is never split across
    the pruned window and the earliest-copy dedupe is unchanged."""
    from collections import defaultdict

    base = profiles.load("tiny")
    for s in range(base.seed, base.seed + 8):
        out = generate(base.model_copy(update={"seed": s}))
        by_id: dict[str, list] = defaultdict(list)
        for ev in out.events:
            by_id[ev.insert_id].append(ev.server_upload_time)
        dups = {k: v for k, v in by_id.items() if len(v) > 1}
        assert dups, f"seed {s}: no duplicate to check"
        for k, times in dups.items():
            span = (max(times) - min(times)).total_seconds()
            assert span < 3600, (s, k, span)


def test_two_processes_under_different_tz_are_byte_identical(tmp_path: Path) -> None:
    code = (
        "import sys; from pathlib import Path; from generator import cli, profiles; "
        "from generator.generate import generate; "
        "cli.write_output(Path(sys.argv[1]), generate(profiles.load('tiny')))"
    )
    for tz in ("UTC", "America/New_York"):
        subprocess.run(
            [sys.executable, "-c", code, str(tmp_path / tz)],
            cwd=ROOT,
            env={**os.environ, "TZ": tz, "PYTHONHASHSEED": "0" if tz == "UTC" else "7"},
            check=True,
        )
    assert manifest.compute(tmp_path / "UTC") == manifest.compute(
        tmp_path / "America/New_York"
    )


def _ev(insert_id: str, upload: datetime) -> Event:
    t = datetime(2026, 1, 5, tzinfo=UTC)
    return Event(
        insert_id=insert_id,
        event_type=EventType.app_opened,
        user_id="u-000001",
        device_id="d-000001",
        client_event_time=t,
        server_received_time=t,
        server_upload_time=upload,
        event_properties={},
    )


def test_emit_order_is_arrival_order_with_insert_id_tiebreak() -> None:
    t = datetime(2026, 1, 5, 1, tzinfo=UTC)
    evs = [_ev("e-3", t), _ev("e-1", t + timedelta(hours=1)), _ev("e-2", t)]
    assert [e.insert_id for e in arrival_order(evs)] == ["e-2", "e-3", "e-1"]
    assert [e.insert_id for e in arrival_order(list(reversed(evs)))] == [
        "e-2",
        "e-3",
        "e-1",
    ]


def _delay_min(e: Event) -> float:
    return (e.server_received_time - e.client_event_time).total_seconds() / 60


def test_events_are_consistent_with_assigned_cause() -> None:
    out = gen()
    groups = by_prompt(out.events)
    seen: set[Cause] = set()
    for pc in out.prompt_causes:
        evs = groups[pc.prompt_id]
        seen.add(pc.cause)
        sent = next(e for e in evs if e.event_type is EventType.prompt_sent)
        window_end = sent.client_event_time + timedelta(
            minutes=sent.event_properties["window_minutes"]
        )
        t = types(evs)
        if pc.cause is Cause.delivery_fault:
            assert t == {"prompt_sent"}
            continue
        assert "prompt_delivered" in t
        if pc.cause is Cause.timing_gap:
            assert t == {"prompt_sent", "prompt_delivered"}
        elif pc.cause is Cause.on_time:
            resp = next(e for e in evs if e.event_type is EventType.response_recorded)
            assert resp.client_event_time <= window_end
            assert resp.server_received_time <= window_end
            assert all(-SKEW_MAX_MIN <= _delay_min(e) <= SKEW_MAX_MIN for e in evs)
        elif pc.cause is Cause.upload_fault:
            cap = next(e for e in evs if e.event_type is EventType.capture_started)
            assert cap.client_event_time <= window_end
            if "response_recorded" in t:
                resp = next(
                    e for e in evs if e.event_type is EventType.response_recorded
                )
                assert resp.server_received_time > window_end
            else:
                assert "upload_failed" in t and "upload_completed" not in t
        else:
            assert pc.cause is Cause.unattributed
            assert any(_delay_min(e) < -SKEW_MAX_MIN for e in evs)
    assert seen == set(Cause)  # tiny exercises every cause


def test_seed_never_writes_under_fixtures(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(writer.FixtureWriteRefused):
        writer.write_jsonl(ROOT / "fixtures" / "tiny" / "raw" / "x.jsonl", [])
    with pytest.raises(writer.FixtureWriteRefused):
        writer.write_csv(ROOT / "fixtures" / "x" / "dims" / "d.csv", [])
    frozen = ROOT / "fixtures" / "tiny"
    before = manifest.compute(frozen)
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    assert cli.seed("tiny") == 0
    assert manifest.compute(frozen) == before
    assert (tmp_path / "out" / "tiny" / "raw").is_dir()


def test_seed_reports_drift_and_exits_1(tmp_path: Path, monkeypatch, capsys) -> None:
    """seed against a manifest that disagrees with regenerated output exits 1 (the
    no-silent-drift guarantee) — the failure path the matching case can't reach."""
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli, "ROOT", tmp_path)  # only for the print's relative_to
    frozen = tmp_path / "fix" / "tiny" / manifest.NAME
    frozen.parent.mkdir(parents=True)
    frozen.write_text(manifest.render({"raw/events_2026-01-05.jsonl": "0" * 64}))
    assert cli.seed("tiny") == 1
    assert "seed DRIFT" in capsys.readouterr().out


def test_freeze_copies_out_to_fixtures_and_writes_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """freeze's success path copies data/out/<p>/ over fixtures/<p>/ and writes a
    manifest the fixture then matches — the write path no refusal test reaches."""
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli, "ROOT", tmp_path)  # only for the print's relative_to
    assert cli.seed("tiny") == 0  # no frozen manifest yet → nothing to drift against
    assert cli.freeze("tiny", "yes", "command line") == 0
    dst = tmp_path / "fix" / "tiny"
    assert (dst / manifest.NAME).exists()
    assert manifest.matches(dst, dst / manifest.NAME)


# ------------------------------------------- fix/large-profile: sharded streams


def test_partition_is_contiguous_and_covers_every_user() -> None:
    """Blocks are contiguous, in order, lose no user, and are near-equal —
    so the (seed, shard) partition is a pure function of `users` and `shards`."""
    users = [f"u-{i:06d}" for i in range(1, 21)]
    for shards in (1, 2, 3, 7, 20):
        blocks = _partition(users, shards)
        assert len(blocks) == shards
        assert [u for b in blocks for u in b] == users
        sizes = [len(b) for b in blocks]
        assert max(sizes) - min(sizes) <= 1


def test_prompt_count_is_invariant_to_shard_count() -> None:
    """Sharding repartitions the streams; it never adds or drops a prompt×user.
    Every cause is still exercised, and there is one latent user per user."""
    for shards in (1, 2, 5):
        out = generate(tiny(shards=shards))
        assert len(out.prompt_causes) == 20 * 7
        assert len(out.latent_users) == 20
        assert {c.cause for c in out.prompt_causes} == set(Cause)


def test_streaming_write_equals_in_memory_at_two_shards(tmp_path: Path) -> None:
    """The memory-bounded streaming writer is byte-for-byte the in-memory
    writer at shards > 1: shard-major, arrival order within a shard."""
    p = tiny(shards=2)
    a, b = tmp_path / "mem", tmp_path / "stream"
    cli.write_output(a, generate(p))
    cli.write_output_streaming(b, p)
    assert manifest.compute(a) == manifest.compute(b)
    for rel in manifest.compute(a):
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_sharded_run_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Invariant 1 holds under sharding: two streaming runs of a 3-shard
    profile are byte-identical (derived seeds, no clock, sorted iteration)."""
    p = tiny(shards=3)
    a, b = tmp_path / "a", tmp_path / "b"
    cli.write_output_streaming(a, p)
    cli.write_output_streaming(b, p)
    assert manifest.compute(a) == manifest.compute(b)


def test_streaming_at_one_shard_reproduces_the_frozen_generator_keys(
    tmp_path: Path,
) -> None:
    """At shards == 1 the streaming path reproduces the frozen generator keys
    too, so the seed() branch (in-memory vs streaming) is a memory choice, not
    a behaviour fork."""
    cli.write_output_streaming(tmp_path, profiles.load("tiny"))
    assert (
        cli.generated_drift(tmp_path, ROOT / "fixtures" / "tiny" / manifest.NAME) == []
    )


def test_shards_draw_independent_streams() -> None:
    """The `(seed + s·P_SHARD)` offset must actually decorrelate the shards. Under
    a broken offset (P_SHARD == 0) the two equal half-blocks would draw the SAME
    sequence, so aligned users across shards would share cohort choice and gauss —
    identical latent centres position-by-position. This catches that."""
    p = tiny(shards=2)  # 20 users → blocks [0:10], [10:20], aligned
    shards = list(iter_shards(p, prepare(p)))
    assert len(shards) == 2
    c0 = [lu.reachable_center_local_hour for lu in shards[0].latent_users]
    c1 = [lu.reachable_center_local_hour for lu in shards[1].latent_users]
    assert c0 != c1  # equal lists ⇒ a shared stream ⇒ the offset did nothing


def test_streaming_writers_refuse_fixtures() -> None:
    """`JsonlAppender` and `TruthStream` carry the same fixtures refusal as
    `write_jsonl`/`write_csv` — `make freeze` is the only writer under fixtures/."""
    with pytest.raises(writer.FixtureWriteRefused):
        writer.JsonlAppender(ROOT / "fixtures" / "tiny" / "raw" / "x.jsonl")
    with pytest.raises(writer.FixtureWriteRefused):
        truth.TruthStream(ROOT / "fixtures" / "tiny")


def test_large_profile_shards_and_partitions_cleanly() -> None:
    """The committed `large` profile is multi-shard and partitions its users
    with no loss (a small guard that its knobs stay coherent)."""
    p = profiles.load("large")
    assert p.shards > 1
    users = [f"u-{i:06d}" for i in range(1, p.users + 1)]
    blocks = _partition(users, p.shards)
    assert sum(len(b) for b in blocks) == p.users
    assert all(b for b in blocks)  # no empty shard (shards <= users)


# ------------------------------------------------- Phase 3: expected/ via freeze


def test_seed_self_check_ignores_expected_keys(tmp_path: Path, monkeypatch, capsys):
    """Invariant 11: the manifest lists expected/attribution.csv (Phase 3) but
    `seed` never writes it — the self-check covers raw/, dims/, truth/ only."""
    real = ROOT / "fixtures" / "tiny" / manifest.NAME
    assert "expected/attribution.csv" in real.read_text()
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    assert cli.seed("tiny") == 0
    assert "manifest match" in capsys.readouterr().out
    assert cli.generated_keys({"expected/a.csv": "x", "raw/e.jsonl": "y"}) == {
        "raw/e.jsonl": "y"
    }
    # a generated key that drifts is still a drift
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    frozen = tmp_path / "fix" / "tiny" / manifest.NAME
    frozen.parent.mkdir(parents=True)
    lines = manifest.parse(real.read_text())
    lines["truth/prompts.jsonl"] = "0" * 64
    frozen.write_text(manifest.render(lines))
    assert cli.seed("tiny") == 1
    assert "truth/prompts.jsonl: changed" in capsys.readouterr().out


def test_freeze_refuses_when_output_lacks_a_manifest_file(tmp_path: Path, monkeypatch):
    """Invariant 11: a freeze after a bare `seed` would drop expected/ from the
    fixture — refused, and the fixture is untouched."""
    monkeypatch.setattr(cli, "DATA_OUT", tmp_path / "out")
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fix")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.seed("tiny") == 0
    assert cli.freeze("tiny", "yes", "command line") == 0
    dst = tmp_path / "fix" / "tiny"
    (tmp_path / "out" / "tiny" / "expected").mkdir()
    (tmp_path / "out" / "tiny" / "expected" / "attribution.csv").write_text("h\n")
    assert cli.freeze("tiny", "yes", "command line") == 0
    before = (dst / manifest.NAME).read_text()
    assert "expected/attribution.csv" in before
    shutil.rmtree(tmp_path / "out" / "tiny" / "expected")
    assert cli.missing_from_output(tmp_path / "out" / "tiny", dst / manifest.NAME) == [
        "expected/attribution.csv"
    ]
    with pytest.raises(SystemExit) as e:
        cli.freeze("tiny", "yes", "command line")
    assert e.value.code == 2
    assert (dst / manifest.NAME).read_text() == before
    assert (dst / "expected" / "attribution.csv").read_text() == "h\n"
