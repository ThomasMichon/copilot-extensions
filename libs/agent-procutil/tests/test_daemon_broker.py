"""Lifetime brokerage preserves the launch contract and owns partial starts."""

from __future__ import annotations

import json
import os
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


def _durable_launch(monkeypatch, *, side_effect=None):
    process = Mock(pid=1234)
    launch = Mock(return_value=process, side_effect=side_effect)
    monkeypatch.setattr(daemon, "_durable_windows_launch", launch)
    return process, launch


def test_durable_windows_launch_always_uses_wmi_even_outside_a_job(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    popen = Mock(side_effect=AssertionError("native durable creation"))
    monkeypatch.setattr(subprocess, "Popen", popen)
    task = Mock(side_effect=AssertionError("automatic Task activation"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", task)
    process, launch = _durable_launch(monkeypatch)
    assert pu.spawn_daemon(
        ["py"], cwd=tmp_path, log_path=tmp_path / "out.log", console_children=True,
    ) is process
    assert launch.call_args.kwargs["creationflags"] == (
        pu._CREATE_NO_WINDOW | pu._CREATE_BREAKAWAY_FROM_JOB
    )
    popen.assert_not_called()
    task.assert_not_called()
    log = (tmp_path / "out.log").read_text(encoding="utf-8")
    assert "wmi-durable-launch" in log and "instance-bound" in log


def test_durable_route_preserves_exact_arguments_and_environment(monkeypatch, tmp_path):
    from agent_procutil import windows_task_broker

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("native")))
    task = Mock(side_effect=AssertionError("automatic Task activation"))
    monkeypatch.setattr(windows_task_broker, "spawn", task)
    process, launch = _durable_launch(monkeypatch)
    out, err = tmp_path / "out.log", tmp_path / "err.log"
    command = ["py", "argument with spaces", ""]
    assert pu.spawn_daemon(
        command, cwd=tmp_path, log_path=out, stderr_log_path=err,
        env={"EXACT": "old"}, env_overrides={"EXACT": "new"},
        console_children=True,
    ) is process
    from agent_procutil.daemon_instances import instance_environment
    assert launch.call_args.args == (command,)
    passed = launch.call_args.kwargs
    assert passed["env"] == {
        "EXACT": "new", **instance_environment(process.instance_receipt),
    }
    assert passed["cwd"] == str(tmp_path)
    assert str(passed["log_path"]) == str(out)
    assert str(passed["stderr_log_path"]) == str(err)
    assert passed["instance_receipt"] is process.instance_receipt
    task.assert_not_called()


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
        "\n from agent_procutil.daemon import spawn_brokered_daemon"
        f"\n spawn_brokered_daemon(['fixture'],cwd={str(tmp_path)!r},"
        f"log_path={str(tmp_path / 'refused.log')!r},"
        "\n env={},creationflags=0,stderr_log_path=None)"
        "\nexcept p.DaemonSpawnError as error:"
        "\n assert error.cleanup_confirmed"
        "\n assert 'windows-broker support' in str(error)"
        "\n print('missing-support-refused')"
        "\nelse: raise AssertionError('unsupported launch accepted')"
    )
    result = run_caller_owned(
        [sys.executable, "-I", "-S", "-c", program], timeout=15,
        max_stdout_bytes=16384, max_stderr_bytes=16384,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "missing-support-refused"


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


def test_blocked_wmi_uses_only_explicit_contained_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    process = Mock(pid=1234)
    popen = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", popen)
    _durable_launch(monkeypatch, side_effect=pu.DaemonSpawnError(
        "Windows durable launch via WMI Win32_Process.Create failed",
        process_created=False, admission_sent=False,
    ))

    result = pu.spawn_daemon(
        ["py", "arg with spaces"], cwd=tmp_path, log_path=tmp_path / "out.log",
        stderr_log_path=tmp_path / "err.log", env={"KEY": "old"},
        env_overrides={"KEY": "new", "OTHER": "value"}, console_children=True,
        extra_creationflags=0x4000, contained_fallback=True,
    )

    assert result is process
    assert popen.call_count == 1
    assert popen.call_args.args == (["py", "arg with spaces"],)
    passed = popen.call_args.kwargs
    assert passed["env"]["KEY"] == "new" and passed["env"]["OTHER"] == "value"
    assert passed["env"]["COPILOT_EXTENSIONS_DAEMON_INSTANCE"]
    assert process.instance_receipt.instance_id == passed["env"]["COPILOT_EXTENSIONS_DAEMON_INSTANCE"]
    assert passed["creationflags"] == pu._CREATE_NO_WINDOW | 0x4000
    for name in ("out.log", "err.log"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "accepts caller-owned lifetime" in text
        assert "arg with spaces" not in text and "value" not in text


def test_blocked_wmi_without_fallback_is_explicit(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    _durable_launch(monkeypatch, side_effect=pu.DaemonSpawnError(
        f"Windows durable launch via WMI failed; {broker.WMI_POLICY_HINT}",
        process_created=False, admission_sent=False,
    ))
    with pytest.raises(pu.DaemonSpawnError, match="PSExec and WMI") as caught:
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "out.log")
    assert caught.value.process_created is False
    popen.assert_not_called()
    from agent_procutil.daemon_instances import inspect_instances
    assert inspect_instances() == []


@pytest.mark.parametrize("contained", [False, True])
def test_containment_never_uses_the_broker(monkeypatch, tmp_path, contained):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1" if contained else "0")
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=_denied()))
    launch = Mock()
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", launch)
    _, durable = _durable_launch(monkeypatch)
    with pytest.raises(OSError):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "out.log",
            breakaway=contained,
        )
    launch.assert_not_called()
    durable.assert_not_called()


