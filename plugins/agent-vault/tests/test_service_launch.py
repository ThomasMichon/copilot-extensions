"""The vault service starts through the shared resident-daemon launch."""

from __future__ import annotations

import json
import os
from pathlib import Path

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
        launch.CACHE_DIR_ENV: "cache", "KPDB": "vault.kdbx", "AGENT_VAULT_KEK_DIR": "keys",
        "AGENT_VAULT_WINDOWS_RUN_DIR": "winrun", "AGENT_VAULT_CORE_RUN_DIR": "corerun",
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


@pytest.mark.parametrize("value", ["~/vault.kdbx", "$VAULT_TEST_ROOT/vault.kdbx"])
def test_path_overrides_are_expanded_before_they_are_resolved(monkeypatch, tmp_path, value):
    # `abspath("~/x")` would name `<caller>/~/x`: expand as the readers do first.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_TEST_ROOT", str(tmp_path / "home"))
    monkeypatch.setenv("KPDB", value)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path / "root")
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    assert seen["env_overrides"]["KPDB"] == str(tmp_path / "home" / "vault.kdbx")


@pytest.mark.parametrize("value", ["~/cache", "$VAULT_TEST_ROOT/cache"])
def test_non_database_overrides_are_resolved_raw_like_their_readers(monkeypatch, tmp_path, value):
    # The cache reader takes the raw path (`<caller>/~/cache`); expanding it for
    # the service alone would split the client and service credential caches.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_TEST_ROOT", str(tmp_path / "home"))
    monkeypatch.setenv(launch.CACHE_DIR_ENV, value)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path / "root")
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    client_view = str(Path(os.environ[launch.CACHE_DIR_ENV]).absolute())
    assert seen["env_overrides"][launch.CACHE_DIR_ENV] == client_view
    assert client_view == str(tmp_path / value)


@pytest.mark.parametrize("layout", ["flat", "registry"])
def test_global_config_relative_database_survives_the_service_cwd(monkeypatch, tmp_path, layout):
    # A request that omits kpdb makes the service resolve the configured
    # default from its own runtime root; it must name the client's database.
    settings = tmp_path / "settings" / "config.json"
    settings.parent.mkdir()
    data = (
        {"kpdb": "vault.kdbx"} if layout == "flat"
        else {"vaults": {"main": {"kpdb": "vault.kdbx"}}, "default_vault": "main"}
    )
    settings.write_text(json.dumps(data), encoding="utf-8")
    for name in ("KPDB", "AGENT_VAULT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.CONFIG_ENV, str(settings))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    expected = str(tmp_path / "settings" / "vault.kdbx")
    for cwd in ("client", "service-root"):
        (tmp_path / cwd).mkdir()
        monkeypatch.chdir(tmp_path / cwd)
        assert config.resolve_kpdb(required=False) == expected


@pytest.mark.parametrize("given", ["vault.kdbx", "~/vault.kdbx"])
def test_vault_add_keeps_a_relative_database_at_the_callers_directory(
    monkeypatch, tmp_path, given
):
    # Settings resolve relative paths against their own directory, so `vault
    # add` anchors a relative path at the caller's cwd before saving it; a
    # home-relative path is stored as given and expanded on read.
    settings = tmp_path / "settings" / "config.json"
    settings.parent.mkdir()
    (tmp_path / "caller").mkdir()
    (tmp_path / "elsewhere").mkdir()
    for name in ("KPDB", "AGENT_VAULT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.CONFIG_ENV, str(settings))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    monkeypatch.chdir(tmp_path / "caller")
    args = type("Args", (), {"name": "example", "kpdb": given, "group": None, "port": None})()
    assert cli.cmd_vault_add(args) == 0
    monkeypatch.chdir(tmp_path / "elsewhere")
    expected = tmp_path / ("caller" if given == "vault.kdbx" else "home") / "vault.kdbx"
    assert config.resolve_kpdb(required=False) == str(expected)
