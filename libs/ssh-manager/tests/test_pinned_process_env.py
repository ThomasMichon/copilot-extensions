"""A config source's pinned environment reaches every SSH subprocess for its target.

A CodeSpace's SSH config is fetched as its owning gh account (``GH_TOKEN``). On
Windows each SSH subprocess runs that config's ProxyCommand (``gh cs ssh
--stdio``) as a child, so a forward or relay spawned with the ambient environment
authenticates as whichever gh account happens to be active and gets HTTP 404 for
a CodeSpace owned by another account.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ssh_manager.forward as forward_mod
import ssh_manager.proxy as proxy
import ssh_manager.proxy_jump as proxy_jump
import ssh_manager.relay_channel as relay_mod
import ssh_manager.reverse_candidates as candidate_mod
from ssh_manager.codespace_source import CodespaceConfigSource
from ssh_manager.config_sources import SSHConfig
from ssh_manager.forward import LocalForward
from ssh_manager.relay_channel import SupervisedRelayForward, _SettleResult
from ssh_manager.reverse_candidates import FiniteReverseForward, ReverseEndpoint, ReverseEvidence

_RAW = """Host cs.fixture-box.main
    User vscode
    ProxyCommand gh cs ssh -c fixture-box --stdio -- -i /home/u/.ssh/cs
    IdentityFile /home/u/.ssh/codespaces.auto
"""
PINNED = {"PATH": "fixture-path", "GH_TOKEN": "synthetic-owner-token"}


def _pinned_config(**overrides) -> SSHConfig:
    return SSHConfig(host_alias="cs.fixture-box.main", user="vscode", process_env=dict(PINNED), **overrides)


def test_codespace_source_carries_its_pinned_environment(tmp_path, monkeypatch):
    source = CodespaceConfigSource("fixture-box", config_dir=tmp_path, gh_env=dict(PINNED))
    monkeypatch.setattr(source, "_fetch_gh_config", lambda: _RAW)
    assert source.refresh().process_env == PINNED

    ambient = CodespaceConfigSource("fixture-box", config_dir=tmp_path)
    monkeypatch.setattr(ambient, "_fetch_gh_config", lambda: _RAW)
    assert ambient.refresh().process_env is None


def test_pinned_environment_is_not_identity_equality_or_repr():
    pinned = _pinned_config()
    plain = SSHConfig(host_alias="cs.fixture-box.main", user="vscode")
    assert pinned == plain
    assert pinned.connection_identity == plain.connection_identity
    assert "synthetic-owner-token" not in repr(pinned)


def test_ssh_process_env_prefers_pinned_and_applies_extras(monkeypatch):
    monkeypatch.setattr(os, "environ", {"PATH": "ambient", "GH_TOKEN": "ambient-token"})
    assert proxy.ssh_process_env(SSHConfig(host_alias="box")) is None
    assert proxy.ssh_process_env(SSHConfig(host_alias="box"), {"X": "1"}) == {
        "PATH": "ambient", "GH_TOKEN": "ambient-token", "X": "1",
    }
    assert proxy.ssh_process_env(_pinned_config(), {"X": "1"}) == {**PINNED, "X": "1"}


@pytest.mark.parametrize("windows", [False, True])
async def test_create_ssh_subprocess_defaults_to_the_pinned_environment(monkeypatch, windows):
    monkeypatch.setattr(proxy, "_is_windows", lambda: windows)
    monkeypatch.setattr(os, "environ", {"GH_TOKEN": "ambient-token"})
    process = SimpleNamespace(wait=AsyncMock(return_value=0), pid=1, returncode=None)
    spawn = AsyncMock(return_value=(process, None))
    brokers = []
    metadata_environments = []

    def profile(host, **kwargs):
        metadata_environments.append(kwargs["process_env"])
        return SimpleNamespace(
            get_ssh_config=lambda: SSHConfig(host, process_env=kwargs["process_env"]),
        )

    def make_broker(command, env, **kwargs):
        brokers.append(env)
        return SimpleNamespace(start=AsyncMock(return_value=34567), close=AsyncMock())

    monkeypatch.setattr(proxy, "spawn_in_kill_on_close_job", spawn)
    monkeypatch.setattr(proxy, "_ProxyBroker", make_broker)
    monkeypatch.setattr(proxy, "_resolve_shell", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy, "_split_windows_command", lambda command: ["proxy"])
    monkeypatch.setattr(proxy, "_client_command", lambda *args, **kwargs: "client")
    monkeypatch.setattr(proxy, "register_process_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(proxy_jump, "SSHProfileSource", profile)
    config = _pinned_config(proxy_command="gh cs ssh -c fixture-box --stdio")

    await proxy.create_ssh_subprocess("ssh", "target", config=config)
    assert spawn.await_args.kwargs["env"] == PINNED
    if windows:
        # The ProxyCommand child (gh cs ssh --stdio) authenticates as the owner.
        assert brokers == [PINNED]

    spawn.reset_mock()
    await proxy.create_ssh_subprocess("ssh", "target", config=config, env={"EXPLICIT": "1"})
    assert spawn.await_args.kwargs["env"] == {"EXPLICIT": "1"}
    assert metadata_environments == ([PINNED, {"EXPLICIT": "1"}] if windows else [])


async def test_local_forward_spawns_with_the_pinned_environment(monkeypatch):
    seen = []

    async def spawn(*args, **kwargs):
        seen.append(kwargs.get("env"))
        return SimpleNamespace(returncode=None, stderr=None, kill=lambda: None)

    async def accepts(port):
        return True

    monkeypatch.setattr(forward_mod, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(LocalForward, "_port_accepts", staticmethod(accepts))
    await LocalForward(_pinned_config(), 4321, local_port=49999).establish()
    assert seen == [PINNED]


async def test_credential_relay_spawns_with_the_pinned_environment(monkeypatch):
    seen = []

    async def spawn(*args, **kwargs):
        seen.append(kwargs["env"])
        return SimpleNamespace(pid=7, returncode=None, stderr=None)

    async def settled(self, proc):
        return _SettleResult(True, "", False)

    monkeypatch.setattr(relay_mod, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(relay_mod, "process_identity", lambda pid: f"id-{pid}")
    monkeypatch.setattr(SupervisedRelayForward, "_wait_settled", settled)
    relay = SupervisedRelayForward(_pinned_config(), 41000)
    await relay.establish()
    assert seen[0]["GH_TOKEN"] == "synthetic-owner-token"
    assert seen[0]["PATH"] == "fixture-path"
    assert set(seen[0]) - set(PINNED)  # still carries the relay's owner marker


async def test_reverse_candidates_spawn_with_the_pinned_environment(monkeypatch):
    seen = []

    async def spawn(*args, **kwargs):
        seen.append(kwargs["env"])
        return SimpleNamespace(pid=100 + len(seen), returncode=None, stderr=None)

    async def settled(self, proc):
        return _SettleResult(True, "")

    async def observe(attempt):
        return ReverseEvidence(attempt.attempt_id, "ready")

    monkeypatch.setattr(candidate_mod, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(FiniteReverseForward, "_wait_settled", settled)
    relay = FiniteReverseForward(
        _pinned_config(), (ReverseEndpoint("127.0.0.1", 41001),),
        request_id="operation-a", observe=observe, ready_timeout=1,
    )
    await relay.establish()
    assert seen and seen[0]["GH_TOKEN"] == "synthetic-owner-token"
    assert set(seen[0]) - set(PINNED)
