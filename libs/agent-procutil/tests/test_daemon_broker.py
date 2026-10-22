"""Lifetime brokerage preserves the launch contract and owns partial starts."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from unittest.mock import MagicMock, Mock

import pytest

import agent_procutil as pu
from agent_procutil import daemon, daemon_broker as broker


def _denied():
    error = OSError("breakaway denied")
    error.winerror = 5
    return error


@pytest.fixture(autouse=True)
def no_native_caller_job(monkeypatch):
    monkeypatch.setattr(broker, "caller_in_job", lambda: False)


def test_nested_caller_job_uses_verified_broker_without_starting_a_direct_child(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(broker, "caller_in_job", lambda: True)
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    process = Mock(pid=1234)
    launch = Mock(return_value=process)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)
    assert pu.spawn_daemon(
        ["py"], cwd=tmp_path, log_path=tmp_path / "out.log", console_children=True,
    ) is process
    popen.assert_not_called()
    assert launch.call_args.kwargs["creationflags"] == pu._CREATE_NO_WINDOW
    assert "requires verified outside-Job" in (tmp_path / "out.log").read_text()


def test_caller_job_query_failure_never_launches(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(broker, "caller_in_job", Mock(side_effect=OSError("query refused")))
    popen, launch = Mock(), Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)
    with pytest.raises(OSError, match="query refused"):
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "out.log")
    popen.assert_not_called()
    launch.assert_not_called()


def test_denied_breakaway_uses_shared_broker_with_actual_child_contract(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    popen = Mock(side_effect=_denied())
    monkeypatch.setattr(subprocess, "Popen", popen)
    process = Mock(pid=1234)
    launch = Mock(return_value=process)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)

    result = pu.spawn_daemon(
        ["py", "arg with spaces"], cwd=tmp_path, log_path=tmp_path / "out.log",
        stderr_log_path=tmp_path / "err.log", env={"KEY": "old"},
        env_overrides={"KEY": "new", "OTHER": "value"}, console_children=True,
        extra_creationflags=0x4000, contained_fallback=True,
    )

    assert result is process
    assert popen.call_count == 1, "successful brokerage must not trigger a contained retry"
    launch.assert_called_once_with(
        ["py", "arg with spaces"], cwd=str(tmp_path), env={"KEY": "new", "OTHER": "value"},
        creationflags=pu._CREATE_NO_WINDOW | 0x4000,
        log_path=str(tmp_path / "out.log"), stderr_log_path=str(tmp_path / "err.log"),
    )
    for name in ("out.log", "err.log"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "trying Windows lifetime broker" in text
        assert "arg with spaces" not in text and "value" not in text


@pytest.mark.parametrize("contained", [False, True])
def test_containment_never_uses_the_broker(monkeypatch, tmp_path, contained):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1" if contained else "0")
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=_denied()))
    launch = Mock()
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)
    with pytest.raises(OSError):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "out.log",
            breakaway=contained,
        )
    launch.assert_not_called()


def test_broker_errors_are_sanitized_in_both_logs(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=_denied()))
    monkeypatch.setattr(
        daemon, "spawn_brokered_daemon",
        Mock(side_effect=pu.DaemonSpawnError("sensitive-fixture-detail")),
    )
    with pytest.raises(pu.DaemonSpawnError) as caught:
        pu.spawn_daemon(
            ["py", "sensitive-fixture-detail"], cwd=tmp_path,
            log_path=tmp_path / "out.log", stderr_log_path=tmp_path / "err.log",
        )
    assert "sensitive-fixture-detail" not in str(caught.value)
    for name in ("out.log", "err.log"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "daemon not started" in text and "sensitive-fixture-detail" not in text


def test_uncertain_cleanup_prohibits_a_contained_retry(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    popen = Mock(side_effect=[_denied(), Mock(pid=123)])
    monkeypatch.setattr(subprocess, "Popen", popen)
    error = pu.DaemonSpawnError("cleanup uncertain")
    error.cleanup_confirmed = False
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", Mock(side_effect=error))
    with pytest.raises(pu.DaemonSpawnError):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "out.log", contained_fallback=True,
        )
    assert popen.call_count == 1, "uncertainty must not create a second daemon"


def test_frames_preserve_unicode_environment_and_embedded_newlines():
    deadline = time.monotonic() + 5
    spec = {"env": {"VALUE": "quotes ' \" ; & \n \u65e5"}, "argv": ["spaced argument", ""]}
    left, right = socket.socketpair()
    with left, right:
        broker._send(left, spec, deadline)
        assert broker._receive(right, deadline) == spec


@pytest.mark.parametrize("wire", [b"\0\0\0\0", b"\xff\xff\xff\xff", b"\0\0\0\2[]"])
def test_invalid_frames_fail_without_unbounded_reads(wire):
    left, right = socket.socketpair()
    with left, right:
        left.sendall(wire)
        with pytest.raises(ValueError):
            broker._receive(right, time.monotonic() + 1)


def test_closed_channel_is_an_explicit_error():
    left, right = socket.socketpair()
    with left, right:
        left.close()
        with pytest.raises(OSError, match="channel closed"):
            broker._receive(right, time.monotonic() + 1)


def test_expired_deadline_does_not_start_another_wait():
    left, right = socket.socketpair()
    with left, right:
        with pytest.raises(TimeoutError, match="deadline"):
            broker._receive(right, time.monotonic() - 1)


def test_wmi_activation_uses_json_stdin_not_shell_interpolation(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    run = Mock(return_value=Mock(returncode=0, stdout="123\n"))
    monkeypatch.setattr(subprocess, "run", run)
    deadline = time.monotonic() + 20
    environment = {"COOKIE": "not-on-commandline", "VALUE": "' & \n \u65e5"}
    argv = ["pythonw", "-I", str(tmp_path / "bootstrap with spaces.py")]

    assert broker._activate(argv, environment, str(tmp_path), deadline) == 123

    args, kwargs = run.call_args
    command = args[0]
    assert command[0].startswith(str(tmp_path))
    assert "not-on-commandline" not in " ".join(command)
    assert "bootstrap with spaces" not in " ".join(command)
    payload = json.loads(kwargs["input"])
    assert payload["command"] == subprocess.list2cmdline(argv)
    assert payload["environment"] == [f"{key}={value}" for key, value in environment.items()]
    assert payload["cwd"] == str(tmp_path)
    assert 0 < kwargs["timeout"] <= 20
    assert "ProcessStartupInformation" in command[-1]
    assert "EnvironmentVariables" in command[-1]
    assert "CreateFlags" in command[-1]
    assert payload["creationflags"] == pu.detached_kwargs(breakaway=False).get("creationflags", 0)


@pytest.mark.parametrize(
    ("status", "output"), [(1, "123"), (0, "not a pid"), (0, "-1"), (0, "0")],
)
def test_wmi_failure_does_not_claim_a_process(monkeypatch, tmp_path, status, output):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(
        subprocess, "run", Mock(return_value=Mock(returncode=status, stdout=output)),
    )
    with pytest.raises(OSError, match="activation failed|invalid process identity"):
        broker._activate(["py"], {}, str(tmp_path), time.monotonic() + 5)


@pytest.fixture()
def broker_channel(monkeypatch):
    listener = MagicMock()
    listener.__enter__.return_value = listener
    connection = MagicMock()
    connection.__enter__.return_value = connection
    listener.accept.return_value = (connection, ("127.0.0.1", 123))
    listener.getsockname.return_value = ("127.0.0.1", 456)
    monkeypatch.setattr(socket, "socket", Mock(return_value=listener))
    monkeypatch.setattr(broker.secrets, "token_hex", lambda _: "fixture-cookie")
    monkeypatch.setattr(broker, "_activate", Mock(return_value=100))
    monkeypatch.setattr(broker, "_bootstrap_python", lambda: "base-python-fixture")
    processes = {}

    def process(pid, args):
        result = Mock(pid=pid, args=args)
        result.wait.return_value = 0
        processes[pid] = result
        return result

    monkeypatch.setattr(broker, "WindowsDaemonProcess", process)
    monkeypatch.setattr(broker, "_outside_job", Mock(return_value=True))
    monkeypatch.setattr(broker, "_send", Mock())
    responses = [
        {"pid": 100, "cookie": "fixture-cookie"}, {"pid": 200}, {"started": 200},
    ]
    monkeypatch.setattr(broker, "_receive", Mock(side_effect=responses))
    return processes, responses


def _launch(tmp_path):
    return broker.spawn_brokered_daemon(
        ["actual-daemon", "private-arg"], cwd=str(tmp_path), env={"A": "B"},
        creationflags=pu._CREATE_NO_WINDOW, log_path=str(tmp_path / "out.log"),
        stderr_log_path=str(tmp_path / "err.log"),
    )


def test_adopts_actual_child_and_releases_only_bootstrap(monkeypatch, broker_channel, tmp_path):
    processes, _ = broker_channel
    result = _launch(tmp_path)
    assert result is processes[200]
    result.terminate.assert_not_called()
    result.close.assert_not_called()
    processes[100].close.assert_called_once()
    processes[100].terminate.assert_not_called()
    sent = broker._send.call_args_list
    assert sent[-1].args[1] == {"start": 200}
    assert sent[0].args[1]["env"] == {"A": "B"}


@pytest.mark.parametrize("hello", [
    {"pid": 100, "cookie": "wrong"}, {"pid": 101, "cookie": "fixture-cookie"},
    {"pid": 100, "cookie": "\u65e5"}, {"pid": 100, "cookie": ["fixture-cookie"]},
])
def test_identity_mismatch_never_sends_environment(
    monkeypatch, broker_channel, tmp_path, hello,
):
    processes, _ = broker_channel
    monkeypatch.setattr(broker, "_receive", Mock(return_value=hello))
    with pytest.raises(pu.DaemonSpawnError, match="lifetime broker failed"):
        _launch(tmp_path)
    broker._send.assert_not_called()
    assert set(processes) == {100}
    processes[100].close.assert_called_once()


def test_bootstrap_uses_base_python_even_without_a_gui_sibling(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    python = base / "python.exe"
    python.write_bytes(b"base-interpreter-fixture")
    monkeypatch.setattr(broker.sys, "base_prefix", str(base))
    monkeypatch.setattr(pu, "windowless_python", lambda _: "venv-redirector/pythonw.exe")
    assert broker._bootstrap_python() == str(python)
    assert not (base / "pythonw.exe").exists()


@pytest.mark.parametrize("failure", ["contained", "confirmation", "bootstrap-exit"])
def test_partial_start_reaps_pinned_child_before_releasing_handles(
    monkeypatch, broker_channel, tmp_path, failure,
):
    processes, _ = broker_channel
    if failure == "contained":
        monkeypatch.setattr(broker, "_outside_job", lambda process: process.pid != 200)
    elif failure == "confirmation":
        monkeypatch.setattr(broker, "_receive", Mock(side_effect=[
            {"pid": 100, "cookie": "fixture-cookie"}, {"pid": 200}, {"started": 999},
        ]))
    else:
        original = broker.WindowsDaemonProcess

        def process(pid, args):
            result = original(pid, args)
            result.wait.return_value = 1
            return result

        monkeypatch.setattr(broker, "WindowsDaemonProcess", process)
    with pytest.raises(pu.DaemonSpawnError):
        _launch(tmp_path)
    processes[200].terminate.assert_called_once()
    processes[200].close.assert_called_once()
    processes[100].close.assert_called_once()
    processes[100].terminate.assert_not_called()


@pytest.mark.parametrize(("pid", "method"), [
    (100, "wait"), (200, "wait"), (100, "close"), (200, "close"),
])
def test_cleanup_failures_are_normalized_and_do_not_skip_other_cleanup(
    monkeypatch, broker_channel, tmp_path, pid, method,
):
    processes, _ = broker_channel
    original = broker.WindowsDaemonProcess

    def process(number, args):
        result = original(number, args)
        if number == pid:
            error = subprocess.TimeoutExpired(args, 5) if method == "wait" else OSError("close")
            getattr(result, method).side_effect = error
        return result

    monkeypatch.setattr(broker, "WindowsDaemonProcess", process)
    monkeypatch.setattr(broker, "_outside_job", lambda process: process.pid != 200)
    with pytest.raises(pu.DaemonSpawnError) as caught:
        _launch(tmp_path)
    assert caught.value.cleanup_confirmed is (method == "close")
    processes[100].close.assert_called_once()
    processes[200].close.assert_called_once()
    processes[200].terminate.assert_called_once()


@pytest.mark.parametrize("failure", ["lost-pid", "adoption"])
def test_unadopted_staged_child_cleanup_is_not_proven_by_bootstrap_exit(
    monkeypatch, broker_channel, tmp_path, failure,
):
    processes, _ = broker_channel
    original = broker.WindowsDaemonProcess

    def process(pid, args):
        if pid == 200:
            raise OSError("cannot adopt child")
        result = original(pid, args)
        result.wait.return_value = 1
        return result

    monkeypatch.setattr(broker, "WindowsDaemonProcess", process)
    monkeypatch.setattr(broker, "_receive", Mock(side_effect=[
        {"pid": 100, "cookie": "fixture-cookie"},
        OSError("lost pid response") if failure == "lost-pid" else {"pid": 200},
    ]))
    with pytest.raises(pu.DaemonSpawnError) as caught:
        _launch(tmp_path)
    assert caught.value.cleanup_confirmed is False
    assert set(processes) == {100}
    processes[100].close.assert_called_once()


@pytest.fixture()
def bootstrap_channel(monkeypatch, tmp_path):
    monkeypatch.setenv(broker._BOOT_COOKIE, "fixture-cookie")
    monkeypatch.setenv(broker._BOOT_PORT, "123")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(socket, "create_connection", Mock(return_value=connection))
    spec = {
        "argv": ["py", "quoted argument"], "cwd": str(tmp_path),
        "log_path": str(tmp_path / "out.log"), "stderr_log_path": str(tmp_path / "err.log"),
        "env": {"VALUE": "kept", broker._BOOT_COOKIE: "not-in-child"},
        "creationflags": pu._CREATE_NO_WINDOW, "deadline": time.monotonic() + 5,
    }
    monkeypatch.setattr(broker, "_receive", Mock(side_effect=[spec, {"start": 77}]))
    monkeypatch.setattr(broker, "_send", Mock())
    process = Mock(pid=77)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(broker, "WindowsDaemonProcess", Mock(return_value=Mock()))
    monkeypatch.setattr(broker, "_resume", Mock())
    return process, spec


def test_bootstrap_preserves_split_logs_and_removes_private_environment(bootstrap_channel):
    process, spec = bootstrap_channel
    assert broker._bootstrap() == 0
    _, kwargs = subprocess.Popen.call_args
    assert kwargs["env"] == {"VALUE": "kept"}
    assert kwargs["creationflags"] == pu._CREATE_NO_WINDOW | broker._CREATE_SUSPENDED
    assert kwargs["cwd"] == spec["cwd"]
    assert kwargs["stdout"].name == spec["log_path"]
    assert kwargs["stderr"].name == spec["stderr_log_path"]
    assert kwargs["stdout"].closed and kwargs["stderr"].closed
    process.kill.assert_not_called()
    broker._resume.assert_called_once()


@pytest.mark.parametrize("fault", ["lost-channel", "adoption", "resume"])
def test_bootstrap_kills_its_uncommitted_suspended_child(
    monkeypatch, bootstrap_channel, fault,
):
    process, spec = bootstrap_channel
    if fault == "resume":
        monkeypatch.setattr(broker, "_resume", Mock(side_effect=OSError("resume failed")))
    else:
        response = OSError("lost channel") if fault == "lost-channel" else {"start": 88}
        monkeypatch.setattr(broker, "_receive", Mock(side_effect=[spec, response]))
    assert broker._bootstrap() == 1
    process.kill.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
