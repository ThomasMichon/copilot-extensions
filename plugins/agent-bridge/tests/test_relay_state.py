"""Tests for live-relay-port sourcing of the git-credential-relay auth hook.

The daemon's actually-bound relay port takes precedence over the statically
declared machines.yaml port, so an ephemeral-fallback relay is honored and
machines.yaml need not hardcode the port per machine.
"""

from __future__ import annotations

import pytest

from agent_bridge import relay_state
from agent_bridge.transport import RELAY_HOOK_NAME, _effective_auth_hooks


@pytest.fixture(autouse=True)
def _reset_live_port():
    relay_state.set_live_relay_port(None)
    yield
    relay_state.set_live_relay_port(None)


def _relay_hook(port: int) -> dict:
    return {
        "name": RELAY_HOOK_NAME,
        "local_port": port,
        "remote_port": port,
        "env": {"LC_GIT_CREDENTIAL_RELAY": str(port)},
    }


def test_no_live_relay_declared_hook_passthrough():
    hooks = [_relay_hook(9857)]
    out = _effective_auth_hooks(hooks)
    assert len(out) == 1
    assert out[0]["local_port"] == 9857
    assert out[0]["remote_port"] == 9857
    assert out[0]["env"]["LC_GIT_CREDENTIAL_RELAY"] == "9857"


def test_live_relay_overrides_declared_port():
    relay_state.set_live_relay_port(41999)
    out = _effective_auth_hooks([_relay_hook(9857)])
    assert len(out) == 1
    assert out[0]["local_port"] == 41999
    assert out[0]["remote_port"] == 41999
    assert out[0]["env"]["LC_GIT_CREDENTIAL_RELAY"] == "41999"


def test_live_relay_synthesizes_when_undeclared():
    relay_state.set_live_relay_port(42000)
    out = _effective_auth_hooks([])
    assert len(out) == 1
    assert out[0]["name"] == RELAY_HOOK_NAME
    assert out[0]["local_port"] == 42000
    assert out[0]["env"]["LC_GIT_CREDENTIAL_RELAY"] == "42000"


def test_no_live_no_declared_emits_nothing():
    assert _effective_auth_hooks([]) == []


def test_non_relay_hooks_pass_through_untouched():
    other = {"name": "some-other", "local_port": 1234, "remote_port": 1234, "env": {"X": "1"}}
    relay_state.set_live_relay_port(42001)
    out = _effective_auth_hooks([other])
    # the non-relay hook is preserved verbatim; a relay hook is synthesized too
    assert other in out
    assert any(h["name"] == RELAY_HOOK_NAME and h["local_port"] == 42001 for h in out)


def test_preserves_extra_env_on_declared_relay_hook():
    relay_state.set_live_relay_port(42002)
    hook = _relay_hook(9857)
    hook["env"]["EXTRA"] = "keep"
    out = _effective_auth_hooks([hook])
    assert out[0]["env"]["EXTRA"] == "keep"
    assert out[0]["env"]["LC_GIT_CREDENTIAL_RELAY"] == "42002"
