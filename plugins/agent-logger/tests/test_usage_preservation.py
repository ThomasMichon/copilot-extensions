"""Synthetic accounting preservation and scheduled producer contracts."""

from __future__ import annotations

import json
import sqlite3
import tarfile
import zipfile
import weakref
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_logger.config import Config, load_config
from agent_logger.sync import engine
from agent_logger.sync.lock import sync_lock
from agent_logger.usage_capture import capture_usage
from agent_logger.usage_sqlite import UsageRowLimitError, read_sqlite_usage
from agent_logger.usage_ledger import (
    ARTIFACT, UsageEvidenceError, evidence_lock_path, merge_session_evidence,
)
from agent_logger.usage_transcript import read_transcript_usage


def _session(tmp_path: Path, sid: str = "s1") -> Path:
    session = tmp_path / "session-state" / sid
    session.mkdir(parents=True)
    event = {
        "id": "e1", "type": "session.usage_record", "timestamp": "2026-01-01T00:00:00Z",
        "data": {"usage": {
            "model": "model-a", "inputTokens": 10, "outputTokens": 2,
            "cacheReadTokens": 6, "apiCallId": "call-1",
            "accounting": {"sourceSessionId": sid, "sequence": 1, "usageId": "u1"},
            "copilotUsage": {"totalNanoAiu": 100, "tokenDetails": {"private": "excluded"}},
        }},
    }
    (session / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    return session


def _record(input_tokens: int | None = 10) -> dict:
    return {
        "source": "session-store", "session_id": "s1", "values": {
            "created_at": "2026-01-01T00:00:00Z", "model": "model-a",
            "input_tokens": input_tokens, "output_tokens": 2,
        },
    }


def _coverage() -> dict:
    return {"session-store": "captured", "transcript": "captured"}


def _db(source: Path) -> None:
    with closing(sqlite3.connect(source / "session-store.db")) as db, db:
        db.execute(
            "CREATE TABLE assistant_usage_events "
            "(session_id TEXT, created_at TEXT, model TEXT, input_tokens INTEGER, "
            "output_tokens INTEGER, token_details_json TEXT)"
        )
        db.execute(
            "INSERT INTO assistant_usage_events VALUES(?,?,?,?,?,?)",
            ("s1", "2026-01-01T00:00:00Z", "model-a", 10, 2, "never capture"),
        )


def _cfg(tmp_path: Path) -> Config:
    data = load_config(home=tmp_path / "home", include_repo=False).as_dict()
    data["sync"]["source"] = str(tmp_path / "source")
    data["sync"]["targets"]["local"]["path"] = str(tmp_path / "dest")
    data["sync"]["compact"] = {"enabled": False}
    data["machine"] = {"name": "synthetic"}
    return Config(data, tmp_path / "home")


def _large_store(source: Path, counts: dict[str, int]) -> dict[str, Path]:
    sessions = {sid: _session(source, sid) for sid in counts}
    _db(source)
    with closing(sqlite3.connect(source / "session-store.db")) as db, db:
        db.execute("DELETE FROM assistant_usage_events")
        for sid, count in counts.items():
            db.executemany(
                "INSERT INTO assistant_usage_events VALUES(?,?,?,?,?,?)",
                [(sid, "2026-01-01T00:00:00Z", "model-a", 10, 2, None)] * count,
            )
    return sessions


def test_usage_capture_splits_large_corpus_without_raising_reader_budget(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    counts = {f"s{index}": 6000 for index in range(4)}
    sessions = _large_store(cfg.sync_source, counts)
    with pytest.raises(UsageRowLimitError, match="row budget"):
        read_sqlite_usage(cfg.sync_source / "session-store.db", set(sessions))

    result = capture_usage(cfg.sync_source, None)

    assert result.complete
    assert result.updated == 4
    for sid, session in sessions.items():
        evidence = json.loads((session / ARTIFACT).read_text())
        stored = [row for row in evidence["records"] if row["source"] == "session-store"]
        assert sum(row["multiplicity"] for row in stored) == counts[sid]
    assert capture_usage(cfg.sync_source, None).updated == 0
    assert engine.run_sync(cfg) == 0
    for sid, session in sessions.items():
        published = tmp_path / "dest" / "synthetic" / "session-state" / sid / ARTIFACT
        assert published.read_bytes() == (session / ARTIFACT).read_bytes()


def test_usage_capture_retains_oversized_session_failure_and_captures_siblings(tmp_path: Path) -> None:
    sessions = _large_store(tmp_path, {"healthy": 2, "oversized": 20001})
    result = capture_usage(tmp_path, None)
    assert result.updated == 1
    assert result.failures == (
        "oversized: session-store capture: accounting store read exceeded row budget",
    )
    assert not (sessions["oversized"] / ARTIFACT).exists()
    assert (sessions["healthy"] / ARTIFACT).is_file()


def test_usage_split_releases_failed_reader_buffers_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger import usage_capture

    class DiscardedRows:
        pass

    live = weakref.WeakSet()
    calls = []

    def read_store(path: Path, session_ids: set[str]) -> dict:
        assert not live, "a failed reader's traceback still retains its row buffer"
        calls.append(len(session_ids))
        discarded = DiscardedRows()
        live.add(discarded)
        if len(session_ids) > 1:
            raise UsageRowLimitError("row budget")
        return {}

    for index in range(4):
        _session(tmp_path, f"s{index}")
    monkeypatch.setattr(usage_capture, "read_sqlite_usage", read_store)
    assert capture_usage(tmp_path, None).complete
    assert calls == [4, 2, 1, 1, 2, 1, 1]
    assert not live


@pytest.mark.parametrize("include", [None, {"s1"}])
def test_usage_ignores_internal_metadata_directories(tmp_path: Path, include) -> None:
    session = _session(tmp_path)
    _db(tmp_path)
    metadata = [
        tmp_path / "session-state" / name
        for name in (".agent-worktrees-locks", ".agent-worktrees-tmp", ".session-operation-locks")
    ]
    for directory in metadata:
        directory.mkdir()
        (directory / "sentinel").write_bytes(b"retained")

    result = capture_usage(tmp_path, include)

    assert result.complete
    assert result.updated == 1
    assert (session / ARTIFACT).is_file()
    for directory in metadata:
        assert list(directory.iterdir()) == [directory / "sentinel"]
        assert (directory / "sentinel").read_bytes() == b"retained"


def test_usage_invalid_session_identity_still_fails(tmp_path: Path) -> None:
    session = _session(tmp_path)
    (session.parent / "invalid session").mkdir()
    result = capture_usage(tmp_path, None)
    assert result.failures == ("session enumeration: invalid session directory identity",)
    assert not (session / ARTIFACT).exists()


def test_sync_with_internal_metadata_preserves_accounting_and_succeeds(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    _db(cfg.sync_source)
    (session.parent / ".session-operation-locks").mkdir()

    assert engine.run_sync(cfg) == 0
    published = tmp_path / "dest" / "synthetic" / "session-state" / "s1"
    assert (published / ARTIFACT).read_bytes() == (session / ARTIFACT).read_bytes()


def test_scheduled_capture_converges_above_total_transcript_limit(tmp_path: Path) -> None:
    from agent_logger.usage_capture import capture_for_sync
    from agent_logger.usage_transcript import MAX_TRANSCRIPT_BYTES

    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    _db(cfg.sync_source)
    usage = (session / "events.jsonl").read_bytes()
    padding = json.dumps({"type": "assistant.message", "data": {"content": "x" * 1024}}).encode() + b"\n"
    with (session / "events.jsonl").open("wb") as stream:
        for _ in range(MAX_TRANSCRIPT_BYTES // len(padding) + 1):
            stream.write(padding)
        stream.write(usage)
    assert (session / "events.jsonl").stat().st_size > MAX_TRANSCRIPT_BYTES

    assert capture_for_sync(cfg, None) == (False, False)
    assert not (session / ARTIFACT).exists()
    assert capture_for_sync(cfg, None) == (True, False)
    evidence = json.loads((session / ARTIFACT).read_text())
    transcript = [row for row in evidence["records"] if row["source"] == "transcript"]
    assert len(transcript) == 1 and transcript[0]["values"]["input_tokens"] == 10
    before = (session / ARTIFACT).read_bytes()
    assert capture_for_sync(cfg, None) == (True, False)
    assert (session / ARTIFACT).read_bytes() == before


def test_scheduled_capture_converges_above_total_event_limit(tmp_path: Path) -> None:
    from agent_logger.usage_capture import capture_for_sync
    from agent_logger.usage_transcript import MAX_EVENTS

    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    _db(cfg.sync_source)
    usage = (session / "events.jsonl").read_bytes()
    (session / "events.jsonl").write_bytes(b"{}\n" * MAX_EVENTS + usage)
    assert capture_for_sync(cfg, None) == (False, False)
    assert capture_for_sync(cfg, None) == (True, False)
    evidence = json.loads((session / ARTIFACT).read_text())
    assert len([row for row in evidence["records"] if row["source"] == "transcript"]) == 1


@pytest.mark.guard
def test_usage_transcript_allowlist_and_token_composition(tmp_path: Path) -> None:
    session = _session(tmp_path)
    records = read_transcript_usage(session, "s1")
    assert records[0]["values"]["input_tokens"] == 10  # already includes cache reads
    assert records[0]["values"]["cache_read_tokens"] == 6
    assert records[0]["values"]["total_nano_aiu"] == 100
    assert "tokenDetails" not in json.dumps(records)
    assert records[0]["values"]["usage_id"] == "u1"


@pytest.mark.guard
def test_usage_merge_is_idempotent_null_enriching_and_additive(tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert merge_session_evidence(session, "s1", [_record(None)], coverage=_coverage())
    assert merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    before = (session / ARTIFACT).stat().st_mtime_ns
    assert not merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    assert (session / ARTIFACT).stat().st_mtime_ns == before
    transcript = read_transcript_usage(session, "s1")
    merge_session_evidence(session, "s1", transcript, coverage=_coverage())
    payload = json.loads((session / ARTIFACT).read_text())
    assert len(payload["records"]) == 2
    assert {row["source"] for row in payload["records"]} == {"session-store", "transcript"}


def test_usage_merge_conflict_preserves_last_good(tmp_path: Path) -> None:
    session = _session(tmp_path)
    merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    before = (session / ARTIFACT).read_bytes()
    with pytest.raises(UsageEvidenceError, match="conflicting"):
        merge_session_evidence(session, "s1", [_record(11)], coverage=_coverage())
    assert (session / ARTIFACT).read_bytes() == before


def test_usage_multiplicity_survives_replay_and_shrinking_source(tmp_path: Path) -> None:
    session = _session(tmp_path)
    merge_session_evidence(session, "s1", [_record(), _record()], coverage=_coverage())
    merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    row = json.loads((session / ARTIFACT).read_text())["records"][0]
    assert row["multiplicity"] == 2


def test_usage_native_replays_do_not_multiply_observations(tmp_path: Path) -> None:
    session = _session(tmp_path)
    records = read_transcript_usage(session, "s1")
    merge_session_evidence(session, "s1", records * 2, coverage=_coverage())
    row = json.loads((session / ARTIFACT).read_text())["records"][0]
    assert row["multiplicity"] == 1


@pytest.mark.parametrize("codec", ["zip", "tar.gz"])
def test_usage_artifact_survives_archive_transparent_read(tmp_path: Path, codec: str) -> None:
    from agent_logger.sessions import SessionRef, read_member

    session = _session(tmp_path)
    capture_usage(tmp_path, {"s1"})
    expected = (session / ARTIFACT).read_bytes()
    archive = tmp_path / f"s1.{codec}"
    if codec == "zip":
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(session / ARTIFACT, ARTIFACT)
    else:
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(session / ARTIFACT, arcname=ARTIFACT)
    assert read_member(SessionRef("s1", "archive", archive), ARTIFACT) == expected


def test_usage_rejects_foreign_session(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(UsageEvidenceError, match="identity mismatch"):
        merge_session_evidence(
            session, "s1", [dict(_record(), session_id="other")], coverage=_coverage(),
        )


@pytest.mark.parametrize("field,value", [
    ("raw_token_details_json", "private"), ("input_tokens", True),
    ("output_tokens", -1), ("duration_ms", float("inf")),
])
def test_usage_rejects_invalid_or_private_values(tmp_path: Path, field: str, value: object) -> None:
    session = _session(tmp_path)
    record = _record()
    record["values"][field] = value
    with pytest.raises(UsageEvidenceError):
        merge_session_evidence(session, "s1", [record], coverage=_coverage())
    assert not (session / ARTIFACT).exists()


def test_usage_writer_contention_is_visible(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with sync_lock(evidence_lock_path(session), timeout=0):
        with pytest.raises(UsageEvidenceError, match="busy"):
            merge_session_evidence(session, "s1", [_record()], coverage=_coverage())


def test_usage_corrupt_artifact_is_not_reset(tmp_path: Path) -> None:
    session = _session(tmp_path)
    (session / ARTIFACT).write_bytes(b"{")
    with pytest.raises(UsageEvidenceError, match="corrupt"):
        merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    assert (session / ARTIFACT).read_bytes() == b"{"


@pytest.mark.parametrize("coverage", [1, [], {}, {"session-store": "green", "transcript": "captured"}])
def test_usage_invalid_prior_coverage_is_visible_without_reset(
    tmp_path: Path, coverage: object,
) -> None:
    session = _session(tmp_path)
    merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    payload = json.loads((session / ARTIFACT).read_text())
    payload["coverage"] = coverage
    (session / ARTIFACT).write_text(json.dumps(payload))
    prior = (session / ARTIFACT).read_bytes()
    result = capture_usage(tmp_path, {"s1"})
    assert result.failures
    assert (session / ARTIFACT).read_bytes() == prior


@pytest.mark.parametrize("tail", [b"{", b'{"type":"session.usage_record"}\n'])
def test_usage_partial_or_malformed_transcript_retains_artifact(tmp_path: Path, tail: bytes) -> None:
    session = _session(tmp_path)
    _db(tmp_path)
    assert capture_usage(tmp_path, {"s1"}).complete
    prior = (session / ARTIFACT).read_bytes()
    with (session / "events.jsonl").open("ab") as stream:
        stream.write(tail)
    result = capture_usage(tmp_path, {"s1"})
    assert result.failures
    assert (session / ARTIFACT).read_bytes() == prior


@pytest.mark.guard
def test_usage_scope_rebuild_missing_and_corrupt_store(tmp_path: Path) -> None:
    session = _session(tmp_path)
    excluded = _session(tmp_path, "excluded")
    _db(tmp_path)
    assert capture_usage(tmp_path, {"s1"}).complete
    assert not (excluded / ARTIFACT).exists()
    with closing(sqlite3.connect(tmp_path / "session-store.db")) as db, db:
        db.execute("DELETE FROM assistant_usage_events")
    assert capture_usage(tmp_path, {"s1"}).complete
    assert len(json.loads((session / ARTIFACT).read_text())["records"]) == 2
    # Simulate a temporarily missing shared store without deleting evidence.
    (tmp_path / "session-store.db").rename(tmp_path / "retained.db")
    assert capture_usage(tmp_path, {"s1"}).gaps
    prior = (session / ARTIFACT).read_bytes()
    (tmp_path / "session-store.db").write_bytes(b"corrupt")
    assert capture_usage(tmp_path, {"s1"}).failures
    assert (session / ARTIFACT).read_bytes() == prior


@pytest.mark.parametrize("created_at", [None, ""])
def test_usage_missing_timestamp_retains_prior_and_allows_raw_transfer(
    tmp_path: Path, created_at: str | None,
) -> None:
    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    _db(cfg.sync_source)
    assert engine.run_sync(cfg) == 0
    prior = (session / ARTIFACT).read_bytes()
    with closing(sqlite3.connect(cfg.sync_source / "session-store.db")) as db, db:
        db.execute("UPDATE assistant_usage_events SET created_at = ?", (created_at,))
    with (session / "events.jsonl").open("ab") as stream:
        stream.write(b'{"type":"test.raw-change"}\n')
    assert engine.run_sync(cfg) == 1
    assert (session / ARTIFACT).read_bytes() == prior
    copied = tmp_path / "dest" / "synthetic" / "session-state" / "s1" / "events.jsonl"
    assert copied.read_bytes() == (session / "events.jsonl").read_bytes()


@pytest.mark.parametrize("missing_store", [False, True])
def test_usage_store_coverage_uses_error_type_not_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_store: bool,
) -> None:
    from agent_logger import usage_capture
    from agent_logger.usage_sqlite import UsageReadError, UsageStoreMissingError

    session = _session(tmp_path)
    error = UsageStoreMissingError if missing_store else UsageReadError

    def fail_store(*args: object, **kwargs: object) -> dict:
        raise error("different wording" if missing_store else "accounting store is missing")

    monkeypatch.setattr(usage_capture, "read_sqlite_usage", fail_store)
    result = capture_usage(tmp_path, {"s1"})
    assert bool(result.gaps) is missing_store
    assert bool(result.failures) is not missing_store
    assert (session / ARTIFACT).exists() is missing_store


def test_usage_bounds_preserve_prior(tmp_path: Path, monkeypatch) -> None:
    from agent_logger import usage_ledger, usage_transcript

    session = _session(tmp_path)
    merge_session_evidence(session, "s1", [_record()], coverage=_coverage())
    prior = (session / ARTIFACT).read_bytes()
    monkeypatch.setattr(usage_ledger, "MAX_BYTES", 10)
    with pytest.raises(UsageEvidenceError, match="byte limit"):
        merge_session_evidence(session, "s1", [], coverage=_coverage())
    assert (session / ARTIFACT).read_bytes() == prior
    monkeypatch.setattr(usage_transcript, "MAX_LINE_BYTES", 20)
    with pytest.raises(UsageEvidenceError, match="limit"):
        read_transcript_usage(session, "s1")


@pytest.mark.parametrize("expression", [
    "zeroblob(8388608)", "printf('%.*c',8388608,'x')",
    "char(0) || printf('%.*c',8388608,'x')",
])
def test_usage_sqlite_bounds_values_before_python_materialization(
    tmp_path: Path, monkeypatch, expression: str,
) -> None:
    from agent_logger import usage_sqlite

    _session(tmp_path)
    _db(tmp_path)
    with closing(sqlite3.connect(tmp_path / "session-store.db")) as db, db:
        db.execute(f"UPDATE assistant_usage_events SET model = {expression}")
    original = usage_sqlite._validate_scalar

    def validate(column: str, value: object) -> None:
        assert not isinstance(value, (str, bytes)) or len(value) <= 2048
        original(column, value)

    monkeypatch.setattr(usage_sqlite, "_validate_scalar", validate)
    with pytest.raises(usage_sqlite.UsageReadError, match="invalid scalar"):
        usage_sqlite.read_sqlite_usage(tmp_path / "session-store.db", {"s1"})


@pytest.mark.guard
def test_usage_engine_capture_precedes_signatures_and_syncs_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    _db(cfg.sync_source)
    target = engine.build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    original_push = target.push
    pushed = []

    def push(*args, **kwargs):
        pushed.append(args[2])
        return original_push(*args, **kwargs)

    monkeypatch.setattr(target, "push", push)
    monkeypatch.setattr(engine, "build_target", lambda *a, **kw: target)
    assert engine.run_sync(cfg) == 0
    destination = tmp_path / "dest" / "synthetic" / "session-state" / "s1" / ARTIFACT
    assert destination.read_bytes() == (session / ARTIFACT).read_bytes()
    before = (session / ARTIFACT).stat().st_mtime_ns
    assert pushed == [{"s1"}]
    assert engine.run_sync(cfg) == 0
    assert (session / ARTIFACT).stat().st_mtime_ns == before
    assert pushed == [{"s1"}]


@pytest.mark.parametrize("field", ["duration", "cost"])
def test_usage_huge_numeric_record_retains_prior_and_allows_raw_transfer(
    tmp_path: Path, field: str,
) -> None:
    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    _db(cfg.sync_source)
    assert engine.run_sync(cfg) == 0
    prior = (session / ARTIFACT).read_bytes()
    transcript = session / "events.jsonl"
    event = json.loads(transcript.read_text())
    event["data"]["usage"][field] = 10**400
    transcript.write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert engine.run_sync(cfg) == 1
    assert (session / ARTIFACT).read_bytes() == prior
    copied = tmp_path / "dest" / "synthetic" / "session-state" / "s1" / "events.jsonl"
    assert copied.read_bytes() == transcript.read_bytes()


@pytest.mark.parametrize("store", ["missing", "corrupt", "deferred"])
def test_usage_incomplete_capture_still_delivers_existing_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: str,
) -> None:
    from agent_logger.sessions import get_codec
    from agent_logger.sync import compact

    cfg = _cfg(tmp_path)
    cfg._data["sync"]["compact"] = {"enabled": True}
    session = _session(cfg.sync_source)
    archived_session = _session(tmp_path / "old-source", "old")
    cfg.compact_archive_root.mkdir(parents=True)
    archive = cfg.compact_archive_root / "old.tar.gz"
    get_codec("targz").archive_dir(archived_session, archive)
    if store == "corrupt":
        (cfg.sync_source / "session-store.db").write_bytes(b"corrupt")
    elif store == "deferred":
        from agent_logger import usage_transcript

        _db(cfg.sync_source)
        assert capture_usage(cfg.sync_source, None).complete
        with (session / "events.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
        monkeypatch.setattr(usage_transcript, "MAX_EVENTS", 1)
    target = engine.build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("incomplete accounting allowed compaction or reconciliation")

    monkeypatch.setattr(compact, "compact_local", forbidden)
    monkeypatch.setattr(target, "compact_backlog", forbidden)
    monkeypatch.setattr(target, "reconcile_hub", forbidden)
    monkeypatch.setattr(engine, "build_target", lambda *a, **kw: target)
    assert engine.run_sync(cfg) == (1 if store == "corrupt" else 0)
    delivered = tmp_path / "dest" / "synthetic" / "archived" / archive.name
    assert delivered.read_bytes() == archive.read_bytes()


def test_usage_engine_dry_run_has_no_accounting_artifact(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    assert engine.run_sync(cfg, dry_run=True) == 0
    assert not (session / ARTIFACT).exists()
    assert not evidence_lock_path(session).exists()


def test_usage_engine_failure_keeps_raw_push_but_blocks_compaction(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.sync import compact

    cfg = _cfg(tmp_path)
    cfg._data["sync"]["compact"] = {"enabled": True}
    session = _session(cfg.sync_source)
    (cfg.sync_source / "session-store.db").write_bytes(b"corrupt")
    called = []
    monkeypatch.setattr(compact, "compact_local", lambda *a, **kw: called.append(True))
    assert engine.run_sync(cfg) == 1
    assert not called
    assert (tmp_path / "dest" / "synthetic" / "session-state" / "s1" / "events.jsonl").exists()
    assert not (session / ARTIFACT).exists()


@pytest.mark.parametrize("store_present", [True, False])
def test_usage_engine_compaction_requires_complete_capture(
    tmp_path: Path, monkeypatch, store_present: bool,
) -> None:
    from agent_logger.sync import compact

    cfg = _cfg(tmp_path)
    cfg._data["sync"]["compact"] = {"enabled": True}
    session = _session(cfg.sync_source)
    if store_present:
        _db(cfg.sync_source)
    called = []

    def compact_after_capture(*args, **kwargs):
        assert (session / ARTIFACT).is_file()
        called.append(True)
        return SimpleNamespace(compacted=0, failed=[])

    monkeypatch.setattr(compact, "compact_local", compact_after_capture)
    assert engine.run_sync(cfg) == 0
    assert bool(called) is store_present


def test_usage_engine_explicit_disable_and_invalid_config(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    session = _session(cfg.sync_source)
    cfg._data["sync"]["usage_capture"] = False
    assert engine.run_sync(cfg) == 0
    assert not (session / ARTIFACT).exists()
    cfg._data["sync"]["usage_capture"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        engine.run_sync(cfg)
