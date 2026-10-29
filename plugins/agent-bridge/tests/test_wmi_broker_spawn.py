"""Bridge service and cutover delegate the canonical native lifetime policy."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent_procutil
from agent_procutil import daemon
from agent_bridge import __main__ as m


@pytest.fixture()
def native_launch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])
    launch = Mock(return_value=Mock(pid=1234))
    monkeypatch.setattr(m, "spawn_daemon", launch)
    return launch


def test_spawn_detached_uses_shared_native_policy(native_launch, tmp_path):
    assert m._spawn_detached_daemon() is native_launch.return_value

    native_launch.assert_called_once()
    kwargs = native_launch.call_args.kwargs
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["log_path"] == str(tmp_path / "agent-bridge.log")
    assert kwargs["stderr_log_path"] == str(tmp_path / "agent-bridge-err.log")
    assert kwargs["console_children"] is True
    assert kwargs["contained_fallback"] is True


def test_shared_native_failure_is_not_replayed_by_service_adapter(native_launch):
    native_launch.side_effect = agent_procutil.DaemonSpawnError("unconfirmed native cleanup")
    with pytest.raises(agent_procutil.DaemonSpawnError):
        m._spawn_detached_daemon()

    native_launch.assert_called_once()


def test_passive_generation_never_falls_back_to_contained(native_launch):
    native_launch.side_effect = agent_procutil.DaemonSpawnError("native admission refused")
    with pytest.raises(agent_procutil.DaemonSpawnError):
        m._launch_bridge_daemon(
            ["py", "-m", "agent_bridge", "start", "--passive"],
            display="agent-bridge passive generation", contained_last_resort=False,
        )
    native_launch.assert_called_once()
    assert native_launch.call_args.kwargs["contained_fallback"] is False


def test_passive_generation_preserves_actual_native_handle(native_launch):
    result = m._launch_bridge_daemon(
        ["py"], display="agent-bridge passive generation", contained_last_resort=False,
    )
    assert result is native_launch.return_value
    assert native_launch.call_args.kwargs["contained_fallback"] is False


def test_other_spawn_errors_are_not_masked_by_service_adapter(native_launch):
    native_launch.side_effect = FileNotFoundError("missing executable")
    with pytest.raises(FileNotFoundError):
        m._spawn_detached_argv(["missing.exe"])
    native_launch.assert_called_once()


def test_shared_native_policy_receives_anchored_roots_and_unmodified_argv(
    monkeypatch, native_launch, tmp_path,
):
    from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "_INSTALL_DIR", "root")
    monkeypatch.setenv(INSTALL_DIR_ENV, "root")
    monkeypatch.setenv(CONFIG_DIR_ENV, "configuration")
    argv = ["py", "spaces & 'quotes'", "unicode-\u65e5", ""]
    m._spawn_detached_argv(argv)

    assert native_launch.call_args.args[0] == argv
    kwargs = native_launch.call_args.kwargs
    assert kwargs["env_overrides"][INSTALL_DIR_ENV] == str(tmp_path / "root")
    assert kwargs["env_overrides"][CONFIG_DIR_ENV] == str(tmp_path / "configuration")
    assert kwargs["cwd"] == str(tmp_path / "root")


def test_spawn_detached_happy_path_never_calls_broker(monkeypatch, native_launch):
    legacy = Mock(side_effect=AssertionError("automatic legacy activation"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", legacy)

    m._spawn_detached_argv(["py"])

    legacy.assert_not_called()
    native_launch.assert_called_once()


@pytest.mark.parametrize("passive", [False, True])
def test_python_daemon_bypasses_redirector_and_preserves_venv(monkeypatch, tmp_path, passive):
    from agent_bridge import service_process_cli

    monkeypatch.setattr(
        service_process_cli, "os",
        SimpleNamespace(name="nt", path=os.path, makedirs=os.makedirs, environ=os.environ),
    )
    executable = str(tmp_path / "venv" / "Scripts" / "python.exe")
    base = str(tmp_path / "base" / "python.exe")
    select = Mock(return_value=(base, {"__PYVENV_LAUNCHER__": executable}))
    monkeypatch.setattr(agent_procutil, "direct_python", select, raising=False)
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    launch = Mock()
    monkeypatch.setattr(m, "spawn_daemon", launch)
    argv = [executable, "-m", "agent_bridge", "start"]
    if passive:
        argv += ["--passive"]
        m._launch_bridge_daemon(argv, display="passive", contained_last_resort=False)
    else:
        m._spawn_detached_argv(argv)
    select.assert_called_once_with(executable)
    assert launch.call_args.args[0] == [base, *argv[1:]]
    assert launch.call_args.kwargs["env_overrides"]["__PYVENV_LAUNCHER__"] == executable
    assert argv[0] == executable, "normalization must not mutate the caller's argv"


def test_watchdog_replacement_uses_delayed_versioned_start(monkeypatch):
    captured = {}
    monkeypatch.setattr(m.sys, "executable", r"C:\venv\python.exe")
    monkeypatch.setattr(
        m, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv),
    )
    m._spawn_watchdog_replacement(
        delay=1.5, start_args=["start", "--passive", "--idle-shutdown", "30"],
    )
    argv = captured["argv"]
    assert argv[0] == r"C:\venv\python.exe"
    assert argv[1] == "-c"
    assert "agent_bridge" in argv[2]
    assert argv[3] == "1.5"
    assert argv[4:] == ["start", "--passive", "--idle-shutdown", "30"]


def test_watchdog_replacement_promotes_active_passive_generation(monkeypatch):
    captured = {}
    monkeypatch.setattr(m.sys, "executable", r"C:\venv\python.exe")
    monkeypatch.setattr(
        m, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv),
    )
    m._spawn_watchdog_replacement(
        start_args=["start", "--port", "51103", "--passive"], active_port=51103,
    )
    assert captured["argv"][4:] == ["start", "--port", "51103"]


@pytest.mark.parametrize("active_port", [51103, None])
@pytest.mark.parametrize("launch_args", [
    ["--serving-launch-id", "ab" * 16],
    ["--serving-launch-id=" + "ab" * 16],
])
def test_watchdog_replacement_discards_one_shot_worker_receipt(
    monkeypatch, active_port, launch_args,
):
    captured = {}
    monkeypatch.setattr(
        m, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv),
    )
    m._spawn_watchdog_replacement(
        start_args=["start", "--port", "51103", "--passive", *launch_args],
        active_port=active_port,
    )
    assert captured["argv"][4:] == [
        "start", "--port", "51103", *(["--passive"] if active_port is None else []),
    ]
    import argparse
    from agent_bridge.service_start_cli import register_service_start_commands

    parser = argparse.ArgumentParser()
    register_service_start_commands(parser.add_subparsers())
    parsed = parser.parse_args(captured["argv"][4:])
    assert parsed.serving_launch_id is None
    assert parsed.passive is (active_port is None)


def test_watchdog_dead_schedules_replacement_before_exit(monkeypatch):
    from agent_bridge import watchdog

    calls = []
    monkeypatch.setattr(m, "_spawn_watchdog_replacement", lambda **_k: calls.append("replacement"))
    monkeypatch.setattr(watchdog, "_force_exit", lambda reason: calls.append(("exit", reason)))
    m._watchdog_dead("listener stopped")
    assert calls == ["replacement", ("exit", "listener stopped")]


def test_watchdog_dead_still_exits_when_replacement_scheduling_crashes(monkeypatch):
    from agent_bridge import watchdog

    calls = []

    def crash(**_kwargs):
        raise RuntimeError("unexpected helper failure")

    monkeypatch.setattr(m, "_spawn_watchdog_replacement", crash)
    monkeypatch.setattr(watchdog, "_force_exit", lambda reason: calls.append(("exit", reason)))
    m._watchdog_dead("listener stopped")
    assert calls == [("exit", "listener stopped")]
