"""Fresh native evidence for a qualified retained cold-resume registration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import native_lifecycle_source as source, native_source_context as context
from agent_worktrees import retained_record_writer, tracking
from agent_worktrees import tracking_session_registration_write as registration
from agent_worktrees import tracking_session_deregistration_write as deregistration
from test_tracking_session_registration_write import record_path as record_path

SID = "00000000-0000-4000-8000-000000000001"
OTHER_SID = "00000000-0000-4000-8000-000000000002"
EPOCH = {"platform": "linux", "environment_id": "e" * 32, "boot_id": SID}


@pytest.mark.parametrize("change", ["birth", "epoch", "hold", "pid"])
def test_changed_native_source_stays_unconfirmed(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    observations = iter([
        {"pid": 101, "start_time": "100", "parent_pid": 1},
        {"pid": 101, "start_time": "200" if change == "birth" else "100", "parent_pid": 1},
    ])
    epochs = iter([EPOCH, {**EPOCH, "boot_id": OTHER_SID} if change == "epoch" else EPOCH])
    monkeypatch.setattr(context, "process", lambda _: next(observations))
    monkeypatch.setattr(context, "epoch", lambda: next(epochs))
    monkeypatch.setattr(source, "session_process_owns_hold", lambda *_: change != "hold")
    assert source.retained_start_identity(SID, True if change == "pid" else 101) is None


@pytest.mark.parametrize("native_source,retained,head_matches,accepted", [
    ("native:fresh", True, True, True),
    ("native:first", True, True, False),
    (None, True, True, False),
    ("native:fresh", False, True, False),
    ("native:fresh", True, False, False),
])
def test_retained_cold_resume_needs_a_new_corroborated_source(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, native_source: str | None,
    retained: bool, head_matches: bool, accepted: bool,
) -> None:
    args: dict[str, Any] = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": SID, "source": "hook:new",
        "started_at": "first", "source_identity": "native:first",
    }
    assert registration.apply_session_register(args)["ok"]
    assert deregistration.apply_session_deregister({
        **args, "ended_at": "exit", "end_reason": "user_exit",
    })["lifecycle"] == "ended"
    if not head_matches:
        record = tracking.load_record(record_path)
        original = record.session_entry(SID)
        assert original is not None
        original.state = "yielded"
        tracking.save_record(record, record_path)
        assert registration.apply_session_register({
            **args, "session_id": OTHER_SID, "source": "bind", "source_identity": "native:other",
        })["ok"]
    monkeypatch.setattr(retained_record_writer, "active", lambda: retained)
    observed: list[tuple[str, int | None]] = []

    def corroborate(sid: str, pid: int | None) -> str | None:
        observed.append((sid, pid))
        return native_source

    monkeypatch.setattr(source, "retained_start_identity", corroborate)
    before = record_path.read_bytes()
    result = registration.apply_session_register({
        **args, "source_identity": None, "started_at": None, "pid": 4242,
    })
    if not accepted:
        assert result["error"] == "lifecycle"
        assert record_path.read_bytes() == before
        assert bool(observed) == retained
        return
    assert result == {"ok": True, "linked_handoff": None}
    entry = tracking.load_record(record_path).session_entry(SID)
    assert entry is not None and observed == [(SID, 4242)]
    assert entry.ended_at is None and entry.pid == 4242
    assert [(item.source_identity, item.ended_at) for item in entry.activations] == [
        ("native:first", "exit"), ("native:fresh", None),
    ]
    assert entry.activations[-1].start_source == "retained-native"


@pytest.mark.parametrize("case", ["held", "missing-hold", "changed-incarnation"])
def test_cli_corroborates_actual_native_hold(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str,
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("Exact native hold proof requires Linux procfs")
    from agent_worktrees import resource_relationship, session_binding_cli, sessions

    args: dict[str, Any] = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": SID, "source": "hook:new",
        "source_identity": "native:previous", "started_at": "first",
    }
    assert registration.apply_session_register(args)["ok"]
    assert deregistration.apply_session_deregister({
        **args, "ended_at": "exit",
    })["lifecycle"] == "ended"
    record = tracking.load_record(record_path)
    record.creation_nonce = "original-incarnation"
    tracking.save_record(record, record_path)
    identity = {"installation_root": str(tmp_path / "owner"), "runtime_generation": "a" * 32}
    monkeypatch.setattr(resource_relationship, "writer_identity", lambda: identity)
    monkeypatch.setattr(cfg, "active_project", lambda: "test-repo")
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _: record_path.parent)
    monkeypatch.setattr(cfg, "tracking_dir", lambda *a: record_path.parent)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path / "native")
    monkeypatch.setattr(sessions, "mux_binding_for_session", lambda *_a, **_kw: None)
    monkeypatch.setattr(source, "witness", lambda *_a, **_kw: None)
    monkeypatch.setattr(session_binding_cli.worktree_trust, "ensure_record_trusted", lambda *_: None)
    monkeypatch.setattr(session_binding_cli.profile_assignment, "bind", lambda *_: None)
    monkeypatch.setattr(session_binding_cli.profile_assignment, "maintain", lambda: None)
    monkeypatch.setattr(session_binding_cli.launch_native_observation, "observe_registration", lambda *_a, **_k: None)
    monkeypatch.setattr(session_binding_cli.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        session_binding_cli, "_ensure_status_monitor",
        lambda: pytest.fail("retained registration must not ensure infrastructure"),
    )
    scope = {
        "version": 1, "identity": identity,
        "creation_nonce": "changed" if case == "changed-incarnation" else record.creation_nonce,
        "worktree_id": record.worktree_id, "project": record.repo,
        "worktree_path": record.worktree_path,
    }
    parser = argparse.ArgumentParser()
    session_binding_cli.add_parsers(parser.add_subparsers(dest="command"))
    parsed = parser.parse_args([
        "register-session", "--session-id", SID, "--worktree-id", record.worktree_id,
        "--cwd", record.worktree_path, "--pid", str(os.getpid()),
        "--retained-record-writer", json.dumps(scope),
    ])
    hold = tmp_path / "native" / SID / f"inuse.{os.getpid()}.hold"
    hold.parent.mkdir(parents=True)
    if case == "missing-hold":
        hold = tmp_path / "unrelated-hold"
    before = record_path.read_bytes()
    assert source.retained_start_identity(SID, os.getpid()) is None
    with hold.open("w"):
        if case == "held":
            assert source.retained_start_identity(SID, os.getpid()) is not None
            assert source.retained_start_identity(OTHER_SID, os.getpid()) is None
        result = session_binding_cli.cmd_register_session(parsed)
    if case != "held":
        assert result != 0
        assert record_path.read_bytes() == before
        return
    assert result == 0
    entry = tracking.load_record(record_path).session_entry(SID)
    assert entry is not None and entry.ended_at is None and len(entry.activations) == 2
    assert entry.activations[0].ended_at == "exit"
    token = entry.activations[1].source_identity
    assert token is not None and token.startswith("native-source-v1:")
