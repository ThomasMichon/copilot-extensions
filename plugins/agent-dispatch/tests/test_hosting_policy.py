"""Execution-harness vetoes precede managed launch effects."""

import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from agent_dispatch import bridge, bridge_reclaim, embody, hosting_policy, restricted_spawn
from agent_dispatch.procutil import no_window_kwargs


def test_validated_selected_marketplace_overlay_is_checked_fresh(tmp_path, monkeypatch):
    from agent_dispatch import peer_launch

    (tmp_path / ".git").mkdir()
    base = tmp_path / ".copilot-extensions" / "agent-worktrees"
    overlay = base / "marketplaces" / "selected" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    (base / "config.yaml").write_text("expose_agent: true\n")
    overlay.write_text("expose_agent: false\n")
    monkeypatch.setenv(peer_launch.CONTEXT_ENV, "selected-installation-receipt")
    validated = Mock(return_value={"marketplaceId": "selected"})
    monkeypatch.setattr(peer_launch, "validate_owner", validated)
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", lambda: ["selected-aw"])
    process = Mock(side_effect=AssertionError("denial must precede peer processes"))
    monkeypatch.setattr(hosting_policy.subprocess, "run", process)
    with pytest.raises(bridge.BridgeUnavailable, match="expose_agent: false"):
        hosting_policy.require_execution_harness(path=str(tmp_path))
    validated.assert_called_once()
    assert validated.call_args.args[0] == "agent-dispatch"
    assert validated.call_args.args[2] == "selected-installation-receipt"
    process.assert_not_called()


@pytest.mark.parametrize("raw", [" ", "{not-json}", "missing-installation-receipt"])
def test_explicit_invalid_context_never_falls_back_to_unscoped_policy(tmp_path, monkeypatch, raw):
    from agent_dispatch import peer_launch

    monkeypatch.setenv(peer_launch.CONTEXT_ENV, raw)
    monkeypatch.setattr(peer_launch, "validate_owner", Mock(side_effect=ValueError("invalid receipt")))
    policy = Mock(side_effect=AssertionError("must not read an unscoped policy"))
    prefix = Mock(side_effect=AssertionError("must not select another runtime"))
    monkeypatch.setattr(hosting_policy, "require_agent_hosting", policy)
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", prefix)
    with pytest.raises(bridge.BridgeUnavailable, match="installation context refused"):
        hosting_policy.require_execution_harness(path=str(tmp_path))
    policy.assert_not_called()
    prefix.assert_not_called()


def test_denied_actual_checkout_has_no_process_or_seed_effects(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n")
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", lambda: ["selected-aw"])
    effects = Mock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(hosting_policy.subprocess, "run", effects)
    with pytest.raises(bridge.BridgeUnavailable, match="expose_agent: false"):
        hosting_policy.require_execution_harness(path=str(tmp_path))
    effects.assert_not_called()


def test_selected_project_not_task_lane_is_checked(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", lambda: ["selected-aw"])

    def run(argv, **kwargs):
        calls.append(argv)
        result = (
            {"path": str(tmp_path)} if argv[1:3] == ["repos", "find"]
            else {"version": 1, "allowed": True, "repository": str(tmp_path), "expose_agent": None,
                  "sources": [], "denied_by": []}
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(hosting_policy.subprocess, "run", run)
    result = hosting_policy.require_execution_harness(project="execution-harness")
    assert calls[0][1:] == ["repos", "find", "execution-harness", "--registered-only", "--json"]
    assert len(calls) == 1
    assert result["allowed"] is True
    assert result["repository"] == str(tmp_path)


@pytest.mark.parametrize("output", [
    "{}", "[]", "not JSON",
    json.dumps({"version": 1, "allowed": True, "repository": "", "expose_agent": None,
                "sources": [], "denied_by": []}),
    json.dumps({"version": 1, "allowed": True, "repository": "/execution", "expose_agent": None,
                "sources": ["relative.yaml"], "denied_by": []}),
    json.dumps({"version": 1, "allowed": True, "repository": "/execution", "expose_agent": None,
                "sources": [], "denied_by": [], "error": None}),
])
def test_unsupported_receiver_never_grants_admission(tmp_path, monkeypatch, output):
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", lambda: ["selected-aw"])
    monkeypatch.setattr(hosting_policy.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, output, ""))
    with pytest.raises(bridge.BridgeUnavailable, match="hosting|checkout"):
        hosting_policy.require_execution_harness(project="execution-harness")


def test_known_actual_path_is_authoritative_without_worktrees_runtime(tmp_path, monkeypatch):
    prefix = Mock(side_effect=AssertionError("known local paths do not require Worktrees"))
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", prefix)
    result = hosting_policy.require_execution_harness(path=str(tmp_path))
    assert result == {
        "version": 1, "allowed": True, "repository": str(tmp_path),
        "expose_agent": None, "sources": [], "denied_by": [],
    }
    prefix.assert_not_called()


def test_missing_execution_metadata_never_becomes_coordinator_cwd(monkeypatch):
    prefix = Mock(side_effect=AssertionError("no speculative runtime lookup"))
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", prefix)
    with pytest.raises(bridge.BridgeUnavailable, match="authoritative checkout"):
        hosting_policy.require_execution_harness()
    prefix.assert_not_called()


@pytest.mark.parametrize("denied", [False, True])
def test_direct_bridge_command_uses_actual_cwd_without_project_or_worktrees(tmp_path, monkeypatch, denied):
    if denied:
        policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
        policy.parent.mkdir(parents=True)
        policy.write_text("expose_agent: false\n")
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(bridge, "_resolve_agent_record", lambda *args, **kwargs: {
        "type": "command", "cwd": str(tmp_path), "project": "unrelated-task-lane",
    })
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix",
                        Mock(side_effect=AssertionError("must use actual cwd")))
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "{}", ""))
    monkeypatch.setattr(bridge.subprocess, "run", run)
    if denied:
        with pytest.raises(bridge.BridgeUnavailable, match="expose_agent: false"):
            bridge.spawn_worker("task", agent="direct", worker_id="worker")
        run.assert_not_called()
    else:
        assert bridge.spawn_worker("task", agent="direct", worker_id="worker").returncode == 0
        run.assert_called_once()


