"""Tests for live-relay-port sourcing of the git-credential-relay auth hook.

The daemon's actually-bound relay port takes precedence over the statically
declared machines.yaml port, and is published to the primary config dir so a
sibling/elevated sub-daemon (which reuses the primary's relay) can discover it.
"""

from __future__ import annotations

import pytest

from agent_bridge import relay_state
from agent_bridge.transport import RELAY_HOOK_NAME, _effective_auth_hooks


@pytest.fixture(autouse=True)
def _isolate_relay_state(tmp_path, monkeypatch):
    # Point the config dir at a per-test temp dir so publishing the relay-port
    # file never touches the real ~/.agent-bridge, and reset the in-process value.
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    relay_state._live_relay_port = None
    yield
    relay_state._live_relay_port = None


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


# --- cross-daemon publish (approach B) ---------------------------------------

def test_publishes_port_to_config_dir(tmp_path):
    relay_state.set_live_relay_port(43000)
    assert (tmp_path / "relay-port").read_text(encoding="utf-8").strip() == "43000"


def test_sibling_reads_published_port_when_inprocess_none(tmp_path):
    relay_state.set_live_relay_port(43001)      # primary hosts + publishes
    relay_state._live_relay_port = None         # simulate a sibling/elevated daemon
    assert relay_state.get_live_relay_port() == 43001


def test_clear_removes_published_file(tmp_path):
    relay_state.set_live_relay_port(43002)
    assert (tmp_path / "relay-port").exists()
    relay_state.set_live_relay_port(None)
    assert not (tmp_path / "relay-port").exists()


def test_elevated_subdir_resolves_to_primary(tmp_path, monkeypatch):
    # Primary publishes at <primary>/relay-port ...
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    relay_state.set_live_relay_port(43003)
    # ... and an elevated sub-daemon (config dir <primary>/elevated) reads it
    # from the parent, so both agree on the same file.
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path / "elevated"))
    relay_state._live_relay_port = None
    assert relay_state.get_live_relay_port() == 43003


def test_get_returns_none_when_no_file_and_no_inprocess(tmp_path):
    relay_state._live_relay_port = None
    assert relay_state.get_live_relay_port() is None


def test_inprocess_value_takes_precedence_over_file(tmp_path):
    relay_state.set_live_relay_port(43004)      # file + in-process = 43004
    relay_state._live_relay_port = 55555        # in-process diverges
    assert relay_state.get_live_relay_port() == 55555
