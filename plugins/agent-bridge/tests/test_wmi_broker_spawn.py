"""Bridge service and cutover share the canonical daemon lifetime broker."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent_procutil
from agent_procutil import daemon
from agent_bridge import __main__ as m


@pytest.fixture()
def breakaway_refused(monkeypatch, tmp_path):
    calls = []

    def popen(argv, **kwargs):
        calls.append(kwargs.get("creationflags", 0))
        if kwargs.get("creationflags", 0) & 0x01000000:
            error = OSError("Access is denied")
            error.winerror = 5
            raise error
        return Mock(pid=77)

    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])
    return calls


def test_spawn_detached_uses_shared_broker_when_breakaway_refused(
    monkeypatch, breakaway_refused, tmp_path,
):
    process = Mock(pid=1234)
    launch = Mock(return_value=process)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)

    assert m._spawn_detached_daemon() is process

    assert len(breakaway_refused) == 1
    launch.assert_called_once()
    kwargs = launch.call_args.kwargs
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["log_path"] == str(tmp_path / "agent-bridge.log")
    assert kwargs["stderr_log_path"] == str(tmp_path / "agent-bridge-err.log")
    assert not kwargs["creationflags"] & 0x01000000


def test_spawn_detached_falls_back_to_contained_only_after_broker_failure(
    monkeypatch, breakaway_refused, tmp_path,
):
    launch = Mock(side_effect=agent_procutil.DaemonSpawnError("fixture broker failure"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)

    m._spawn_detached_daemon()

    first, second = breakaway_refused
    assert first & 0x01000000
    assert not second & 0x01000000
    launch.assert_called_once()
    err = (tmp_path / "agent-bridge-err.log").read_text(encoding="utf-8")
    assert "broker failed" in err and "contained_fallback" in err


def test_passive_generation_never_falls_back_to_contained(monkeypatch, breakaway_refused):
    launch = Mock(side_effect=agent_procutil.DaemonSpawnError("fixture broker failure"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)
    with pytest.raises(agent_procutil.DaemonSpawnError):
        m._launch_bridge_daemon(
            ["py", "-m", "agent_bridge", "start", "--passive"],
            display="agent-bridge passive generation", contained_last_resort=False,
        )
    assert len(breakaway_refused) == 1


def test_passive_generation_preserves_actual_brokered_handle(monkeypatch, breakaway_refused):
    process = Mock(pid=4321)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", Mock(return_value=process))
    result = m._launch_bridge_daemon(
        ["py"], display="agent-bridge passive generation", contained_last_resort=False,
    )
    assert result is process


def test_other_spawn_errors_are_not_masked_by_the_broker(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(
        agent_procutil.subprocess, "Popen",
        Mock(side_effect=FileNotFoundError("missing executable")),
    )
    launch = Mock()
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)
    with pytest.raises(FileNotFoundError):
        m._spawn_detached_argv(["missing.exe"])
    launch.assert_not_called()


def test_shared_broker_receives_anchored_roots_and_unmodified_argv(
    monkeypatch, breakaway_refused, tmp_path,
):
    from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "_INSTALL_DIR", "root")
    monkeypatch.setenv(INSTALL_DIR_ENV, "root")
    monkeypatch.setenv(CONFIG_DIR_ENV, "configuration")
    argv = ["py", "spaces & 'quotes'", "unicode-\u65e5", ""]
    launch = Mock(return_value=Mock(pid=123))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)

    m._spawn_detached_argv(argv)

    assert launch.call_args.args[0] == argv
    kwargs = launch.call_args.kwargs
    assert kwargs["env"][INSTALL_DIR_ENV] == str(tmp_path / "root")
    assert kwargs["env"][CONFIG_DIR_ENV] == str(tmp_path / "configuration")
    assert kwargs["cwd"] == str(tmp_path / "root")


def test_spawn_detached_happy_path_never_calls_broker(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    popen, launch = Mock(), Mock()
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", popen)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)

    m._spawn_detached_argv(["py"])

    launch.assert_not_called()
    assert popen.call_args.kwargs["creationflags"] == 0x08000000 | 0x01000000
    assert popen.call_args.kwargs["cwd"] == str(tmp_path)


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
