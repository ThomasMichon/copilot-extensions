"""Finite-work, revision, interruption and scalar-only transcript checkpoints."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from agent_logger import usage_capture, usage_progress, usage_transcript
from agent_logger.sync.publication_directory import PublicationDirectory
from agent_logger.usage_ledger import ARTIFACT, UsageEvidenceError
from agent_logger.usage_progress import read_transcript_progress


def _usage(tokens=1, *, native=True):
    value = {
        "type": "session.usage_record", "timestamp": "2026-01-01T00:00:00Z",
        "data": {"usage": {"model": "test-model", "inputTokens": tokens}},
    }
    if native:
        value["id"] = f"event-{tokens}"
    return json.dumps(value).encode() + b"\n"


def _session(tmp_path, content):
    session = tmp_path / "source" / "session-state" / "session"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_bytes(content)
    return session


def _finish(session, cache, *, byte_budget=512, event_budget=2):
    previous = -1
    for _ in range(100):
        result = read_transcript_progress(
            session, session.name, cache, byte_budget=byte_budget, event_budget=event_budget,
        )
        assert result.consumed <= byte_budget
        assert result.offset > previous
        previous = result.offset
        if result.complete:
            return result
    pytest.fail("finite source did not converge within its measured budgets")


def test_resumption_matches_reference_multiplicity_and_skips_unchanged_reads(tmp_path, monkeypatch):
    content = _usage(1, native=False) * 5 + _usage(2) * 3
    session = _session(tmp_path, content)
    cache = tmp_path / "cache"
    result = _finish(session, cache)
    assert len(result.records) == 2
    counts = {row["values"]["input_tokens"]: row["multiplicity"] for row in result.records}
    assert counts == {1: 5, 2: 1}
    assert not (session / ARTIFACT).exists()
    real_open = PublicationDirectory.open

    class NoReads:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            if name in {"read", "readline"}:
                pytest.fail("unchanged completed transcript was read again")
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    def checked_open(directory, name, **kwargs):
        stream = real_open(directory, name, **kwargs)
        return NoReads(stream) if name == "events.jsonl" else stream

    monkeypatch.setattr(PublicationDirectory, "open", checked_open)
    repeated = read_transcript_progress(session, session.name, cache)
    assert repeated.complete and repeated.consumed == 0
    assert repeated.records == result.records


def test_actual_reads_stay_inside_byte_and_event_budgets(tmp_path, monkeypatch):
    session = _session(tmp_path, _usage(1, native=False) * 12)
    real_open = PublicationDirectory.open
    reads = []

    class CountReads:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def readline(self, limit):
            data = self.stream.readline(limit)
            reads.append(len(data))
            return data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    def counted_open(directory, name, **kwargs):
        stream = real_open(directory, name, **kwargs)
        return CountReads(stream) if name == "events.jsonl" else stream

    monkeypatch.setattr(PublicationDirectory, "open", counted_open)
    result = read_transcript_progress(
        session, session.name, tmp_path / "cache", byte_budget=512, event_budget=2,
    )
    assert not result.complete
    assert sum(reads) == result.consumed <= 512
    assert len(reads) == 2


@pytest.mark.parametrize("mutation", ["append", "rewrite", "replace", "truncate", "rewrite-append"])
def test_changed_generation_is_revalidated_from_zero(tmp_path, mutation):
    session = _session(tmp_path, _usage(1) * 4)
    cache = tmp_path / "cache"
    initial = read_transcript_progress(session, session.name, cache, event_budget=1)
    assert not initial.complete and initial.offset == len(_usage(1))
    events = session / "events.jsonl"
    if mutation == "append":
        events.write_bytes(_usage(1) * 4 + _usage(2))
        expected = {1, 2}
    elif mutation == "replace":
        replacement = session / "new"
        replacement.write_bytes(_usage(2) * 4)
        replacement.replace(events)
        expected = {2}
    else:
        payload = _usage(2) * (1 if mutation == "truncate" else 4)
        if mutation == "rewrite-append":
            payload += _usage(3)
        events.write_bytes(payload)
        expected = {2, 3} if mutation == "rewrite-append" else {2}
    result = _finish(session, cache)
    assert {row["values"]["input_tokens"] for row in result.records} == expected


def test_complete_cache_revalidates_appends_and_preserves_no_conversation_content(tmp_path):
    private = b'{"type":"assistant.message","data":{"content":"private-conversation-marker"}}\n'
    session = _session(tmp_path, private + _usage(1))
    cache = tmp_path / "cache"
    assert _finish(session, cache).complete
    with (session / "events.jsonl").open("ab") as stream:
        stream.write(_usage(2))
    result = _finish(session, cache)
    assert {row["values"]["input_tokens"] for row in result.records} == {1, 2}
    for path in cache.glob("*.json"):
        assert b"private-conversation-marker" not in path.read_bytes()
        assert b"assistant.message" not in path.read_bytes()


def test_corrupt_cursor_rebuilds_without_skipping_unverified_bytes(tmp_path):
    session = _session(tmp_path, _usage(1) + _usage(2))
    cache = tmp_path / "cache"
    first = read_transcript_progress(session, session.name, cache, event_budget=1)
    assert not first.complete
    cursor = next(cache.glob("*.json"))
    payload = json.loads(cursor.read_bytes())
    payload["offset"] = (session / "events.jsonl").stat().st_size
    cursor.write_text(json.dumps(payload))
    rebuilt = read_transcript_progress(session, session.name, cache, event_budget=1)
    assert rebuilt.rebuilt and not rebuilt.complete
    assert rebuilt.consumed == len(_usage(1))
    assert len(_finish(session, cache).records) == 2


def test_failed_checkpoint_replace_preserves_prior_progress_and_retries_exactly(tmp_path, monkeypatch):
    session = _session(tmp_path, _usage(1, native=False) * 3)
    cache = tmp_path / "cache"
    assert not read_transcript_progress(session, session.name, cache, event_budget=1).complete
    cursor = next(cache.glob("*.json"))
    prior = cursor.read_bytes()
    real_replace = PublicationDirectory.replace

    def interrupted(directory, temporary, destination):
        if directory.path == cache:
            raise OSError("simulated interruption")
        return real_replace(directory, temporary, destination)

    with monkeypatch.context() as patch:
        patch.setattr(PublicationDirectory, "replace", interrupted)
        with pytest.raises(OSError, match="simulated interruption"):
            read_transcript_progress(session, session.name, cache, event_budget=1)
    assert cursor.read_bytes() == prior
    assert not list(cache.glob("*.tmp"))
    result = _finish(session, cache)
    assert result.records[0]["multiplicity"] == 3


@pytest.mark.parametrize("tail", [b"{broken}\n", b"[]\n", b'{"value":NaN}\n', b"[" * 2000 + b"\n"])
def test_invalid_events_do_not_advance_last_good_cursor(tmp_path, tail):
    session = _session(tmp_path, _usage(1) + tail)
    cache = tmp_path / "cache"
    assert not read_transcript_progress(session, session.name, cache, event_budget=1).complete
    cursor = next(cache.glob("*.json"))
    prior = cursor.read_bytes()
    with pytest.raises(UsageEvidenceError):
        read_transcript_progress(session, session.name, cache)
    assert cursor.read_bytes() == prior


def test_partial_tail_is_deferred_and_later_completion_is_revalidated(tmp_path):
    session = _session(tmp_path, _usage(1) + _usage(2).rstrip(b"\n"))
    cache = tmp_path / "cache"
    partial = read_transcript_progress(session, session.name, cache)
    assert not partial.complete and partial.reason == "partial tail"
    with (session / "events.jsonl").open("ab") as stream:
        stream.write(b"\n")
    assert len(_finish(session, cache).records) == 2


def test_mutation_during_capture_rejects_new_progress(tmp_path, monkeypatch):
    session = _session(tmp_path, _usage(1))
    original = usage_transcript.project_usage_line

    def changing(line, sid):
        with (session / "events.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
        return original(line, sid)

    monkeypatch.setattr(usage_transcript, "project_usage_line", changing)
    with pytest.raises(UsageEvidenceError, match="changed during"):
        read_transcript_progress(session, session.name, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.json"))


def test_global_budget_is_charged_even_when_individual_sessions_fail(tmp_path, monkeypatch):
    for index in range(4):
        session = tmp_path / "session-state" / f"session-{index}"
        session.mkdir(parents=True)
        (session / "events.jsonl").write_bytes(b"x" * 20)
    monkeypatch.setattr(usage_capture, "MAX_CAPTURE_BYTES", 32)
    monkeypatch.setattr(usage_transcript, "MAX_LINE_BYTES", 8)
    result = usage_capture.capture_usage(tmp_path, None, cache_root=tmp_path / "cache")
    assert len(result.failures) == 3
    assert len(result.deferred) == 1
    assert not list((tmp_path / "session-state").glob(f"*/{ARTIFACT}"))


def test_missing_transcript_cannot_reuse_complete_cache(tmp_path):
    session = _session(tmp_path, _usage(1))
    cache = tmp_path / "cache"
    assert _finish(session, cache).complete
    (session / "events.jsonl").unlink()
    with pytest.raises(FileNotFoundError):
        read_transcript_progress(session, session.name, cache)


def test_oversized_usage_projection_is_not_cached(tmp_path, monkeypatch):
    session = _session(tmp_path, _usage(1))
    monkeypatch.setattr(usage_progress, "MAX_BYTES", 10)
    with pytest.raises(UsageEvidenceError, match="byte limit"):
        read_transcript_progress(session, session.name, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.json"))


def test_conflicting_observation_does_not_advance_checkpoint(tmp_path):
    conflicting = json.loads(_usage(2))
    conflicting["id"] = "event-1"
    session = _session(tmp_path, _usage(1) + json.dumps(conflicting).encode() + b"\n")
    cache = tmp_path / "cache"
    assert not read_transcript_progress(session, session.name, cache, event_budget=1).complete
    cursor = next(cache.glob("*.json"))
    prior = cursor.read_bytes()
    with pytest.raises(UsageEvidenceError, match="conflicting"):
        read_transcript_progress(session, session.name, cache)
    assert cursor.read_bytes() == prior


def test_checkpoint_restarts_in_new_process_without_double_counting(tmp_path):
    session = _session(tmp_path, _usage(1, native=False) * 3)
    cache = tmp_path / "cache"
    code = """
