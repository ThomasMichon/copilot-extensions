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
