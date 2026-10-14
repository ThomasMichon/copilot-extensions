"""The vault service starts through the shared resident-daemon launch."""

from __future__ import annotations

import agent_procutil
import pytest

from agent_vault import cli, config, launch, service


@pytest.fixture
def recorded_spawns(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "spawn_service", lambda args: calls.append(list(args)))
    monkeypatch.setattr(cli, "send_command", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    return calls


@pytest.mark.parametrize("windows", [True, False])
def test_start_service_launches_through_spawn_service(monkeypatch, recorded_spawns, windows):
    monkeypatch.setattr(cli, "IS_WINDOWS", windows)
    assert cli.start_service(4242) is True
    # Always in the foreground: spawn_daemon already detached it, and POSIX's
    # self-daemonize would move it to HOME with its output on /dev/null.
    assert recorded_spawns == [["--tcp-port", "4242", "--foreground"]]


def test_windows_self_daemonize_launches_through_spawn_service(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "spawn_service", lambda args: calls.append(list(args)))
    service.daemonize_windows(["-m", "agent_vault.service", "--tcp-port", "4242", "-v"])
    assert calls == [["--tcp-port", "4242", "-v", "--foreground"]]


def test_spawn_service_runs_from_the_runtime_root_with_a_log(monkeypatch, tmp_path):
    home = tmp_path / "vault-home"
    monkeypatch.setenv(config.HOME_ENV, str(home))
    seen: dict = {}

    def _fake_spawn_daemon(argv, **kwargs):
        seen.update(argv=list(argv), **kwargs)
        return "proc"

    monkeypatch.setattr(launch, "spawn_daemon", _fake_spawn_daemon)
    assert launch.spawn_service(["--foreground"]) == "proc"
    root = home.resolve()
    assert root.is_dir()
    assert seen["argv"][1:] == ["-m", "agent_vault.service", "--foreground"]
    assert seen["cwd"] == root
    assert seen["log_path"] == root / launch.SERVICE_STDIO_LOG
    # The service always ran inside its caller's Job before: keep it running
    # there (logged) when that Job forbids breakaway.
    assert seen["contained_fallback"] is True
    assert seen.get("breakaway", True) is True


def test_spawn_service_is_the_shared_primitive():
    assert launch.spawn_daemon is agent_procutil.spawn_daemon
