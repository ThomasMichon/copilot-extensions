"""Windows probe ownership contracts; no native SSH process is launched."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ssh_manager import config_sources, process, proxy

from agent_ssh import probe


@pytest.mark.parametrize("returncode", [0, 255])
def test_windows_probe_uses_owned_profile_launcher(monkeypatch, returncode):
    config = config_sources.SSHConfig("target", effective_config=(("proxyjump", "jump"),))
    sources = []

    def source(name, **kwargs):
        sources.append((name, kwargs))
        return SimpleNamespace(get_ssh_config=lambda: config)

    child = SimpleNamespace(wait=AsyncMock(return_value=returncode))
    spawn = AsyncMock(return_value=child)
    cleanup = AsyncMock()
    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "selected-ssh")
    monkeypatch.setattr(config_sources, "SSHProfileSource", source)
    monkeypatch.setattr(proxy, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(process, "terminate_ssh_process_tree", cleanup)
    assert probe.probe_alias("target", 8) is (returncode == 0)
    assert sources == [("target", {
        "config_file": None, "resolve_timeout": 8, "ssh_executable": "selected-ssh",
    })]
    assert spawn.await_args.args[0] == "selected-ssh"
    assert spawn.await_args.args[-2:] == ("target", "exit 0")
    assert spawn.await_args.kwargs["config"] is config
    cleanup.assert_awaited_once_with(child)


def test_windows_probe_deadline_waits_for_owned_cleanup(monkeypatch):
    config = config_sources.SSHConfig("target")
    child = SimpleNamespace(wait=AsyncMock(side_effect=asyncio.TimeoutError))
    cleanup = AsyncMock()
    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "ssh")
    monkeypatch.setattr(
        config_sources, "SSHProfileSource",
        lambda *args, **kwargs: SimpleNamespace(get_ssh_config=lambda: config),
    )
    monkeypatch.setattr(proxy, "create_ssh_subprocess", AsyncMock(return_value=child))
    monkeypatch.setattr(process, "terminate_ssh_process_tree", cleanup)
    assert probe.probe_alias("target", 8) is False
    cleanup.assert_awaited_once_with(child)


def test_windows_probe_cleanup_error_is_not_unreachable(monkeypatch):
    config = config_sources.SSHConfig("target")
    child = SimpleNamespace(wait=AsyncMock(return_value=0))
    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "ssh")
    monkeypatch.setattr(
        config_sources, "SSHProfileSource",
        lambda *args, **kwargs: SimpleNamespace(get_ssh_config=lambda: config),
    )
    monkeypatch.setattr(proxy, "create_ssh_subprocess", AsyncMock(return_value=child))
    monkeypatch.setattr(
        process, "terminate_ssh_process_tree",
        AsyncMock(side_effect=RuntimeError("native cleanup unconfirmed")),
    )
    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        probe.probe_alias("target", 8)


def test_windows_probe_configuration_error_does_not_launch(monkeypatch):
    spawn = AsyncMock()

    def invalid():
        raise ValueError("invalid profile")

    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "ssh")
    monkeypatch.setattr(
        config_sources, "SSHProfileSource",
        lambda *args, **kwargs: SimpleNamespace(get_ssh_config=invalid),
    )
    monkeypatch.setattr(proxy, "create_ssh_subprocess", spawn)
    with pytest.raises(ValueError, match="invalid profile"):
        probe.probe_alias("target", 8)
    spawn.assert_not_awaited()


def test_windows_probe_refuses_an_unpaired_old_library_before_launch(monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.delattr(proxy, "WINDOWS_OWNED_PROBE_VERSION")
    monkeypatch.setattr(proxy, "create_ssh_subprocess", spawn)
    with pytest.raises(RuntimeError, match="probe support is unavailable"):
        probe.probe_alias("target", 8)
    spawn.assert_not_awaited()


@pytest.mark.parametrize("support", [None, True, 1.0, 0, 2, "1"])
def test_windows_probe_refuses_invalid_contract_declarations(monkeypatch, support):
    spawn = AsyncMock()
    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.setattr(proxy, "WINDOWS_OWNED_PROBE_VERSION", support)
    monkeypatch.setattr(proxy, "create_ssh_subprocess", spawn)
    with pytest.raises(RuntimeError, match="probe support is unavailable"):
        probe.probe_alias("target", 8)
    spawn.assert_not_awaited()


@pytest.mark.parametrize("timeout", [0, -1])
def test_windows_probe_refuses_an_unbounded_deadline_before_launch(monkeypatch, timeout):
    spawn = AsyncMock()
    monkeypatch.setattr(probe, "IS_WINDOWS", True)
    monkeypatch.setattr(proxy, "create_ssh_subprocess", spawn)
    with pytest.raises(ValueError, match="positive integer"):
        probe.probe_alias("target", timeout)
    spawn.assert_not_awaited()
