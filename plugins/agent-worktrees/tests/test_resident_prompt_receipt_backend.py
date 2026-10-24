"""In-process receipt budgets and exact-incarnation replay; no resident launched."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
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


@pytest.mark.parametrize("caller_budget", [0.05, 2.0])
def test_pre_effect_budget_is_one_second_or_earlier_caller_deadline(store, monkeypatch, caller_budget):
    clock = [time.monotonic()]

    @contextmanager
    def delayed_admission(path, **kwargs):
        assert kwargs["deadline"] <= clock[0] + receipt.MAX_LOCK_SECONDS
        clock[0] += min(caller_budget, 1.0) + 0.001
        yield

    monkeypatch.setattr(receipt, "_append_lock", delayed_admission)
    monkeypatch.setattr(receipt, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert invoke(record=True, deadline=clock[0] + caller_budget)["reason"] == "pre-effect-timeout"
    assert not handoff_trace.trace_path("project", "worktree").exists()


@pytest.mark.parametrize("nonce", ["", None, "bad\nnonce"])
def test_missing_or_invalid_incarnation_never_records(store, nonce):
    assert invoke(record=True, nonce=nonce)["status"] == "unavailable"
    assert not (store / "logs").exists()


def test_activity_query_cannot_acknowledge_failed_resident_fsync(store, monkeypatch, capsys):
    from agent_worktrees import activity

    fsync = receipt.os.fsync
    attempts = []

    def fail(fd):
        attempts.append(fd)
        raise OSError("controlled storage failure")

    monkeypatch.setattr(receipt.os, "fsync", fail)
    monkeypatch.setattr(activity.cfg, "active_project", lambda: "project")
    assert invoke(record=True)["status"] == "unknown"
    args = SimpleNamespace(receipt_locator="task:one", worktree_id="worktree")
    assert activity.cmd_activity(args) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "prompt receipt unavailable" in captured.err
    assert len(attempts) == 2
    monkeypatch.setattr(receipt.os, "fsync", fsync)
    assert activity.cmd_activity(args) == 0
    assert json.loads(capsys.readouterr().out)["received"] is True
    assert len(handoff_trace.read_trace("project", "worktree")) == 1


def test_legacy_history_query_synchronizes_the_matching_open_file(store, monkeypatch):
    legacy = store / "activity.jsonl"
    legacy.write_text(json.dumps({
        "event": receipt.EVENT, "worktree_id": "worktree",
        "session_id": "subject", "locator": "task:one",
    }) + "\n", encoding="utf-8")
    fsync = receipt.os.fsync

    def fail(_fd):
        raise OSError("controlled storage failure")

    monkeypatch.setattr(receipt.os, "fsync", fail)
    with pytest.raises(OSError):
        receipt.prompt_receipt("project", "worktree", "task:one", legacy_path=legacy)
    monkeypatch.setattr(receipt.os, "fsync", fsync)
    assert receipt.prompt_receipt(
        "project", "worktree", "task:one", legacy_path=legacy,
    )["received"] is True


@pytest.fixture
def capacity_store(store, monkeypatch):
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(receipt, "datetime", SimpleNamespace(now=lambda _zone: stamp))
    monkeypatch.setattr(receipt, "time", SimpleNamespace(monotonic=lambda: 0.0))

    @contextmanager
    def trace(_path, *, deadline):
        assert deadline == receipt.MAX_LOCK_SECONDS
        yield

    monkeypatch.setattr(receipt, "_append_lock", trace)
    return store, stamp


@pytest.mark.parametrize("existing_rows", [4095, 4096])
def test_record_reserves_a_queryable_row(capacity_store, existing_rows):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    before = b"{}\n" * existing_rows
    path.write_bytes(before)
    result = invoke(record=True)
    if existing_rows == 4096:
        assert result["status"] == "unavailable" and result["reason"] == "history-capacity-unavailable"
        assert path.read_bytes() == before
    else:
        assert result["status"] == "received"
        assert invoke()["status"] == "received"
        assert invoke(record=True)["replayed"] is True
        assert len(path.read_bytes().splitlines()) == 4096


def _noise_bytes(size, *, partial_tail):
    prefix, suffix = b'{"noise":"', b'"}\n'
    row = prefix + b"x" * (4096 - len(prefix) - len(suffix)) + suffix
    count, remainder = divmod(size, len(row))
    tail = (b" " * (remainder - 3) + b"{}\n") if remainder >= 3 else b"\n" * remainder
    data = row * count + tail
    if partial_tail:
        data = data[:-1] + b" "
    assert len(data) == size and data.endswith(b"\n") is not partial_tail
    return data


@pytest.mark.parametrize("partial_tail", [False, True])
@pytest.mark.parametrize("spare_bytes", [-1, 0, 1])
def test_record_reserves_queryable_bytes_and_separator(capacity_store, partial_tail, spare_bytes):
    _, stamp = capacity_store
    encoded = (json.dumps({
        "ts": stamp.isoformat(), "event": receipt.EVENT, "worktree_id": "worktree",
        "creation_nonce": "incarnation", "session_id": "subject",
        "source": "context-handoff", "locator": "task:one",
    }, ensure_ascii=True) + "\n").encode("utf-8")
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    before = _noise_bytes(
        receipt.MAX_SCAN_BYTES - len(encoded) - spare_bytes, partial_tail=partial_tail,
    )
    path.write_bytes(before)
    result = invoke(record=True)
    if spare_bytes < int(partial_tail):
        assert result["status"] == "unavailable" and result["reason"] == "history-capacity-unavailable"
        assert path.read_bytes() == before
    else:
        assert result["status"] == "received"
        assert path.read_bytes() == before + (b"\n" if partial_tail else b"") + encoded
        assert invoke()["status"] == "received"
        assert invoke(record=True)["replayed"] is True


@pytest.mark.parametrize("writer", ["compatibility", "resident"])
@pytest.mark.parametrize("terminal_size", [65535, 65536])
def test_terminal_record_separator_preserves_individual_limit(store, writer, terminal_size):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    prefix, suffix = b'{"noise":"', b'"}'
    before = prefix + b"x" * (terminal_size - len(prefix) - len(suffix)) + suffix
    path.write_bytes(before)
    if terminal_size == 65536:
        if writer == "compatibility":
            with pytest.raises(ValueError, match="record-limit"):
                receipt.prompt_receipt("project", "worktree", "task:one", "subject", record=True)
        else:
            result = invoke(record=True)
            assert result["status"] == "unavailable" and result["reason"] == "record-limit"
        assert path.read_bytes() == before
    else:
        if writer == "compatibility":
            assert receipt.prompt_receipt(
                "project", "worktree", "task:one", "subject", record=True,
            )["received"] is True
            assert receipt.prompt_receipt("project", "worktree", "task:one")["received"] is True
        else:
            assert invoke(record=True)["status"] == invoke()["status"] == "received"
        assert len(path.read_bytes().splitlines(keepends=True)[0]) == 65536


@pytest.mark.parametrize("writer", ["compatibility", "resident"])
def test_maximum_unterminated_matching_receipt_replays_without_separator(store, writer):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    event = {
        "event": receipt.EVENT, "worktree_id": "worktree", "session_id": "subject",
        "creation_nonce": "incarnation", "locator": "task:one", "padding": "",
    }
    event["padding"] = "x" * (65536 - len(json.dumps(event).encode("utf-8")))
    before = json.dumps(event).encode("utf-8")
    assert len(before) == 65536
    path.write_bytes(before)
    if writer == "compatibility":
        assert receipt.prompt_receipt(
            "project", "worktree", "task:one", "subject", record=True,
        )["replayed"] is True
    else:
        result = invoke(record=True)
        assert result["status"] == "received" and result["replayed"] is True
    assert path.read_bytes() == before


def test_rotated_legacy_replay_syncs_the_matching_descriptor_not_replacement(store, monkeypatch):
    legacy = store / "logs" / "activity.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "event": receipt.EVENT, "worktree_id": "worktree", "session_id": "subject",
        "creation_nonce": "incarnation", "locator": "task:one",
    }) + "\n", encoding="utf-8")
    replacement = legacy.with_name("replacement.jsonl")
    replacement.write_text('{"event":"unrelated"}\n', encoding="utf-8")
    original_identity = (legacy.stat().st_dev, legacy.stat().st_ino)
    opened, synced, rotation = [], [], []
    path_open, fsync = Path.open, receipt.os.fsync

    def capture_open(path, *args, **kwargs):
        handle = path_open(path, *args, **kwargs)
        if path == legacy:
            opened.append(handle)
        return handle

    def capture_sync(fd):
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
        fsync(fd)

    @contextmanager
    def rotating_admission():
        calls = 0

        def revalidate():
            nonlocal calls
            calls += 1
            if calls == 2:
                try:
                    replacement.replace(legacy)
                    rotation.append("replaced")
                except PermissionError:
                    assert os.name == "nt"
                    rotation.append("blocked-by-open-descriptor")

        yield revalidate

    monkeypatch.setattr(Path, "open", capture_open)
    monkeypatch.setattr(receipt.os, "fsync", capture_sync)
    result = invoke(admission=rotating_admission)
    assert result["status"] == "received" and result["replayed"] is True
    assert rotation and synced == [original_identity]
    assert len(opened) == 1 and opened[0].closed


def test_matching_descriptor_closes_after_revalidation_refusal(store, monkeypatch):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "event": receipt.EVENT, "worktree_id": "worktree", "session_id": "subject",
        "creation_nonce": "incarnation", "locator": "task:one",
    }) + "\n", encoding="utf-8")
    opened = []
    path_open = Path.open

    def capture_open(candidate, *args, **kwargs):
        handle = path_open(candidate, *args, **kwargs)
        if candidate == path:
            opened.append(handle)
        return handle

    @contextmanager
    def refusing_admission():
        calls = 0

        def revalidate():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("subject-association-unavailable")

        yield revalidate

    monkeypatch.setattr(Path, "open", capture_open)
    result = invoke(admission=refusing_admission)
    assert result["status"] == "unavailable" and result["reason"] == "subject-association-unavailable"
    assert len(opened) == 1 and opened[0].closed
