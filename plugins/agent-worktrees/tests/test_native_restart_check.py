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


def test_canonical_self_claim_is_provisional_not_task_or_launch_authority(subject, monkeypatch):
    from agent_worktrees import claim_providers

    owner, record, expected = subject
    record.resources.append(tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=NATIVE,
        ),
    ))
    monkeypatch.setattr(
        claim_providers, "resolve_claim_status", lambda *a, **kw: pytest.fail("self-claim callback"),
    )
    before = asdict(record)
    result = owner.check(expected, loader=lambda: record)
    assert result["worktree_qualified"] is True
    assert result["launch_authorized"] is False and result["automatic_eligible"] is False
    assert "task_policies" not in result
    assert asdict(record) == before


@pytest.mark.parametrize("damage", [
    "other-session", "legacy-ref", "producer", "opaque", "released", "unknown",
    "duplicate", "task-mixed", "inbound", "released-ledger", "missing-machine",
])
def test_noncanonical_self_claim_never_reaches_task_or_process_probe(subject, monkeypatch, damage):
    from agent_worktrees import claim_providers

    owner, record, expected = subject
    claim = tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=NATIVE,
        ),
    )
    record.resources = [claim]
    if damage == "other-session":
        claim.ref = claim.ref.replace(NATIVE, "00000000-0000-4000-8000-000000000002")
    elif damage == "legacy-ref":
        claim.ref = tracking.format_claim_ref(None, None, record.worktree_id, session=NATIVE)
    elif damage == "producer":
        claim.producer_present = True
    elif damage == "opaque":
        claim.metadata_opaque = True
    elif damage in {"released", "unknown"}:
        claim.state = damage
    elif damage == "duplicate":
        record.resources.append(replace(claim))
    elif damage == "task-mixed":
        record.resources.append(tracking.ResourceClaim(kind="task", ref="dispatch-local-task:t"))
    elif damage == "inbound":
        record.owner_ref = "other/project/worktree"
    elif damage == "released-ledger":
        record.last_finalize_released = [claim]
    else:
        record.machine = ""
    monkeypatch.setattr(
        claim_providers, "resolve_claim_status", lambda *a, **kw: pytest.fail("claim callback"),
    )
    monkeypatch.setattr(owner, "session_liveness", lambda *a: pytest.fail("process probe"))
    result = owner.check(expected, loader=lambda: record)
    assert result["reason"] == "claim-policy-unqualified"
    assert result["worktree_qualified"] is False and result["launch_authorized"] is False


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


