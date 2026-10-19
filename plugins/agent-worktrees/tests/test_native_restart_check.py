"""Current worktree checks are exact, provisional and never launch permission."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, replace

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import session_metadata_cli, tracking
from agent_worktrees.sessions import _list_mux_sessions as real_mux_listing


NATIVE = "00000000-0000-4000-8000-000000000001"
SOURCE = "native-source-v1:" + "a" * 64
EPOCH = {"platform": "linux", "environment_id": "b" * 32, "boot_id": NATIVE}


@pytest.fixture
def subject(sample_record, tmp_path, monkeypatch):
    from agent_worktrees import native_restart_check as owner

    worktree = tmp_path / "checkout"
    worktree.mkdir()
    record = replace(
        sample_record, worktree_path=str(worktree), platform="linux",
        creation_nonce="0123456789abcdef", head_revision=5, lifecycle_revision=8,
        head_session=NATIVE, sessions=[tracking.SessionEntry(
            NATIVE, "start", activations=[tracking.SessionActivation(
                2, "start", "start", source_identity=SOURCE,
            )],
        )],
    )
    expected = {
        "project": record.repo,
        "worktree_id": record.worktree_id, "worktree_path": record.worktree_path,
        "native_session_id": NATIVE, "worktree_incarnation": record.creation_nonce,
        "head_revision": 5, "owner_revision": 8,
        "activation_ordinal": 2, "source_identity": SOURCE,
        "target_epoch": EPOCH,
    }
    monkeypatch.setattr(owner.native_source_context, "epoch", lambda: dict(EPOCH))
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(owner.sessions, "_list_mux_sessions", lambda: {})
    monkeypatch.setattr(owner, "session_liveness", lambda _: "dead")
    return owner, record, expected


def test_exact_current_owner_check_is_not_launch_permission(subject):
    owner, record, expected = subject
    before = asdict(record)
    result = owner.check(expected, loader=lambda: record)
    assert result == {
        "schema": "native-restart-worktree-check", "version": 1,
        "expected": expected, "worktree_qualified": True,
        "launch_authorized": False, "automatic_eligible": False,
        "reason": "current-worktree-matches",
    }
    assert asdict(record) == before


@pytest.mark.parametrize("change,reason", [
    ({"repo": "different-project"}, "project-changed"),
    ({"creation_nonce": "fedcba9876543210"}, "worktree-incarnation-changed"),
    ({"head_revision": 6}, "head-changed"),
    ({"lifecycle_revision": 9}, "lifecycle-changed"),
    ({"head_session": None, "sessions": []}, "head-changed"),
    ({"status": "completed"}, "terminal-worktree"),
    ({"completed_at": "ended"}, "terminal-worktree"),
    ({"paused": True}, "paused"),
    ({"paused_revision": 1}, "pause-history-unqualified"),
    ({"pending_seed": "other task"}, "pending-seed"),
    ({"execution_leg_opaque": True}, "non-native-execution"),
    ({"session_backend_opaque": True}, "non-native-execution"),
    ({"dispatch_attempt_opaque": True}, "task-policy-unqualified"),
    ({"dispatch_attempt_raw_present": True}, "task-policy-unqualified"),
    ({"owner_ref": "machine/repo/worktree"}, "claim-policy-unqualified"),
    ({"bound_agent": "worker"}, "profile-policy-unqualified"),
    ({"profile_assignment_revision": 1}, "profile-policy-unqualified"),
    ({"platform": "wsl"}, "foreign-environment"),
    ({"worktree_path": "/different/checkout"}, "worktree-path-changed"),
])
def test_current_policy_changes_refuse_before_process_probe(subject, monkeypatch, change, reason):
    owner, record, expected = subject
    monkeypatch.setattr(owner, "session_liveness", lambda _: pytest.fail("ineligible process probe"))
    result = owner.check(expected, loader=lambda: replace(record, **change))
    assert result["worktree_qualified"] is False
    assert result["reason"] == reason
    assert result["launch_authorized"] is False


@pytest.mark.parametrize("change,reason", [
    ({"ended_at": "end"}, "ended-native-activation"),
    ({"source_ambiguous": True}, "activation-source-unconfirmed"),
    ({"ordinal": 3}, "activation-changed"),
    ({"source_identity": "native-source-v1:" + "c" * 64}, "activation-changed"),
])
def test_exact_activation_not_just_native_uuid(subject, change, reason):
    owner, record, expected = subject
    record.sessions[0].activations[0] = replace(record.sessions[0].activations[0], **change)
    assert owner.check(expected, loader=lambda: record)["reason"] == reason


@pytest.mark.parametrize("state", ["live", "unknown"])
def test_live_or_unknown_never_qualifies_for_cold_resume(subject, monkeypatch, state):
    owner, record, expected = subject
    monkeypatch.setattr(owner, "session_liveness", lambda _: state)
    result = owner.check(expected, loader=lambda: record)
    assert result["reason"] == f"native-head-{state}"
    assert result["worktree_qualified"] is False


def test_missing_transcript_and_existing_mux_refuse(subject, monkeypatch):
    owner, record, expected = subject
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: False)
    assert owner.check(expected, loader=lambda: record)["reason"] == "missing-native-conversation"
    monkeypatch.setattr(owner.sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(
        owner.sessions, "_list_mux_sessions",
        lambda: {owner.sessions.mux_session_name(record.worktree_id): 0},
    )
    assert owner.check(expected, loader=lambda: record)["reason"] == "existing-mux"


def test_current_epoch_must_match_requested_target(subject, monkeypatch):
    owner, record, expected = subject
    monkeypatch.setattr(owner.native_source_context, "epoch", lambda: {**EPOCH, "boot_id": "other"})
    assert owner.check(expected, loader=lambda: record)["reason"] == "foreign-environment"


def test_owner_change_during_process_observation_invalidates_snapshot(subject, monkeypatch):
    owner, record, expected = subject
    reads = iter([record, replace(record, paused=True)])
    assert owner.check(expected, loader=lambda: next(reads))["reason"] == "owner-changed"


def test_missing_worktree_is_structured_refusal(subject):
    owner, record, expected = subject
    assert owner.check(expected, loader=lambda: None)["reason"] == "missing-or-mismatched-worktree"


@pytest.mark.parametrize("field,value", [
    ("project", None),
    ("worktree_id", "../escape"), ("worktree_incarnation", "invalid"),
    ("head_revision", True), ("owner_revision", -1), ("activation_ordinal", 0),
    ("native_session_id", "not-a-uuid"), ("worktree_path", "relative"),
    ("source_identity", "unknown"), ("target_epoch", {**EPOCH, "extra": True}),
    ("target_epoch", {**EPOCH, "platform": []}),
])
def test_invalid_expectations_never_reach_loader(subject, field, value):
    owner, record, expected = subject
    with pytest.raises(ValueError):
        owner.check({**expected, field: value}, loader=lambda: pytest.fail("unsafe read"))


def test_cli_check_requires_explicit_bounded_payload_and_no_ambient_identity(subject, monkeypatch, capfd):
    from agent_worktrees import __main__ as core
    owner, record, expected = subject
    seen = []
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "not-the-requested-head")
    monkeypatch.setattr(
        owner, "check",
        lambda payload, **kwargs: seen.append(payload) or {"launch_authorized": False},
    )
    args = core.build_parser().parse_args([
        "session-role", "--restart-check", json.dumps(expected), "--json",
    ])
    assert session_metadata_cli.cmd_session_role(args) == 0
    assert seen == [expected]
    assert json.loads(capfd.readouterr().out)["launch_authorized"] is False
    with pytest.raises(SystemExit):
        core.build_parser().parse_args([
            "session-role", "--hosting-evidence", "--restart-check", "{}",
        ])


def test_actual_child_reads_persisted_owner_not_parent_session(subject, tmp_path):
    owner, record, expected = subject
    record_path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, record_path)
    code = """
