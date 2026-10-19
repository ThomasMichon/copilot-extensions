"""Read-only exact-session evidence for a host-owned recovery journal."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from dataclasses import replace

import pytest

from agent_worktrees import session_metadata_cli, tracking


NATIVE_ID = "00000000-0000-4000-8000-000000000001"
OTHER_NATIVE_ID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def owner():
    return importlib.import_module("agent_worktrees.native_hosting_evidence")


@pytest.fixture
def native_record(sample_record, tmp_path, monkeypatch):
    from agent_worktrees import sessions

    monkeypatch.setattr(
        importlib.import_module("agent_worktrees.native_hosting_evidence"),
        "session_process_owns_hold", lambda *_: True,
    )
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
    assert result["binding_proof"] == "inuse-hold-v1"
    assert result["liveness"] == "live"
    assert result["worktree_path"] == native_record.worktree_path
    assert result["automatic_eligible"] is False


def test_hosting_reports_existing_worktree_incarnation_without_allocating_one(
    owner, native_record, monkeypatch,
):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "live")
    record = replace(native_record, creation_nonce="0123456789abcdef", head_revision=17)
    result = owner.observe(record.worktree_id, loader=lambda: record)
    assert result["recovery_binding_capability"] == "worktree-head-v1"
    assert result["worktree_incarnation"] == record.creation_nonce
    assert result["head_revision"] == 17
    legacy = replace(record, creation_nonce="")
    result = owner.observe(legacy.worktree_id, loader=lambda: legacy)
    assert result["confirmed"] is True
    assert result["recovery_binding_capability"] is None
    assert result["worktree_incarnation"] is None
    assert legacy.creation_nonce == ""


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


def test_exact_historical_lifecycle_survives_no_current_hosting(owner, native_record, monkeypatch):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "dead")
    historical = tracking.SessionEntry(
        OTHER_NATIVE_ID, "old", ended_at="exit", activations=[
            tracking.SessionActivation(
                1, "old", "old", ended_at="exit", end_source="hook",
                source_identity="native:old", end_reason="user_exit",
            ),
        ],
    )
    record = replace(native_record, sessions=[historical, *native_record.sessions])
    result = owner.observe(
        record.worktree_id, loader=lambda: record, native_session_id=OTHER_NATIVE_ID,
    )
    assert result["confirmed"] is False
    assert result["native_session_id"] == NATIVE_ID
    assert result["lifecycle_capability"] == "source-bound-v1"
    assert result["lifecycle"]["native_session_id"] == OTHER_NATIVE_ID
    assert result["lifecycle"]["activations"][0]["end_reason"] == "user_exit"
    assert record.resolved_head_session == NATIVE_ID
    assert record.session_entry(NATIVE_ID).ended_at is None


@pytest.mark.parametrize("ambiguity,pruned,saturated,confirmed", [
    (False, False, False, True), (True, False, False, False),
    (False, True, False, False), (False, False, True, False),
])
def test_positive_lifecycle_requires_unique_canonical_physical_source(
    owner, native_record, monkeypatch, ambiguity, pruned, saturated, confirmed,
):
    from agent_worktrees import native_source_context

    epoch = {
        "platform": "linux", "environment_id": "a" * 32,
        "boot_id": OTHER_NATIVE_ID,
    }
    token = native_source_context.identity(NATIVE_ID, 101, "100", epoch)
    record = replace(native_record, sessions=[
        tracking.SessionEntry(
            NATIVE_ID, "start", pruned_sources=[token] if pruned else [],
            source_history_saturated=saturated, activations=[
            tracking.SessionActivation(
                3, "start", "start", source_identity=token, source_ambiguous=ambiguity,
            ),
        ]),
    ])
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(native_source_context, "epoch", lambda: epoch)
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner, "session_liveness", lambda _: "live")
    monkeypatch.setattr(owner.sessions, "mux_binding_for_session", lambda *_args, **_kwargs: {
        "session_name": owner.sessions.mux_session_name(record.worktree_id),
        "pane_id": "%1", "copilot_pid": 101, "copilot_start_time": "100",
        "pane_start_time": "99", "session_incarnation": "mux:1:100",
    })
    result = owner.observe(record.worktree_id, loader=lambda: record)
    assert result["confirmed"] is confirmed
    assert result["lifecycle_qualified"] is confirmed
    assert result["activation_ordinal"] == (3 if confirmed else 0)
    assert result["source_identity"] == (token if confirmed else None)
    assert result["lifecycle"]["activations"][0]["source_ambiguous"] is not confirmed


def test_ended_native_interval_is_not_checkpointed_again(owner, native_record, monkeypatch):
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(
        owner, "session_liveness", lambda _: pytest.fail("ended activation must not be reopened"),
    )
    native_record.sessions[0].ended_at = "exit"
    result = owner.observe(native_record.worktree_id, loader=lambda: native_record)
    assert result["confirmed"] is False
    assert result["reason"] == "ended-native-activation"


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


def test_hosting_cli_passes_only_an_explicit_lifecycle_session(monkeypatch):
    from agent_worktrees import native_hosting_evidence

    seen = []
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", OTHER_NATIVE_ID)
    monkeypatch.setattr(
        native_hosting_evidence, "observe",
        lambda worktree_id, **kwargs: seen.append(kwargs["native_session_id"]) or {},
    )
    for requested in (None, NATIVE_ID):
        assert session_metadata_cli.cmd_session_role(argparse.Namespace(
            hosting_evidence=True, worktree_id="exact-worktree", session_id=requested,
        )) == 0
    assert seen == [None, NATIVE_ID]


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


@pytest.mark.parametrize("held_session,confirmed", [
    (NATIVE_ID, True), (OTHER_NATIVE_ID, False),
])
def test_pid_reuse_before_probe_cannot_pair_head_with_another_session(
    owner, native_record, tmp_path, monkeypatch, held_session, confirmed,
):
    if not sys.platform.startswith("linux"):
        pytest.skip("Exact held-file ownership proof requires native Linux procfs")
    from agent_worktrees import locks

    liveness = importlib.import_module("agent_worktrees.session_liveness")
    monkeypatch.setattr(owner, "session_process_owns_hold", liveness.session_process_owns_hold)
    pid = os.getpid()
    born = locks.process_start_time(pid)
    assert born
    state_root = tmp_path / "native-state"
    old = state_root / NATIVE_ID
    current = state_root / OTHER_NATIVE_ID
    old.mkdir(parents=True)
    current.mkdir()
    (old / f"inuse.{pid}.lock").touch()
    (old / f"inuse.{pid}.hold").touch()
    (old / "events.jsonl").touch()
    monkeypatch.setattr(owner.sessions, "_session_state_dir", lambda: state_root)
    monkeypatch.setattr(owner.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(liveness, "copilot_pid_state", lambda candidate: candidate == pid)
    monkeypatch.setattr(owner.sessions, "mux_binding_for_session", lambda *_args, **_kwargs: {
        "worktree_id": native_record.worktree_id,
        "session_name": owner.sessions.mux_session_name(native_record.worktree_id),
        "pane_id": "%1", "copilot_pid": pid, "copilot_start_time": born,
        "pane_start_time": "pane:100", "session_incarnation": "mux:1:100",
    })
    # Stale A pathnames alone cannot prove ownership when this incarnation holds B.
    with (state_root / held_session / f"inuse.{pid}.hold").open("w"):
        assert liveness.session_liveness(NATIVE_ID) == "live"
        result = owner.observe(native_record.worktree_id, loader=lambda: native_record)
    assert result["confirmed"] is confirmed
    assert result["binding_proof"] == ("inuse-hold-v1" if confirmed else None)
    if not confirmed:
        assert result["reason"] == "native-session-process-unconfirmed"
        assert result["liveness"] == "unknown"