@pytest.mark.parametrize("with_task", [False, True])
def test_actual_child_reads_persisted_owner_not_parent_session(subject, tmp_path, with_task):
    owner, record, expected = subject
    if with_task:
        record.resources.append(tracking.ResourceClaim(kind="task", ref="dispatch-local-task:task-1"))
    record_path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, record_path)
    before = record_path.read_bytes()
    code = """
import json, os, sys
from pathlib import Path
from agent_worktrees import native_restart_check as owner, session_metadata_cli as cli
from agent_worktrees import __main__ as core, claim_providers as cp
expected = json.loads(sys.argv[1])
cli.cfg.tracking_dir = lambda: Path(sys.argv[2]).parent
owner.native_source_context.epoch = lambda: expected["target_epoch"]
owner.sessions.session_has_conversation_data = lambda _: True
owner.sessions._list_mux_sessions = lambda: {}
owner.session_liveness = lambda _: "dead"
record = owner.tracking.load_record(Path(sys.argv[2]))
policy = {
    "schema": "native-restart-task-policy", "version": 1, "task_id": "task-1",
    "repo": record.repo, "state": "started", "owner": record.machine + "/" + record.worktree_id,
    "owner_session_id": expected["native_session_id"], "generation": 7,
    "awaiting_steer": False, "pending_steer": False, "resume_requested": False, "hold_reason": None,
    "hold_actor": None, "hold_at": None, "run_waiter": False, "task_qualified": True,
    "launch_authorized": False, "automatic_eligible": False, "reason": "current-task-matches",
}
callback = (
    "import json,os,sys; "
    "assert sys.argv[1:] == ['--local','claim-status','task-1','--native-restart-policy']; "
    "assert os.environ['AGENT_DISPATCH_NO_SELFPROVISION'] == '1'; "
    "print(" + repr(json.dumps({"exists": True, "native_restart_policy": policy})) + ")"
)
provider = cp.ClaimProviderManifest(
    namespace="dispatch-local-task", plugin="agent-dispatch@copilot-extensions",
    plugin_root=str(Path(sys.argv[2]).parent),
    status_command=(sys.executable, "-c", callback, "--local"),
    no_self_provision_env="AGENT_DISPATCH_NO_SELFPROVISION",
)
cp.discover_claim_providers = lambda _: ({"dispatch-local-task": provider}, ())
cp.peer_launch_adapter.explicit_context = lambda: False
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
    assert bool(result.get("task_policies")) is with_task
    assert record_path.read_bytes() == before


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


@pytest.fixture
def task_policy(subject, monkeypatch):
    from agent_worktrees import claim_providers

    owner, record, expected = subject
    record.resources.append(tracking.ResourceClaim(kind="task", ref="dispatch-local-task:task-1"))
    policy = {
        "schema": "native-restart-task-policy", "version": 1,
        "task_id": "task-1", "repo": record.repo, "state": "started",
        "owner": f"{record.machine}/{record.worktree_id}", "owner_session_id": NATIVE,
        "generation": 7, "awaiting_steer": False, "pending_steer": False, "resume_requested": False,
        "hold_reason": None, "hold_actor": None, "hold_at": None, "run_waiter": False,
        "task_qualified": True, "launch_authorized": False, "automatic_eligible": False,
        "reason": "current-task-matches",
    }
    calls = []

    def resolve(ref, **kwargs):
        calls.append((ref, kwargs))
        return {"available": True, "exists": True, "native_restart_policy": policy}

    monkeypatch.setattr(claim_providers, "resolve_claim_status", resolve)
    return owner, record, expected, policy, calls


def test_current_task_binds_original_native_owner_without_granting_launch(task_policy):
    owner, record, expected, policy, calls = task_policy
    result = owner.check(expected, loader=lambda: record)
    assert result["worktree_qualified"] is True
    assert result["task_policies"] == [{"ref": record.resources[0].ref, "policy": policy}]
    assert result["launch_authorized"] is False
    assert calls == [(record.resources[0].ref, {"native_restart_policy": True, "timeout": 2})]


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"version": True}, {"version": 2},
    {"owner": "other/worktree"}, {"repo": "other"},
    {"owner_session_id": "00000000-0000-4000-8000-000000000002"},
    {"task_id": "different"}, {"state": "suspended"}, {"state": "completed"},
    {"state": "queued"}, {"state": "claimed"}, {"state": "submitted"},
    {"generation": 0}, {"generation": True}, {"generation": 2**64},
    {"awaiting_steer": True}, {"resume_requested": True}, {"hold_reason": "hold"},
    {"hold_actor": "operator"}, {"hold_at": 1}, {"run_waiter": True},
    {"task_qualified": 1}, {"launch_authorized": True}, {"automatic_eligible": True},
    {"pending_steer": True}, {"pending_steer": 0},
])
def test_task_owner_or_policy_changes_refuse_even_if_provider_says_qualified(task_policy, change):
    owner, record, expected, policy, calls = task_policy
    policy.update(change)
    result = owner.check(expected, loader=lambda: record)
    assert result["worktree_qualified"] is False
    assert result["launch_authorized"] is False
    assert result["reason"] == "task-policy-unqualified"


@pytest.mark.parametrize("field", [
    "generation", "hold_reason", "hold_actor", "hold_at", "awaiting_steer", "run_waiter",
    "launch_authorized", "automatic_eligible", "pending_steer",
])
def test_incomplete_task_policy_never_qualifies(task_policy, field):
    owner, record, expected, policy, calls = task_policy
    del policy[field]
    assert owner.check(expected, loader=lambda: record)["reason"] == "task-policy-unqualified"


@pytest.mark.parametrize("ref", [
    "task-1", "dispatch-task:task-1", "dispatch-central-task:--unsafe",
    "dispatch-local-task: task-1", "codespace:task-1",
])
def test_never_guess_default_authority_from_legacy_task_ref(task_policy, ref):
    owner, record, expected, policy, calls = task_policy
    record.resources[0].ref = ref
    assert owner.check(expected, loader=lambda: record)["reason"] == "task-policy-unqualified"
    assert calls == []


def test_dispatch_provenance_requires_corresponding_current_task_ref(task_policy):
    owner, record, expected, policy, calls = task_policy
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="different-task", reservation_key="key", attempt=1,
        driver="native", supervisor="supervisor", creator_machine=record.machine,
    )
    assert owner.check(expected, loader=lambda: record)["reason"] == "task-policy-unqualified"
    assert calls == []


def test_parsed_dispatch_provenance_is_not_a_substitute_for_current_task(task_policy):
    owner, record, expected, policy, calls = task_policy
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id=policy["task_id"], reservation_key="key", attempt=1,
        driver="native", supervisor="supervisor", creator_machine=record.machine,
    )
    record.dispatch_attempt_raw_present = True
    assert owner.check(expected, loader=lambda: record)["worktree_qualified"] is True
    policy["generation"] += 1
    policy["owner_session_id"] = "00000000-0000-4000-8000-000000000002"
    assert owner.check(expected, loader=lambda: record)["worktree_qualified"] is False


@pytest.mark.parametrize("change", [
    {"state": "released"}, {"state": "at-rest"}, {"producer_present": True},
    {"kind": "pr"}, {"state": "unknown"}, {"metadata_opaque": True},
])
def test_nonactive_or_other_claims_remain_unqualified(task_policy, change):
    owner, record, expected, policy, calls = task_policy
    record.resources[0] = replace(record.resources[0], **change)
    assert owner.check(expected, loader=lambda: record)["reason"] == "claim-policy-unqualified"
    assert calls == []


def test_mixed_claims_do_not_create_an_unbounded_probe_loop(task_policy):
    owner, record, expected, policy, calls = task_policy
    record.resources.append(tracking.ResourceClaim(kind="task", ref="dispatch-central-task:other"))
    assert owner.check(expected, loader=lambda: record)["reason"] == "claim-policy-unqualified"
    assert calls == []


@pytest.mark.parametrize("state", ["unknown", "", None, False, [], {}])
def test_persisted_unknown_claim_state_never_becomes_restart_qualification(task_policy, tmp_path, state):
    import yaml

    owner, record, expected, policy, calls = task_policy
    path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    raw = yaml.safe_load(path.read_text())
    raw["resources"][0]["state"] = state
    path.write_text(yaml.safe_dump(raw))
    loaded = tracking.load_record(path)
    assert loaded.resources[0].is_live is True  # Preserve conservative finality handling.
    result = owner.check(expected, loader=lambda: tracking.load_record(path))
    assert result["worktree_qualified"] is False
    assert result["reason"] == "claim-policy-unqualified"
    assert calls == []
    tracking.save_record(loaded, path)
    assert owner.check(expected, loader=lambda: tracking.load_record(path))["worktree_qualified"] is False


@pytest.mark.parametrize("resources", [None, {}, "unknown", [None], [{}]])
def test_malformed_persisted_claim_collection_cannot_look_unclaimed(task_policy, tmp_path, resources):
    import yaml

    owner, record, expected, policy, calls = task_policy
    path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    raw = yaml.safe_load(path.read_text())
    raw["resources"] = resources
    path.write_text(yaml.safe_dump(raw))
    loaded = tracking.load_record(path)
    assert owner.check(expected, loader=lambda: loaded)["worktree_qualified"] is False
    tracking.save_record(loaded, path)
    reloaded = tracking.load_record(path)
    assert owner.check(expected, loader=lambda: reloaded)["worktree_qualified"] is False
    assert calls == []


@pytest.mark.parametrize("envelope", [
    {"available": False}, {"available": True, "exists": False},
    {"available": True, "exists": True}, {"available": 1, "exists": True},
])
def test_unavailable_absent_or_legacy_provider_cannot_prove_task_policy(task_policy, monkeypatch, envelope):
    from agent_worktrees import claim_providers

    owner, record, expected, policy, calls = task_policy
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a, **kw: envelope)
    assert owner.check(expected, loader=lambda: record)["worktree_qualified"] is False


def test_task_policy_change_during_callback_invalidates_worktree_snapshot(task_policy, monkeypatch):
    from agent_worktrees import claim_providers

    owner, record, expected, policy, calls = task_policy

    def resolve(*args, **kwargs):
        record.paused = True
        return {"available": True, "exists": True, "native_restart_policy": policy}

    monkeypatch.setattr(claim_providers, "resolve_claim_status", resolve)
    assert owner.check(expected, loader=lambda: record)["reason"] == "owner-changed"


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