def test_embody_refuses_before_launch_and_seed(monkeypatch):
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["selected-aw"])
    monkeypatch.setattr(hosting_policy, "require_execution_harness",
                        Mock(side_effect=bridge.BridgeUnavailable("policy denied")))
    launch = Mock(side_effect=AssertionError("launch"))
    monkeypatch.setattr(embody.subprocess, "run", launch)
    with pytest.raises(bridge.BridgeUnavailable, match="policy denied"):
        embody.spawn_embodied_worker("task", worker_id="worker", project="harness")
    launch.assert_not_called()


def test_restricted_admission_precedes_claim_binding_and_credentials(monkeypatch):
    effects = []
    client = Mock()
    client.get_reservation.return_value = {
        "driver": restricted_spawn.DRIVER, "state": "reserving",
    }
    monkeypatch.setattr(restricted_spawn, "agent_bridge_launch_prefix", lambda: ["bridge"])

    def provider(spec, action, body):
        effects.append(action)
        raise bridge.BridgeUnavailable("guest repository denied")

    monkeypatch.setattr(restricted_spawn, "_provider", provider)
    with pytest.raises(bridge.BridgeUnavailable, match="guest repository denied"):
        restricted_spawn.start(
            {"id": "task", "_spawn_client": client, "spawn_reservation_key": "key"},
            spec={"name": "worker", "instance_id": "a" * 64, "command": ["provider"]},
        )
    assert effects == ["dispatch-check-hosting"]
    client.claim.assert_not_called()
    client.record_restricted_delivery.assert_not_called()
    client.task_grant.assert_not_called()


_LEGACY_RECEIVER = """
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--json', action='store_true')
commands = parser.add_subparsers(dest='command')
for command in ('create', 'resume', 'send', 'embody'):
    sub = commands.add_parser(command)
    sub.add_argument('target', nargs='?')
    sub.add_argument('prompt', nargs='?')
    sub.add_argument('--caller')
    sub.add_argument('--no-wait', action='store_true')
    sub.add_argument('--new', action='store_true')
    sub.add_argument('--seed')
    sub.add_argument('--driver')
    sub.add_argument('--json', action='store_true')
args = parser.parse_args()
print('LEGACY LAUNCH REACHED')
"""


@pytest.mark.parametrize("receiver", ["bridge", "worktrees", "reclaim"])
def test_mixed_version_owner_refuses_before_any_legacy_launch(monkeypatch, receiver):
    prefix = [sys.executable, "-c", _LEGACY_RECEIVER]
    # An old receiver would accept the same ordinary command without the
    # mandatory contract, so the regression exercises actual argparse refusal.
    control = subprocess.run(
        [*prefix, "embody", "--new"], capture_output=True, text=True, check=False,
        **no_window_kwargs(),
    )
    assert control.returncode == 0 and "LEGACY LAUNCH REACHED" in control.stdout
    if receiver == "bridge":
        monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: prefix)
        monkeypatch.setattr(bridge, "_resolve_agent_record", lambda *a, **kw: {"type": "remote"})
        result = bridge.spawn_worker("task", worker_id="worker", agent="namespace:target")
    elif receiver == "worktrees":
        monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: prefix)
        monkeypatch.setattr(hosting_policy, "require_embodiment_harness", lambda **kw: None)
        result = embody.spawn_embodied_worker("task", worker_id="worker")
    else:
        result = bridge_reclaim.resume_worktree_and_send(
            "worktree", "seed", exe=prefix, agent="worker", caller="dispatch",
            wait=False, json_output=True, timeout=10,
        )
    assert result.returncode == 2
    assert "--require-agent-hosting" in result.stderr
    assert "LEGACY LAUNCH REACHED" not in result.stdout