@pytest.mark.parametrize("name", [
    "COPILOT_EXTENSIONS_TEST_CONTAINED", "copilot_extensions_test_contained",
])
@pytest.mark.parametrize("route", ["env", "env_overrides"])
def test_contained_child_environment_never_uses_wmi(monkeypatch, tmp_path, route, name):
    if name != name.upper() and os.name != "nt":
        pytest.skip("environment names are case-insensitive only on Windows")
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "contained_test_mode", lambda: False)
    popen = Mock(return_value=Mock(pid=4321))
    monkeypatch.setattr(subprocess, "Popen", popen)
    _, durable = _durable_launch(monkeypatch)
    marker = {name: "1"}
    extra = (
        {"env": {**os.environ, **marker}} if route == "env" else {"env_overrides": marker}
    )
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "out.log", **extra)
    durable.assert_not_called()
    popen.assert_called_once()
    flags = popen.call_args.kwargs.get("creationflags", 0)
    assert not flags & pu._CREATE_BREAKAWAY_FROM_JOB


def test_broker_errors_are_sanitized_in_both_logs(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=_denied()))
    _durable_launch(monkeypatch, side_effect=pu.DaemonSpawnError(
        "sensitive-fixture-detail", process_created=False, admission_sent=False,
    ))
    with pytest.raises(pu.DaemonSpawnError):
        pu.spawn_daemon(
            ["py", "sensitive-fixture-detail"], cwd=tmp_path,
            log_path=tmp_path / "out.log", stderr_log_path=tmp_path / "err.log",
        )
    for name in ("out.log", "err.log"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "daemon not started" in text and "sensitive-fixture-detail" not in text


def test_uncertain_cleanup_prohibits_a_contained_retry(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    _durable_launch(monkeypatch, side_effect=pu.DaemonSpawnError(
        "Windows lifetime broker failed (child)", cleanup_confirmed=False,
        process_created=True, process_identity=(123, "fixture-birth"),
    ))
    with pytest.raises(pu.DaemonSpawnError) as caught:
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "out.log", contained_fallback=True,
        )
    assert caught.value.cleanup_confirmed is False
    popen.assert_not_called()  # uncertainty must not create a second daemon


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

    def accept(listener, cookie, pid, deadline):
        hello = broker._receive(connection, deadline)
        if not broker._authenticated(hello, cookie):
            raise TimeoutError("only unauthenticated clients connected")
        if hello.get("pid") != pid:
            raise OSError("daemon broker identity mismatch")
        return connection

    monkeypatch.setattr(broker, "_accept_bootstrap", accept)
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


@pytest.mark.parametrize("bootstrap_in_provider_job", [False, True])
@pytest.mark.parametrize("child_outside_any_job", [False, True])
def test_bounded_provider_job_bootstrap_does_not_replace_child_lifetime_check(
    monkeypatch, broker_channel, tmp_path, child_outside_any_job, bootstrap_in_provider_job,
):
    processes, _ = broker_channel
    checked = []

    def outside_job(process):
        checked.append(process.pid)
        if process.pid == 200:
            return child_outside_any_job
        return not bootstrap_in_provider_job

    monkeypatch.setattr(broker, "_outside_job", outside_job)
    if child_outside_any_job:
        assert _launch(tmp_path) is processes[200]
        assert broker._send.call_args_list[-1].args[1] == {"start": 200}
    else:
        with pytest.raises(pu.DaemonSpawnError, match="child-adoption"):
            _launch(tmp_path)
        assert all(call.args[1] != {"start": 200} for call in broker._send.call_args_list)
        processes[200].terminate.assert_called_once()
    assert checked == [200], "a provider-Job bootstrap is allowed; only the resident must escape"


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


@pytest.mark.parametrize("max_pending", [4, 32])
def test_competing_local_clients_cannot_block_bootstrap_admission(monkeypatch, max_pending):
    # Serial two-second reads of 20 silent peers would far exceed this
    # 3-second deadline; concurrent bounded handling admits the bootstrap at once.
    monkeypatch.setattr(broker, "_MAX_PENDING", max_pending)
    cookie, pid = "fixture-cookie", 4242
    clients = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        address = listener.getsockname()
        try:
            clients.extend(socket.create_connection(address) for _ in range(20))
            garbage = socket.create_connection(address)
            garbage.sendall(b"\xff\xff\xff\xff")
            oversized = socket.create_connection(address)
            oversized.sendall((broker._MAX_HELLO + 1).to_bytes(4, "big"))
            impostor = socket.create_connection(address)
            broker._send(impostor, {"cookie": "wrong", "pid": pid}, time.monotonic() + 5)
            genuine = socket.create_connection(address)
            clients += [garbage, oversized, impostor, genuine]
            broker._send(genuine, {"cookie": cookie, "pid": pid}, time.monotonic() + 5)
            started = time.monotonic()
            with broker._accept_bootstrap(
                listener, cookie, pid, time.monotonic() + 3,
            ) as accepted:
                elapsed = time.monotonic() - started
                broker._send(accepted, {"ok": True}, time.monotonic() + 5)
                assert broker._receive(genuine, time.monotonic() + 5) == {"ok": True}
        finally:
            for client in clients:
                client.close()
    assert elapsed < 1.5, "silent peers delayed a ready authenticated bootstrap"


def test_only_silent_clients_fail_at_the_deadline():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        with socket.create_connection(listener.getsockname()):
            with pytest.raises(TimeoutError):
                broker._accept_bootstrap(listener, "cookie", 1, time.monotonic() + 0.3)


def test_authenticated_client_with_wrong_pid_is_an_identity_failure():
    cookie = "fixture-cookie"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(broker._BACKLOG)
        with socket.create_connection(listener.getsockname()) as client:
            broker._send(client, {"cookie": cookie, "pid": 1}, time.monotonic() + 5)
            with pytest.raises(OSError, match="identity mismatch"):
                broker._accept_bootstrap(listener, cookie, 2, time.monotonic() + 5)


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
    if (pid, method) == (200, "wait"):
        # An unconfirmed child exit keeps its pinned handle for the caller.
        processes[200].close.assert_not_called()
        assert caught.value.process is processes[200]
    else:
        processes[200].close.assert_called_once()
        assert getattr(caught.value, "process", None) is None
    processes[200].terminate.assert_called_once()


def test_failed_child_termination_transfers_live_process_control(
    monkeypatch, broker_channel, tmp_path,
):
    processes, _ = broker_channel
    original = broker.WindowsDaemonProcess

    def process(number, args):
        result = original(number, args)
        if number == 200:
            result.terminate.side_effect = OSError("access denied")
            result.wait.side_effect = subprocess.TimeoutExpired(args, 5)
        return result

    monkeypatch.setattr(broker, "WindowsDaemonProcess", process)
    monkeypatch.setattr(broker, "_outside_job", lambda process: process.pid != 200)
    with pytest.raises(pu.DaemonSpawnError) as caught:
        _launch(tmp_path)
    assert caught.value.cleanup_confirmed is False
    assert caught.value.process is processes[200]
    processes[200].close.assert_not_called()
    processes[100].close.assert_called_once()


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


def test_stdio_transfer_failure_never_stages_a_child(monkeypatch, broker_channel, tmp_path):
    processes, _ = broker_channel
    monkeypatch.setattr(broker, "_duplicate_into", Mock(side_effect=OSError("dup failed")))
    with pytest.raises(pu.DaemonSpawnError, match="child-staging") as raised:
        broker.spawn_brokered_daemon(
            ["actual-daemon"], cwd=str(tmp_path), env={}, creationflags=0,
            log_path=str(tmp_path / "out.log"), stderr_log_path=None,
            stdio_handles=(11, 12, 13),
        )
    assert raised.value.process_created is False
    broker._send.assert_not_called()
    assert set(processes) == {100}
    processes[100].close.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle duplication")
def test_partial_handle_duplication_closes_copies_in_the_bootstrap(monkeypatch):
    closed_in_target, duplicated = [], []

    class Kernel:
        def __init__(self):
            self.GetCurrentProcess = Mock(return_value=-1)
            self.OpenProcess = Mock(return_value=99)
            self.CloseHandle = Mock(return_value=True)
            self.DuplicateHandle = Mock(side_effect=self.duplicate)

        @staticmethod
        def duplicate(source, handle, target, copy, access, inherit, options):
            if options == broker._DUPLICATE_CLOSE_SOURCE:
                assert source == 99 and copy is None
                closed_in_target.append(handle)
                return True
            if handle == 13:
                return False
            copy._obj.value = handle + 1000
            duplicated.append(handle + 1000)
            return True

    kernel = Kernel()
    monkeypatch.setattr(broker, "_kernel32", lambda: kernel)
    with pytest.raises(OSError):
        broker._duplicate_into(Mock(pid=100), [11, 12, 13])
    assert closed_in_target == duplicated == [1011, 1012]
    kernel.CloseHandle.assert_called_once_with(99)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle reconstruction")
def test_bootstrap_handle_reconstruction_failure_starts_nothing(
    monkeypatch, bootstrap_channel,
):
    import msvcrt

    process, spec = bootstrap_channel
    spec["stdio_handles"] = [11, 12, 13]
    opened = []

    def open_osfhandle(handle, flags):
        if handle == 12:
            raise OSError("invalid handle")
        descriptor = os.open(os.devnull, os.O_RDONLY)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(msvcrt, "open_osfhandle", open_osfhandle)
    assert broker._bootstrap() == 1
    subprocess.Popen.assert_not_called()
    process.kill.assert_not_called()
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])