import json, sys
from pathlib import Path
from agent_worktrees import native_restart_check as owner, session_metadata_cli as cli
from agent_worktrees import __main__ as core
expected = json.loads(sys.argv[1])
cli.cfg.tracking_dir = lambda: Path(sys.argv[2]).parent
owner.native_source_context.epoch = lambda: expected["target_epoch"]
owner.sessions.session_has_conversation_data = lambda _: True
owner.sessions._list_mux_sessions = lambda: {}
owner.session_liveness = lambda _: "dead"
args = core.build_parser().parse_args(["session-role", "--restart-check", sys.argv[1], "--json"])
raise SystemExit(cli.cmd_session_role(args))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, json.dumps(expected), str(record_path)],
        capture_output=True, text=True, timeout=30, check=False, **no_window_kwargs(),
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["expected"] == expected
    assert result["worktree_qualified"] is True
    assert result["launch_authorized"] is False


@pytest.mark.parametrize("raw", [
    "not-json", "{}", "[" * 2000 + "]" * 2000, '"' + "a" * 8192 + '"',
    '{"target_epoch": {"platform": []}}',
])
def test_malformed_cli_payload_is_a_json_error_not_a_read(subject, monkeypatch, capfd, raw):
    from agent_worktrees import __main__ as core
    owner, record, expected = subject
    monkeypatch.setattr(
        session_metadata_cli.tracking, "load_record", lambda _: pytest.fail("invalid payload read"),
    )
    args = core.build_parser().parse_args(["session-role", "--restart-check", raw, "--json"])
    assert session_metadata_cli.cmd_session_role(args) != 0
    assert "Could not check native restart worktree" in json.loads(capfd.readouterr().out)["error"]


