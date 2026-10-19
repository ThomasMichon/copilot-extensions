"""Original-source binding across hook and resident process boundaries."""

from __future__ import annotations

import os
import sys

import pytest

from agent_worktrees import native_lifecycle_source as source
from agent_worktrees import native_source_context as context
from agent_worktrees import tracking


SID = "00000000-0000-4000-8000-000000000001"
OTHER_SID = "00000000-0000-4000-8000-000000000002"
EPOCH = {"platform": "linux", "environment_id": "e" * 32, "boot_id": SID}


@pytest.fixture
def witness(monkeypatch):
    monkeypatch.setattr(context, "epoch", lambda: dict(EPOCH))
    return {
        "version": 1, "qualified": True, **EPOCH,
        "ancestors": [{"pid": 101, "start_time": "100"}],
    }


def test_resident_never_recaptures_missing_original_source(monkeypatch):
    monkeypatch.setattr(
        context, "capture", lambda: pytest.fail("resident source must not be recaptured"),
    )
    assert source.witness({}, resident=True) is None
    assert source.witness(
        {"_agentWorktrees": {"nativeSource": {"qualified": False}}}, resident=True,
    ) == {"qualified": False}


def test_exact_uuid_hold_qualifies_start_not_a_different_process(witness, monkeypatch):
    monkeypatch.setattr(
        source, "session_process_owns_hold",
        lambda sid, pid, birth: sid == SID and pid == 101 and birth == "100",
    )
    expected = context.identity(SID, 101, "100", EPOCH)
    assert source.start_identity(SID, witness) == expected
    assert source.start_identity(OTHER_SID, witness) is None
    changed = {**witness, "ancestors": [{"pid": 101, "start_time": "200"}]}
    assert source.start_identity(SID, changed) is None


def test_zero_boot_tick_is_a_valid_native_birth_token(witness):
    witness["ancestors"][0]["start_time"] = "0"
    assert context.candidates(SID, witness) == [
        (context.identity(SID, 101, "0", EPOCH), 101, "0"),
    ]


@pytest.mark.parametrize("change", [
    {"boot_id": OTHER_SID}, {"environment_id": "f" * 32}, {"platform": "wsl"},
    {"ancestors": []}, {"ancestors": [{"pid": True, "start_time": "100"}]},
    {"ancestors": [{"pid": 101, "start_time": "100"}] * 17},
    {"qualified": False}, {"version": True},
])
def test_invalid_original_source_stays_unconfirmed(witness, change):
    assert source.start_identity(SID, {**witness, **change}) is None


def test_delayed_end_matches_history_not_current_head(witness):
    token = context.identity(SID, 101, "100", EPOCH)
    entry = tracking.SessionEntry(SID, "first", activations=[
        tracking.SessionActivation(
            1, "first", "first", source_identity=token, ended_at="replacement",
        ),
        tracking.SessionActivation(
            2, "replacement", "replacement", source_identity="native:replacement",
        ),
    ])
    assert source.end_identity(SID, witness, entry) == token
    assert source.end_identity(OTHER_SID, witness, entry) is None
    entry.activations[-1].source_identity = token
    assert source.end_identity(SID, witness, entry) is None


def test_capture_is_bounded_metadata_only(witness, monkeypatch):
    monkeypatch.setattr(context, "process", lambda pid: {
        "pid": pid, "start_time": str(pid), "parent_pid": pid + 1,
    })
    value = context.capture()
    assert value["qualified"] is True
    assert len(value["ancestors"]) == context.MAX_ANCESTORS
    assert set(value) == {"version", "qualified", "platform", "environment_id", "boot_id", "ancestors"}
    assert all(set(item) == {"pid", "start_time"} for item in value["ancestors"])


def test_capture_refuses_parent_reuse_or_reparenting(witness, monkeypatch):
    monkeypatch.setattr(os, "getpid", lambda: 1000)
    observations = iter([
        {"pid": 1000, "start_time": "999", "parent_pid": 101},
        {"pid": 101, "start_time": "200", "parent_pid": 1},
        {"pid": 1000, "start_time": "999", "parent_pid": 1},
    ])
    monkeypatch.setattr(context, "process", lambda pid: next(observations))
    value = context.capture()
    assert value["qualified"] is False
    assert "ancestors" not in value


def test_actual_held_file_start_binding(tmp_path, monkeypatch, witness):
    if not sys.platform.startswith("linux"):
        pytest.skip("Exact native hold proof requires Linux procfs")
    from agent_worktrees import locks, session_liveness, sessions

    pid = os.getpid()
    birth = locks.process_start_time(pid)
    assert birth
    witness["ancestors"] = [{"pid": pid, "start_time": birth}]
    root = tmp_path / "sessions"
    directory = root / SID
    directory.mkdir(parents=True)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    monkeypatch.setattr(source, "session_process_owns_hold", session_liveness.session_process_owns_hold)
    hold = directory / f"inuse.{pid}.hold"
    hold.touch()
    assert source.start_identity(SID, witness) is None
    with hold.open("r"):
        assert source.start_identity(SID, witness) == context.identity(SID, pid, birth, EPOCH)
        witness["ancestors"][0]["start_time"] = str(int(birth) + 1)
        assert source.start_identity(SID, witness) is None
