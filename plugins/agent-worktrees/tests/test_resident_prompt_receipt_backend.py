"""In-process receipt budgets and exact-incarnation replay; no resident launched."""

from contextlib import contextmanager
import json
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import handoff_prompt_receipt as receipt, handoff_trace


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff_trace.cfg, "install_dir", lambda: tmp_path)
    return tmp_path


@contextmanager
def admission():
    yield lambda: None


def invoke(*, record=False, nonce="incarnation", **kwargs):
    return receipt.resident_prompt_receipt(
        "project", "worktree", "task:one", "subject",
        creation_nonce=nonce, record=record,
        deadline=kwargs.pop("deadline", time.monotonic() + 2),
        admission=kwargs.pop("admission", admission), **kwargs,
    )


def test_exact_nonce_record_query_and_replay(store):
    assert invoke()["status"] == "absent"
    assert invoke(record=True) == {"status": "received", "reason": "recorded", "replayed": False}
    assert invoke()["status"] == "received"
    assert invoke(record=True)["replayed"] is True
    assert invoke(nonce="another")["status"] == "absent"
    rows = handoff_trace.read_trace("project", "worktree")
    assert len(rows) == 1 and rows[0]["creation_nonce"] == "incarnation"


def test_matching_legacy_row_is_unavailable_without_backfill(store):
    receipt.prompt_receipt("project", "worktree", "task:one", "subject", record=True)
    path = handoff_trace.trace_path("project", "worktree")
    before = path.read_bytes()
    for record in (False, True):
        assert invoke(record=record) == {
            "status": "unavailable", "reason": "legacy-incarnation-unavailable", "replayed": False,
        }
    assert path.read_bytes() == before


def test_total_row_limit(store):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}\n" * (receipt.MAX_SCAN_ROWS + 1))
    assert invoke(record=True)["reason"] == "scan-limit"
    assert b"task:one" not in path.read_bytes()


def test_total_byte_limit_is_shared_across_both_histories(store):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    row = json.dumps({"noise": "x" * 500}).encode() + b"\n"
    path.write_bytes(row * 1100)
    legacy = store / "logs" / "activity.jsonl"
    legacy.write_bytes(row * 1100)
    assert invoke(record=True)["reason"] == "scan-limit"
    assert b"task:one" not in path.read_bytes()


def test_oversized_record_fails_before_append(store):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * (receipt._MAX_RECORD_BYTES + 1))
    assert invoke(record=True)["reason"] == "record-limit"


def test_scan_clock_limit_after_slow_parse(store, monkeypatch):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}\n")
    clock = [time.monotonic()]
    loads = json.loads

    def slow_loads(data):
        clock[0] += 0.21
        return loads(data)

    monkeypatch.setattr(receipt, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(receipt, "json", SimpleNamespace(loads=slow_loads))
    assert invoke(record=True, deadline=clock[0] + 2)["reason"] == "scan-timeout"


def test_record_guard_precedes_trace_and_revalidates_before_append(store, monkeypatch):
    entered = []
    held = [False]
    canonical = receipt._append_lock

    @contextmanager
    def guard():
        held[0] = True
        entered.append("record")
        try:
            yield lambda: entered.append("revalidate")
        finally:
            held[0] = False

    @contextmanager
    def trace(path, **kwargs):
        assert held[0]
        entered.append("trace")
        with canonical(path, **kwargs):
            yield

    fsync = receipt.os.fsync

    def sync(fd):
        assert held[0] and entered[-1] == "revalidate"
        entered.append("fsync")
        fsync(fd)

    monkeypatch.setattr(receipt, "_append_lock", trace)
    monkeypatch.setattr(receipt.os, "fsync", sync)
    assert invoke(record=True, admission=guard)["status"] == "received"
    assert entered[:3] == ["record", "trace", "revalidate"]
    assert entered.count("revalidate") >= 2 and not held[0]


def test_removed_association_before_append_is_unavailable(store):
    calls = [0]

    @contextmanager
    def guard():
        def validate():
            calls[0] += 1
            if calls[0] >= 2:
                raise ValueError("subject-association-unavailable")
        yield validate

    assert invoke(record=True, admission=guard)["reason"] == "subject-association-unavailable"
    assert not handoff_trace.trace_path("project", "worktree").exists()


def test_late_fsync_remains_unknown_then_exact_query_reconciles(store, monkeypatch):
    clock = [time.monotonic()]
    fsync = receipt.os.fsync
    attempts = []

    def late_sync(fd):
        fsync(fd)
        attempts.append(fd)
        if len(attempts) == 1:
            clock[0] += 3

    monkeypatch.setattr(receipt, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(receipt.os, "fsync", late_sync)
    first = invoke(record=True, deadline=clock[0] + 2)
    assert first["status"] == "unknown"
    assert invoke(deadline=clock[0] + 2)["status"] == "received"
    assert len(attempts) == 2
    assert len(handoff_trace.read_trace("project", "worktree")) == 1


def test_fsync_error_never_reports_absent_or_safe_retry(store, monkeypatch):
    fsync = receipt.os.fsync
    attempts = []

    def fail(_fd):
        attempts.append(_fd)
        raise OSError("controlled failure with private details")

    monkeypatch.setattr(receipt.os, "fsync", fail)
    for record in (True, False, True):
        assert invoke(record=record) == {
            "status": "unknown", "reason": "commit-unknown", "replayed": False,
        }
    assert len(attempts) == 3
    assert len(handoff_trace.read_trace("project", "worktree")) == 1
    monkeypatch.setattr(receipt.os, "fsync", fsync)
    assert invoke()["status"] == "received"
    assert len(handoff_trace.read_trace("project", "worktree")) == 1


def test_expired_admission_has_no_effect(store):
    assert invoke(record=True, deadline=time.monotonic() - 1)["status"] == "unavailable"
    assert not (store / "logs").exists()


@pytest.mark.parametrize("nonce", ["", None, "bad\nnonce"])
def test_missing_or_invalid_incarnation_never_records(store, nonce):
    assert invoke(record=True, nonce=nonce)["status"] == "unavailable"
    assert not (store / "logs").exists()