import json, sys
from pathlib import Path
from agent_logger.usage_progress import read_transcript_progress
session, cache = map(Path, sys.argv[1:])
result = read_transcript_progress(session, session.name, cache, event_budget=2)
print(json.dumps({"complete": result.complete, "consumed": result.consumed,
                  "multiplicity": result.records[0]["multiplicity"]}))
"""
    outcomes = []
    for _ in range(3):
        result = subprocess.run(
            [sys.executable, "-c", code, str(session), str(cache)],
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        outcomes.append(json.loads(result.stdout))
    assert [result["complete"] for result in outcomes] == [False, True, True]
    assert [result["multiplicity"] for result in outcomes] == [2, 3, 3]
    assert outcomes[-1]["consumed"] == 0


def test_hard_interruption_temporary_is_reclaimed_under_writer_lock(tmp_path):
    session = _session(tmp_path, _usage(1))
    cache = tmp_path / "cache"
    assert _finish(session, cache).complete
    cursor = next(cache.glob("*.json"))
    temporary = cursor.with_name(cursor.name + ".tmp")
    temporary.write_bytes(b"interrupted write")
    result = read_transcript_progress(session, session.name, cache)
    assert result.complete and result.consumed == 0
    assert not temporary.exists()


def test_saturated_native_replays_have_linear_normalization_work(tmp_path, monkeypatch):
    from agent_logger import usage_ledger

    limit = 32
    replays = 300
    monkeypatch.setattr(usage_ledger, "MAX_RECORDS", limit)
    monkeypatch.setattr(usage_progress, "MAX_RECORDS", limit)
    session = _session(
        tmp_path, b"".join(_usage(index) for index in range(limit)) + _usage(0) * replays,
    )
    cache = tmp_path / "cache"
    assert not read_transcript_progress(session, session.name, cache, event_budget=limit).complete
    original = usage_ledger.normalize_record
    normalized = 0

    def counted(record, sid):
        nonlocal normalized
        normalized += 1
        return original(record, sid)

    monkeypatch.setattr(usage_ledger, "normalize_record", counted)
    result = read_transcript_progress(session, session.name, cache)
    assert result.complete and len(result.records) == limit
    assert all(row["multiplicity"] == 1 for row in result.records)
    assert normalized <= 2 * (limit + replays)


def test_shared_budget_rotates_past_persistently_failing_predecessors(tmp_path, monkeypatch):
    source = tmp_path / "source"
    for index in range(4):
        session = source / "session-state" / f"0{index}-bad"
        session.mkdir(parents=True)
        (session / "events.jsonl").write_bytes(b"x" * 512 + b"\n")
    healthy = source / "session-state" / "last-healthy"
    healthy.mkdir()
    (healthy / "events.jsonl").write_bytes(_usage(1))
    monkeypatch.setattr(usage_capture, "MAX_CAPTURE_BYTES", 512)
    monkeypatch.setattr(usage_transcript, "MAX_LINE_BYTES", 256)
    for _ in range(3):
        usage_capture.capture_usage(source, None, cache_root=tmp_path / "cache")
        if (healthy / ARTIFACT).exists():
            break
    assert (healthy / ARTIFACT).is_file(), "healthy later session was starved by failing predecessors"


def test_capture_order_reserves_fair_progress_before_reader_interruption(tmp_path):
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    sessions = ["one", "two", "three"]
    sessions.sort()
    with pytest.raises(RuntimeError, match="reader stopped"):
        with usage_progress.capture_order(
            cache, source, sessions, usage_progress.TranscriptBudget(512),
        ) as (ordered, rebuilt):
            assert ordered[0] == sessions[0] and not rebuilt
            raise RuntimeError("reader stopped")
    with usage_progress.capture_order(
        cache, source, sessions, usage_progress.TranscriptBudget(512),
    ) as (ordered, rebuilt):
        assert ordered[0] == sessions[1] and not rebuilt


def test_corrupt_capture_order_rebuilds_without_changing_session_scope(tmp_path):
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    with usage_progress.capture_order(
        cache, source, ["allowed"], usage_progress.TranscriptBudget(512),
    ):
        pass
    next(cache.glob("capture-*.json")).write_bytes(b"corrupt")
    with usage_progress.capture_order(
        cache, source, ["allowed"], usage_progress.TranscriptBudget(512),
    ) as (ordered, rebuilt):
        assert rebuilt and ordered == ["allowed"]