def test_released_claim_metadata_is_not_current_policy_proof(subject):
    owner, record, expected = subject
    claim = tracking.ResourceClaim(kind="task", ref="dispatch-task:old-task", state="released")
    record.resources.append(claim)
    assert owner.check(expected, loader=lambda: record)["reason"] == "claim-policy-unqualified"


@pytest.mark.parametrize("failure", [
    PermissionError("mux socket denied"),
    subprocess.TimeoutExpired(["tmux", "list-sessions"], 5),
    subprocess.CompletedProcess(["tmux", "list-sessions"], 1, stdout="", stderr="permission denied"),
])
def test_failed_real_mux_listing_never_qualifies(subject, monkeypatch, failure):
    owner, record, expected = subject
    monkeypatch.setattr(owner.sessions, "_list_mux_sessions", real_mux_listing)

    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(subprocess, "run", run)
    result = owner.check(expected, loader=lambda: record)
    assert result["reason"] == "mux-state-unknown"
    assert result["worktree_qualified"] is False
    assert result["launch_authorized"] is False


@pytest.mark.parametrize("listing,qualified,reason", [
    (None, False, "mux-state-unknown"),
    ({}, True, "current-worktree-matches"),
    ({"unrelated": 1}, True, "current-worktree-matches"),
])
def test_mux_listing_distinguishes_uncertainty_from_confirmed_absence(
    subject, monkeypatch, listing, qualified, reason,
):
    owner, record, expected = subject
    monkeypatch.setattr(owner.sessions, "_list_mux_sessions", lambda: listing)
    result = owner.check(expected, loader=lambda: record)
    assert result["worktree_qualified"] is qualified
    assert result["reason"] == reason


def test_existing_mux_uses_canonical_encoded_identity(subject, monkeypatch):
    owner, record, expected = subject
    record = replace(record, worktree_id=record.worktree_id + ".dotted")
    expected = {**expected, "worktree_id": record.worktree_id}
    monkeypatch.setattr(
        owner.sessions, "_list_mux_sessions",
        lambda: {owner.sessions.mux_session_name(record.worktree_id): 0},
    )
    assert owner.check(expected, loader=lambda: record)["reason"] == "existing-mux"
