"""``agent-bridge service start --direct``: the installer's direct start.

An installer that already tried the platform service manager (or has none)
starts the daemon through the bridge's own job-surviving ``spawn_daemon``
launch instead of a PowerShell ``Start-Process``. ``--direct`` never touches
systemd or the scheduled task, records the started daemon's pid, and exits
non-zero when the daemon does not become healthy.
"""

from __future__ import annotations

import argparse
import subprocess
from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as m


def _fast_time(monkeypatch):
    import time as _time

    now = {"value": 0.0}
    monkeypatch.setattr(_time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        _time, "sleep", lambda seconds: now.__setitem__("value", now["value"] + seconds)
    )


@pytest.fixture()
def direct_env(monkeypatch, tmp_path):
    _fast_time(monkeypatch)
    pid_file = tmp_path / "agent-bridge.pid"
    monkeypatch.setattr(m, "_PID_FILE", str(pid_file))
    monkeypatch.setattr(m, "_service_port", lambda: 12345)
    monkeypatch.setattr(m, "_active_endpoint", lambda: None)
    monkeypatch.setattr(m, "_active_endpoint_is_forward", lambda: False)
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: False)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid, _timeout=15: False)
    monkeypatch.setattr(m, "_pid_from_lock", lambda _port: None)
    monkeypatch.setattr(
        m, "_systemd_available",
        lambda: pytest.fail("--direct must not consult systemd"),
    )
    monkeypatch.setattr(
        m, "_win_task_exists",
        lambda: pytest.fail("--direct must not consult the scheduled task"),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: pytest.fail("--direct must not run a service manager"),
    )
    return pid_file


def test_direct_start_spawns_once_and_records_the_daemon_pid(monkeypatch, direct_env):
    state = {"spawned": 0}

    def _launch(argv, *, display, contained_last_resort):
        state["spawned"] += 1
        assert contained_last_resort is False, "--direct must never start inside the Job"
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(m, "_launch_bridge_daemon", _launch)
    monkeypatch.setattr(m, "_service_is_running", lambda: state["spawned"] > 0)

    m._service_start(direct=True)

    assert state["spawned"] == 1
    assert direct_env.read_text(encoding="utf-8") == "4321"


def test_direct_start_records_the_brokered_actual_daemon_pid(monkeypatch, direct_env):
    from agent_procutil.daemon_broker import WindowsDaemonProcess

    state = {"spawned": 0}

    def _launch(argv, **_kwargs):
        state["spawned"] += 1
        process = WindowsDaemonProcess.__new__(WindowsDaemonProcess)
        process.pid = 999
        return process

    monkeypatch.setattr(m, "_launch_bridge_daemon", _launch)
    monkeypatch.setattr(m, "_service_is_running", lambda: state["spawned"] > 0)

    m._service_start(direct=True)

    assert direct_env.read_text(encoding="utf-8") == "999"


def test_direct_start_fails_when_the_daemon_never_becomes_healthy(monkeypatch, direct_env):
    spawned = {"n": 0}
    monkeypatch.setattr(
        m, "_launch_bridge_daemon",
        lambda *_a, **_k: spawned.__setitem__("n", spawned["n"] + 1) or SimpleNamespace(pid=7),
    )
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_service_process_is_live", lambda **_k: False)

    with pytest.raises(SystemExit) as info:
        m._service_start(direct=True)

    assert info.value.code == 1
    assert spawned["n"] == 1, "no second spawn: the installer owns retries"


def test_direct_start_leaves_a_healthy_daemon_alone(monkeypatch, direct_env):
    monkeypatch.setattr(m, "_service_is_running", lambda: True)
    monkeypatch.setattr(
        m, "_launch_bridge_daemon", lambda *_a, **_k: pytest.fail("already healthy")
    )
    m._service_start(direct=True)
    assert not direct_env.exists()


def test_direct_start_fails_over_an_unanswering_forward_without_spawning(
    monkeypatch, direct_env
):
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_active_endpoint_is_forward", lambda: True)
    monkeypatch.setattr(
        m, "_launch_bridge_daemon", lambda *_a, **_k: pytest.fail("never spawn over a forward")
    )

    with pytest.raises(SystemExit) as info:
        m._service_start(direct=True)

    assert info.value.code == 1


def test_plain_start_over_an_unanswering_forward_still_returns(monkeypatch, direct_env):
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_active_endpoint_is_forward", lambda: True)
    m._service_start()


def test_cli_routes_direct_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(m, "_service_start", lambda direct=False: seen.append(direct))
    parser = argparse.ArgumentParser()
    m.register_service_control_commands(parser.add_subparsers())

    m._cmd_service(parser.parse_args(["service", "start", "--direct"]))
    m._cmd_service(parser.parse_args(["service", "start"]))

    assert seen == [True, False]


def test_direct_start_fails_instead_of_starting_inside_the_callers_job(
    monkeypatch, direct_env, tmp_path
):
    """Breakaway denied and the WMI broker failing must fail a direct start:
    a daemon started inside the installer's Job would die when it closes."""
    import agent_procutil

    popens = []

    def _popen(argv, **kwargs):
        popens.append(kwargs.get("creationflags", 0))
        exc = OSError(13, "Access is denied")
        exc.winerror = 5
        raise exc

    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", _popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    from agent_procutil import daemon

    def broker_failed(*_args, **_kwargs):
        raise agent_procutil.DaemonSpawnError("broker failed")

    monkeypatch.setattr(daemon, "spawn_brokered_daemon", broker_failed)
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])
    monkeypatch.setattr(m, "_service_is_running", lambda: False)

    with pytest.raises(SystemExit) as info:
        m._service_start(direct=True)

    assert info.value.code == 1
    assert len(popens) == 1, "no contained retry after breakaway and the broker failed"
    assert not direct_env.exists()