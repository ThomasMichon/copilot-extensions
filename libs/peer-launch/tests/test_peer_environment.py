"""``peer_environment``'s per-plugin forwarding allowlists."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "peer_launch", ROOT / "libs" / "peer-launch" / "peer_launch.py"
)
peer_launch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peer_launch)


def _context(plugin: str) -> dict[str, str]:
    return {
        "pluginId": plugin,
        "installReceipt": "receipt",
        "payloadRoot": "/payload",
        "pluginRoot": "/plugin",
        "stateRoot": "/plugin/state",
    }


def test_agent_dispatch_forwards_control_token_command():
    """A peer-launched agent-dispatch client must receive the on-demand
    control-token fetch command, not just the raw token -- otherwise a
    deployment that configures only the command (never the raw secret)
    silently produces an unauthenticated peer-launched client."""
    inherited = {
        "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND": "vault get control-token",
        "AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND": "vault get shared-control-token",
    }
    env = peer_launch.peer_environment(_context("agent-dispatch"), inherited)
    assert env["AGENT_DISPATCH_CONTROL_TOKEN_COMMAND"] == "vault get control-token"
    assert (
        env["AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND"]
        == "vault get shared-control-token"
    )


def test_agent_dispatch_forwards_raw_control_token():
    inherited = {"AGENT_DISPATCH_CONTROL_TOKEN": "raw-control-tok"}
    env = peer_launch.peer_environment(_context("agent-dispatch"), inherited)
    assert env["AGENT_DISPATCH_CONTROL_TOKEN"] == "raw-control-tok"


def test_agent_dispatch_omits_unset_control_token_keys():
    env = peer_launch.peer_environment(_context("agent-dispatch"), {})
    assert "AGENT_DISPATCH_CONTROL_TOKEN" not in env
    assert "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND" not in env


def test_pr_owner_roster_and_state_rebinding():
    assert peer_launch.PEERS["agent-pull-requests"] == "agent_pull_requests"
    assert "agent-pull-requests" in peer_launch.OWNERS
    env = peer_launch.peer_environment(_context("agent-pull-requests"), {
        "AGENT_PULL_REQUESTS_HOME": "/foreign/state",
        "AGENT_PULL_REQUESTS_PAYLOAD_ROOT": "/foreign/payload",
        "AGENT_PULL_REQUESTS_UNRELATED_OVERRIDE": "foreign",
    })
    assert env["AGENT_PULL_REQUESTS_HOME"] == "/plugin/state"
    assert env["AGENT_PULL_REQUESTS_PAYLOAD_ROOT"] == "/payload"
    assert "AGENT_PULL_REQUESTS_UNRELATED_OVERRIDE" not in env


def test_other_peers_never_inherit_pr_owner_state():
    env = peer_launch.peer_environment(_context("agent-worktrees"), {
        "AGENT_PULL_REQUESTS_HOME": "/foreign/state",
        "AGENT_PULL_REQUESTS_UNRELATED_OVERRIDE": "foreign",
    })
    assert not any(name.startswith("AGENT_PULL_REQUESTS_") for name in env)


def test_machines_peer_rebinds_its_owner_without_foreign_runtime_overrides():
    assert peer_launch.PEERS["agent-machines"] == "agent_machines"
    env = peer_launch.peer_environment(_context("agent-machines"), {
        "AGENT_MACHINES_PAYLOAD_ROOT": "/foreign",
        "AGENT_RT_ROOT": "/other",
        "AGENT_WORKTREES_PAYLOAD_ROOT": "/caller",
        "HOME": "/user",
    })
    assert env["AGENT_RT_ROOT"] == "/plugin"
    assert env["AGENT_MACHINES_PAYLOAD_ROOT"] == "/payload"
    assert env["HOME"] == "/user"
    assert "AGENT_WORKTREES_PAYLOAD_ROOT" not in env


def test_agent_dispatch_retains_authority_and_separate_shared_control():
    inherited = {
        "AGENT_DISPATCH_TASK_AUTHORITY": "central",
        "AGENT_DISPATCH_SHARED_CONTROL_TOKEN": "shared-control",
    }
    env = peer_launch.peer_environment(_context("agent-dispatch"), inherited)
    assert env["AGENT_DISPATCH_TASK_AUTHORITY"] == "central"
    assert env["AGENT_DISPATCH_SHARED_CONTROL_TOKEN"] == "shared-control"
    other = peer_launch.peer_environment(_context("agent-worktrees"), inherited)
    assert "AGENT_DISPATCH_TASK_AUTHORITY" not in other
    assert "AGENT_DISPATCH_SHARED_CONTROL_TOKEN" not in other
