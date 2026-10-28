"""Lifetime brokerage preserves the launch contract and owns partial starts."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
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
    from agent_procutil import _daemon_instance_store
    monkeypatch.setattr(_daemon_instance_store, "bind_process", lambda *_: None)
    from agent_procutil.daemon_instances import DaemonInstanceSpec
    from pathlib import Path
    original = pu.spawn_daemon

    def explicit_fixture(*args, **kwargs):
        if "instance" not in kwargs:
            kwargs["instance"] = DaemonInstanceSpec(
                "fixture", Path(kwargs["cwd"]), Path(__file__).resolve(),
            )
        return original(*args, **kwargs)
    monkeypatch.setattr(pu, "spawn_daemon", explicit_fixture)


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


def test_default_nested_job_route_uses_adopted_task_transport(monkeypatch, tmp_path):
    from agent_procutil import windows_task_broker

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(broker, "_activate", Mock(side_effect=AssertionError("legacy WMI")))
    popen = Mock(side_effect=AssertionError("direct child"))
    monkeypatch.setattr(subprocess, "Popen", popen)
    process = Mock(pid=1234)
    launch = Mock(return_value=process)
    monkeypatch.setattr(windows_task_broker, "spawn", launch)
    out, err = tmp_path / "out.log", tmp_path / "err.log"
    command = ["py", "argument with spaces", ""]
    assert pu.spawn_daemon(
        command, cwd=tmp_path, log_path=out, stderr_log_path=err,
        env={"EXACT": "old"}, env_overrides={"EXACT": "new"},
        console_children=True,
    ) is process
    from agent_procutil.daemon_instances import instance_environment
    launch.assert_called_once_with(
        command, cwd=str(tmp_path),
        env={"EXACT": "new", **instance_environment(process.instance_receipt)},
        creationflags=pu._CREATE_NO_WINDOW,
        log_path=str(out), stderr_log_path=str(err),
        instance_receipt=process.instance_receipt,
    )
    broker._activate.assert_not_called()
    popen.assert_not_called()


def test_default_broker_missing_support_refuses_without_legacy_activation(tmp_path):
    from pathlib import Path
    from agent_procutil.owned_process import run_caller_owned

    source = str(Path(pu.__file__).resolve().parents[1])
    program = (
        f"import os,sys; sys.path.insert(0, {source!r});"
        "os.environ.pop('COPILOT_EXTENSIONS_TEST_CONTAINED',None);"
        "import agent_procutil as p;"
        "from agent_procutil.daemon_instances import DaemonInstanceSpec;"
        "from pathlib import Path;"
        "from agent_procutil import daemon_broker as b;"
        "p._is_windows=lambda:True;b.caller_in_job=lambda:True;"
        "\ndef refused(*a,**k): raise AssertionError('legacy activation')"
        "\nb._activate=refused"
        "\ntry:"
        f"\n p.spawn_daemon(['fixture'],cwd={str(tmp_path)!r},"
        f"log_path={str(tmp_path / 'refused.log')!r},"
        f"instance=DaemonInstanceSpec('fixture',Path({str(tmp_path)!r}),Path({str(Path(pu.__file__).resolve())!r})))"
        "\nexcept p.DaemonSpawnError as error:"
        "\n assert error.cleanup_confirmed"
        "\n assert 'windows-broker support' in str(error.__cause__)"
        "\n print('missing-support-refused')"
        "\nelse: raise AssertionError('unsupported launch accepted')"
    )
    result = run_caller_owned(
        [sys.executable, "-I", "-S", "-c", program], timeout=15,
        max_stdout_bytes=16384, max_stderr_bytes=16384,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "missing-support-refused"


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


def _caller_kernel(monkeypatch, members, *, in_job=True, mode="complete"):
    kernel = Mock()
    error = [5]
    kernel.GetCurrentProcess.return_value = -1

    def is_in_job(handle, job, result):
        assert handle == -1 and job is None
        result._obj.value = in_job
        return mode != "query-error"

    def query(job, kind, data, size, returned):
        assert job is None and kind == 3
        capacity = (size - 8) // broker.ctypes.sizeof(broker.ctypes.c_size_t)
        assigned = len(members)
        count = min(capacity, assigned)
        if mode == "oversized":
            assigned = 65537
        if mode == "partial":
            assigned += 1
        broker.ctypes.c_uint32.from_buffer(data, 0).value = assigned
        broker.ctypes.c_uint32.from_buffer(data, 4).value = count
        if mode == "invalid":
            broker.ctypes.c_uint32.from_buffer(data, 4).value = capacity + 1
            return True
        values = (broker.ctypes.c_size_t * count).from_buffer(data, 8)
        values[:] = members[:count]
        if mode == "read-error":
            error[0] = 5
            return False
        if assigned > capacity:
            error[0] = 234
            return False
        return True

    kernel.IsProcessInJob.side_effect = is_in_job
    kernel.QueryInformationJobObject.side_effect = query
    monkeypatch.setattr(broker, "_kernel32", lambda: kernel)
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: error[0], raising=False)
    monkeypatch.setattr(
        broker.ctypes, "WinError", lambda code: OSError(code, "native Job query failed"), raising=False,
    )
    return kernel


@pytest.mark.parametrize("same_job", [False, True])
def test_caller_membership_distinguishes_ambient_jobs(monkeypatch, same_job):
    target = Mock(pid=broker.os.getpid() + 1)
    members = [broker.os.getpid()] + ([target.pid] if same_job else [])
    kernel = _caller_kernel(monkeypatch, members)
    assert broker._outside_caller_job(target) is not same_job
    kernel.IsProcessInJob.assert_called_once()


def test_caller_without_a_job_has_no_job_cleanup_boundary(monkeypatch):
    kernel = _caller_kernel(monkeypatch, [], in_job=False)
    assert broker._outside_caller_job(Mock(pid=99001))
    kernel.QueryInformationJobObject.assert_not_called()


def test_caller_membership_resizes_without_truncating(monkeypatch):
    monkeypatch.setattr(broker.os, "getpid", lambda: 99999)
    members = [broker.os.getpid(), *range(1000, 1130)]
    kernel = _caller_kernel(monkeypatch, members)
    assert broker._outside_caller_job(Mock(pid=99001))
    assert kernel.QueryInformationJobObject.call_count == 2


@pytest.mark.parametrize(
    "mode,members,reason",
    [
        ("query-error", [], "native Job query"),
        ("read-error", [], "native Job query"),
        ("oversized", [], "read budget"),
        ("partial", [0], "bounded read"),
        ("invalid", [], "invalid membership"),
        ("complete", [], "identify the caller"),
        ("complete", [0], "invalid membership"),
        ("complete", [broker.os.getpid(), broker.os.getpid()], "invalid membership"),
    ],
)
def test_unknown_caller_membership_refuses_adoption(monkeypatch, mode, members, reason):
    _caller_kernel(monkeypatch, members, mode=mode)
    with pytest.raises(OSError, match=reason):
        broker._outside_caller_job(Mock(pid=99001))


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
    assert launch.call_count == 1
    assert launch.call_args.args == (["py", "arg with spaces"],)
    passed = launch.call_args.kwargs
    assert passed["env"]["KEY"] == "new" and passed["env"]["OTHER"] == "value"
    assert passed["env"]["COPILOT_EXTENSIONS_DAEMON_INSTANCE"]
    assert passed["instance_receipt"].instance_id == passed["env"]["COPILOT_EXTENSIONS_DAEMON_INSTANCE"]
    assert passed["creationflags"] == pu._CREATE_NO_WINDOW | 0x4000
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
        Mock(side_effect=pu.DaemonSpawnError(
            "sensitive-fixture-detail", process_created=False, admission_sent=False,
        )),
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


@pytest.mark.parametrize("contained", [False, True])
def test_wmi_activation_uses_json_stdin_not_shell_interpolation(monkeypatch, tmp_path, contained):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "contained_test_mode", lambda: contained)
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
    assert payload["creationflags"] == pu.detached_kwargs(breakaway=False)["creationflags"]
    assert not payload["creationflags"] & pu._CREATE_BREAKAWAY_FROM_JOB


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


@pytest.mark.parametrize("contained", [False, True])
def test_broker_preserves_resident_creation_flags(
    monkeypatch, broker_channel, tmp_path, contained,
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "contained_test_mode", lambda: contained)
    _launch(tmp_path)
    spec = broker._send.call_args_list[0].args[1]
    assert spec["creationflags"] == pu._CREATE_NO_WINDOW


@pytest.mark.parametrize("child_outside_any_job", [False, True])
def test_bounded_provider_job_bootstrap_does_not_replace_child_lifetime_check(
    monkeypatch, broker_channel, tmp_path, child_outside_any_job,
):
    processes, _ = broker_channel
    checked = []

    def outside_job(process):
        checked.append(process.pid)
        return child_outside_any_job if process.pid == 200 else True

    monkeypatch.setattr(broker, "_outside_job", outside_job)
    if child_outside_any_job:
        assert _launch(tmp_path) is processes[200]
        assert broker._send.call_args_list[-1].args[1] == {"start": 200}
    else:
        with pytest.raises(pu.DaemonSpawnError, match="child-adoption"):
            _launch(tmp_path)
        assert all(call.args[1] != {"start": 200} for call in broker._send.call_args_list)
        processes[200].terminate.assert_called_once()
    assert checked == [100, 200], "bootstrap and resident must both escape every Job"


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
