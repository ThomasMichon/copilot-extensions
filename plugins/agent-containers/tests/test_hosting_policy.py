"""Guest admission is physical-instance-bound and precedes launch effects."""

import subprocess
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_containers import hosting_policy


def target():
    return SimpleNamespace(
        user="node", container_id="a" * 64, workspace_folder="/workspaces/source repo",
    )


def test_invalid_explicit_installation_context_precedes_guest_transport(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", " ")
    run = Mock(side_effect=AssertionError("invalid context must not reach a guest"))
    monkeypatch.setattr(hosting_policy.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="installation context refused"):
        hosting_policy.require_container_hosting(target())
    run.assert_not_called()


def test_guest_denial_uses_physical_instance_and_actual_checkout(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, "", "expose_agent: false")

    monkeypatch.setattr(hosting_policy.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="expose_agent: false"):
        hosting_policy.require_container_hosting(target())
    argv, kwargs = calls[0]
    assert argv[:6] == ["docker", "exec", "-u", "node", "a" * 64, "bash"]
    assert "cd -- '/workspaces/source repo'" in argv[-1]
    assert 'agent-policy check --path "$PWD"' in argv[-1]
    assert kwargs["check"] is False
    assert len(calls) == 1


def test_unsupported_guest_has_no_weaker_fallback(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 2, "", "unrecognized command"))
    monkeypatch.setattr(hosting_policy.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="receiver unsupported"):
        hosting_policy.require_container_hosting(target())
    assert run.call_count == 1


def test_prepared_execution_checkout_not_anchor_is_checked(monkeypatch):
    calls = []

    def run(_target, command):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({
            "version": 1, "allowed": True, "repository": "/workspaces/prepared", "expose_agent": None,
            "sources": [], "denied_by": [],
        }))

    monkeypatch.setattr(hosting_policy, "_run", run)
    monkeypatch.setattr("plugin_resolve.reflection.parse_execution_checkout",
                        lambda output: "/workspaces/prepared")
    hosting_policy.require_container_hosting(target(), worktree_id="worker-1")
    assert "--worktree-id worker-1" in calls[0]
    assert "cd -- /workspaces/prepared" in calls[1]
    assert "source repo" not in calls[1]


def test_launch_guard_preserves_extra_arguments_and_stops_on_denial():
    command = hosting_policy.guarded_launch("copilot --acp", "/workspaces/source")
    assert command.index("agent-policy check") < command.index("copilot --acp")
    assert 'copilot --acp "$@"' in command
    assert "|| return $?" in command
    assert command.endswith("repository_policy_launch")


def test_live_attachment_requires_receiver_support_but_not_a_new_launch_grant(monkeypatch):
    calls = []
    monkeypatch.setattr(hosting_policy, "_run",
                        lambda target, command: calls.append(command))
    hosting_policy.require_container_hosting(target(), live_attachment=True)
    assert len(calls) == 1
    assert "agent-policy check --help" in calls[0]
    assert "--path" not in calls[0]


@pytest.mark.parametrize("allowed", [False, True])
def test_portable_prepare_reads_actual_policy_before_projection_without_guest_owner(
    tmp_path, monkeypatch, allowed,
):
    import shlex
    import sys

    from agent_procutil import no_window_flags
    from agent_containers.config import ContainersConfig, FleetConfig
    from agent_containers.session_host_context import TrustedContext

    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(f"expose_agent: {str(allowed).lower()}\n")
    selected = "a" * 64
    context = TrustedContext(
        ContainersConfig(), FleetConfig(acp_command="copilot --acp"),
        "selected-user", "/actual", selected,
    )
    peer_context = json.dumps({
        "schema": "agent-bridge.receiving-policy", "version": 1, "marketplaceId": "selected",
    })
    native_run = subprocess.run
    seen = []

    def run(argv, **kwargs):
        assert argv[:6] == ["docker", "exec", "-i", "-u", "selected-user", selected]
        assert "agent-policy" not in argv[-1]
        words = shlex.split(argv[-1].split(" && ", 1)[1])
        words[0], words[-4] = sys.executable, str(tmp_path)
        seen.append(argv)
        return native_run(
            words, input=kwargs["input"], text=True, capture_output=True,
            timeout=10, creationflags=no_window_flags(),
        )

    monkeypatch.setattr("agent_containers.runtime_drivers.run_routed", lambda *a, **k: None)
    monkeypatch.setattr(hosting_policy.subprocess, "run", run)
    if allowed:
        command, _launcher, _count, instance = hosting_policy.session_host_launch_inputs(
            context, selected, policy_context=peer_context,
        )
        assert command == "copilot --acp" and instance == selected
    else:
        with pytest.raises(RuntimeError, match="expose_agent: false"):
            hosting_policy.session_host_launch_inputs(context, selected, policy_context=peer_context)
    assert len(seen) == 1
