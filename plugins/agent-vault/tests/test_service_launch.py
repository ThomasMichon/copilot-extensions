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


def test_relative_home_override_is_passed_resolved(monkeypatch, tmp_path):
    # The service runs from its root: a relative override inherited as-is
    # would resolve to <home>/<home> there, apart from the clients' copy.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.HOME_ENV, "state")
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    root = (tmp_path / "state").resolve()
    assert seen["cwd"] == root
    assert seen["env_overrides"][config.HOME_ENV] == str(root)


def test_unset_home_override_stays_unset(monkeypatch, tmp_path):
    # Its presence selects the cache location, so legacy mode must not gain it.
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path)
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    assert config.HOME_ENV not in seen["env_overrides"]


def test_relative_path_overrides_are_passed_resolved(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path / "root")
    relative = {
        config.CONFIG_ENV: "settings.json", config.RUN_DIR_ENV: "run",
        config.PID_ENV: "vault.pid", config.LOG_ENV: "vault.log", config.SOCKET_ENV: "vault.sock",
        launch.CACHE_DIR_ENV: "cache", "KPDB": "vault.kdbx",
    }
    for name, value in relative.items():
        monkeypatch.setenv(name, value)
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    for name, value in relative.items():
        assert seen["env_overrides"][name] == str(tmp_path / value)


def test_unset_path_overrides_stay_unset(monkeypatch, tmp_path):
    for name in launch._PATH_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path)
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    assert not set(launch._PATH_OVERRIDES) & set(seen["env_overrides"])


def test_relative_database_path_is_absolute_before_any_request(monkeypatch, tmp_path):
    # Requests carry the client's kpdb; the service resolves it from its own
    # runtime root, so a relative one must already be absolute.
    monkeypatch.chdir(tmp_path)
    assert config._expand_kpdb("vault.kdbx") == str(tmp_path / "vault.kdbx")
    assert config._expand_kpdb("db/vault.kdbx", base_dir=tmp_path / "repo") == str(
        tmp_path / "repo" / "db" / "vault.kdbx"
    )
