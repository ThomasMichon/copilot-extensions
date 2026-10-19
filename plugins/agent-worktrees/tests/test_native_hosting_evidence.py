"""Read-only exact-session evidence for a host-owned recovery journal."""

from __future__ import annotations

import argparse
import importlib
from dataclasses import replace

import pytest

from agent_worktrees import session_metadata_cli, tracking


NATIVE_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def owner():
    return importlib.import_module("agent_worktrees.native_hosting_evidence")


@pytest.fixture
def native_record(sample_record, tmp_path, monkeypatch):
    from agent_worktrees import sessions

    monkeypatch.setattr(sessions, "mux_binding_for_session", lambda *_args, **_kwargs: {
        "worktree_id": sample_record.worktree_id,
        "session_name": sessions.mux_session_name(sample_record.worktree_id),
        "pane_id": "%1", "copilot_start_time": "native:100", "pane_start_time": "native:99",
        "copilot_pid": 101,
        "session_incarnation": "mux:1:100",
    })
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    return replace(
        sample_record, worktree_path=str(worktree), platform="linux",
        head_session=NATIVE_ID,
        sessions=[tracking.SessionEntry(
            session_id=NATIVE_ID, started_at="2026-01-01T00:00:00Z",
        )],
    )


def test_confirmed_native_head_uses_owners_exact_identity(owner, native_record, monkeypatch):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "live")
    result = owner.observe(native_record.worktree_id, loader=lambda: native_record)
    assert result["confirmed"] is True
    assert result["native_session_id"] == NATIVE_ID
    assert result["native_incarnation"] == "101:native:100"
    assert result["liveness"] == "live"
    assert result["worktree_path"] == native_record.worktree_path
    assert result["automatic_eligible"] is False


@pytest.mark.parametrize("state", ["dead", "unknown"])
def test_dead_or_uncertain_is_not_positive_hosting(
    owner, native_record, monkeypatch, state,
):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: state)
    result = owner.observe(native_record.worktree_id, loader=lambda: native_record)
    assert result["confirmed"] is False
    assert result["liveness"] == state


def test_head_change_during_probe_is_uncertain(owner, native_record, monkeypatch):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "live")
    reads = iter([native_record, replace(native_record, head_revision=9)])
    result = owner.observe(native_record.worktree_id, loader=lambda: next(reads))
    assert result["confirmed"] is False
    assert result["reason"] == "owner-changed"


def test_live_native_head_without_proven_mux_binding_is_uncertain(
    owner, native_record, monkeypatch,
):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "live")
    monkeypatch.setattr(owner.sessions, "mux_binding_for_session", lambda *_args, **_kwargs: None)
    result = owner.observe(native_record.worktree_id, loader=lambda: native_record)
    assert result["confirmed"] is False
    assert result["reason"] == "native-host-unconfirmed"


@pytest.mark.parametrize("change,reason", [
    ({"paused": True}, "paused"),
    ({"status": "completed"}, "terminal-worktree"),
    ({"platform": "windows"}, "foreign-environment"),
    ({"head_session": None, "sessions": []}, "no-native-head"),
])
def test_no_positive_evidence_for_ineligible_owner(
    owner, native_record, monkeypatch, change, reason,
):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(
        owner, "session_liveness",
        lambda _: pytest.fail("ineligible record must not reach a process probe"),
    )
    result = owner.observe(
        native_record.worktree_id, loader=lambda: replace(native_record, **change),
    )
    assert result["confirmed"] is False
    assert result["reason"] == reason


def test_hosting_cli_is_explicit_and_has_no_ambient_session_fallback(monkeypatch):
    from agent_worktrees import __main__ as core

    args = core.build_parser().parse_args([
        "session-role", "--hosting-evidence", "--worktree-id", "worktree-one", "--json",
    ])
    assert args.hosting_evidence is True
    assert args.worktree_id == "worktree-one"
    assert getattr(args, "session_id", None) is None


def test_legacy_session_role_still_uses_existing_path(monkeypatch):
    monkeypatch.setattr(session_metadata_cli, "_resolve_worktree_for_read", lambda *_: None)
    assert session_metadata_cli.cmd_session_role(argparse.Namespace(
        session_id=NATIVE_ID, worktree_id=None, worktree_dir=None,
    )) == 0


@pytest.mark.parametrize("identity", ["../other", "/absolute", "a\\other", "..", "a\0b"])
def test_unsafe_identity_is_rejected_before_owner_read(owner, identity):
    with pytest.raises(ValueError, match="identity"):
        owner.observe(identity, loader=lambda: pytest.fail("unsafe owner read"))


def test_windows_hosting_refuses_before_any_process_or_mux_probe(
    owner, native_record, monkeypatch,
):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(
        owner, "session_liveness",
        lambda _: pytest.fail("unqualified native platform must not spawn a probe"),
    )
    result = owner.observe(
        native_record.worktree_id, loader=lambda: replace(native_record, platform="windows"),
    )
    assert result["confirmed"] is False
    assert result["reason"] == "native-hosting-unqualified"


def test_dotted_worktree_identity_compares_encoded_mux_name(owner, native_record, monkeypatch):
    record = replace(native_record, worktree_id="host.example-linux")
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "live")
    monkeypatch.setattr(owner.sessions, "mux_binding_for_session", lambda *_args, **_kwargs: {
        "worktree_id": "host_example-linux", "session_name": "wt-host_example-linux",
        "pane_id": "%1", "copilot_pid": 101, "copilot_start_time": "native:100",
        "pane_start_time": "native:99", "session_incarnation": "mux:1:100",
    })
    result = owner.observe(record.worktree_id, loader=lambda: record)
    assert result["confirmed"] is True
    assert result["worktree_id"] == "host.example-linux"
