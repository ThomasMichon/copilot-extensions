"""Credential-free protocol and custody checks; never register a host task."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import Mock

import pytest
import windows_broker_protocol_acceptance as protocol_probe
import windows_launch_broker_probe as probe


def test_protocol_fixture_refuses_shared_host_before_any_native_action(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "inspect", Mock(side_effect=AssertionError("host mutation")))
    monkeypatch.setattr(
        protocol_probe.sys, "argv",
        ["fixture", "--ephemeral-ci-consent", str(tmp_path / "ce-broker-probe-owned")],
    )
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.main()
    protocol_probe.inspect.assert_not_called()


def test_protocol_caller_refuses_shared_host_before_loading_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.caller(tmp_path, tmp_path)
    protocol_probe.imports.assert_not_called()


def test_publication_crash_fixture_refuses_shared_host_before_loading_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.crash_publication(tmp_path, tmp_path, "{}", "{}", "task")
    protocol_probe.imports.assert_not_called()


def test_publication_interruption_owner_refuses_shared_host_before_loading_runtime(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.interrupt_publication(tmp_path, tmp_path, {}, {}, "task")
    protocol_probe.imports.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Native nested Windows adapter input and read-only lookup")
def test_production_adapter_reaches_missing_task_with_two_jobs_and_live_outer_stdin(tmp_path):
    import agent_procutil as pu
    import zdd
    from agent_procutil.windows_task_broker import Registration
    from zdd.private_windows_native import native

    tmp_path = tmp_path.resolve()
    entry = tmp_path / "windows_broker_entry.py"
    entry.write_text("raise AssertionError('no broker may be activated')\n", encoding="utf-8")
    config = Registration(
        "missing-native-fixture-" + uuid.uuid4().hex, str(tmp_path),
        str((Path(sys.base_prefix) / "pythonw.exe").resolve(strict=True)),
        str(entry), native().current_sid(),
    )
    paths = [str(Path(module.__file__).resolve().parents[1]) for module in (pu, zdd)]
    program = (
        f"import json,sys;sys.path[:0]={paths!r};"
        "from agent_procutil import DaemonSpawnError;"
        "from agent_procutil.windows_task_broker import _registration,task_operation;"
        "from zdd.private_protocol import Deadline;"
        "assert sys.stdin.readline()=='launch\\n';"
        f"config=_registration(json.loads({json.dumps(config.frame())!r}));"
        "\ntry: task_operation('activate',config,Deadline(10))"
        "\nexcept DaemonSpawnError as error:"
        "\n assert 'broker-adoption-required' in str(error),str(error)"
        "\n print('missing-task-refused',flush=True)"
        "\nelse: raise AssertionError('unexpected task activation')"
    )
    owners = []
    process = None
    try:
        process, outer = pu.spawn_sync_in_kill_on_close_job(
            [str(Path(sys.base_prefix) / "python.exe"), "-I", "-c", program],
            require_job=True, allow_breakaway=False,
            env=protocol_probe.fixture_environment(dict(os.environ), control=True),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=0x08000000,
        )
        assert outer is not None
        owners.append(outer)
        inner = protocol_probe.make_job(pu, owners)
        api = pu._kernel32()
        pu._configure_job_api(api)
        assert api.AssignProcessToJobObject(inner.handle, int(process._handle))
        process.stdin.write("launch\n")
        process.stdin.flush()
        process.wait(timeout=15)
        assert process.returncode == 0, process.stderr.read()
        assert process.stdout.read().strip() == "missing-task-refused"
    finally:
        for owner in reversed(owners):
            if owner.handle is not None:
                owner.terminate_and_wait(5)
        if process is not None:
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def test_protocol_direct_inspection_cannot_bypass_shared_host_refusal(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    copy = Mock(side_effect=AssertionError("fixture escaped admission"))
    monkeypatch.setattr(protocol_probe.shutil, "copytree", copy)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.inspect(tmp_path)
    copy.assert_not_called()


@pytest.mark.parametrize("name", ["SYSTEMROOT", "SystemRoot", "systemroot"])
def test_protocol_fixture_preserves_case_insensitive_windows_prerequisites(name):
    result = protocol_probe.fixture_environment({
        name: r"C:\Windows", "PATH": r"C:\runtime", "GITHUB_ACTIONS": "true",
        "UNRELATED_CREDENTIAL": "must-not-cross",
    }, control=False)
    assert result == {name: r"C:\Windows", "PATH": r"C:\runtime"}
    assert protocol_probe.fixture_environment(
        {"GITHUB_ACTIONS": "true"}, control=True,
    ) == {"GITHUB_ACTIONS": "true"}


def test_protocol_caller_preserves_os_profile_context_but_never_arbitrary_credentials():
    source = {
        "USERPROFILE": r"C:\fixture-profile",
        "LocalAppData": r"C:\fixture-profile\AppData\Local",
        "ProgramFiles": r"C:\Program Files",
        "GITHUB_TOKEN": "must-not-cross",
        "UNRELATED_CREDENTIAL": "must-not-cross",
    }
    assert protocol_probe.fixture_environment(source, control=True) == {
        key: value for key, value in source.items() if "must-not-cross" != value
    }
    assert protocol_probe.fixture_environment(source, control=False) == {}


def test_probe_refuses_inspection_outside_authorized_ci(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.sys, "argv", ["probe", "inspect", str(tmp_path), "a" * 32])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(probe, "Client", lambda *a, **kw: pytest.fail("host pipe opened"))
    with pytest.raises(RuntimeError, match="ephemeral CI"):
        probe.main()


@pytest.mark.parametrize("wire", [b"[]", b"null", b"bad-json"])
def test_probe_rejects_non_object_frames(wire):
    connection = Mock()
    connection.recv_bytes.return_value = wire
    with pytest.raises(ValueError):
        probe.receive(connection)
    connection.recv_bytes.assert_called_once_with(4096)


def test_probe_deadline_precedes_read():
    connection = Mock()
    connection.poll.return_value = False
    with pytest.raises(TimeoutError):
        probe.receive(connection)
    connection.recv_bytes.assert_not_called()


def test_probe_bounds_outgoing_frames():
    connection = Mock()
    with pytest.raises(ValueError, match="budget"):
        probe.send(connection, {"value": "x" * 4096})
    connection.send_bytes.assert_not_called()
    probe.send(connection, {"adopt": 42})
    assert json.loads(connection.send_bytes.call_args.args[0]) == {"adopt": 42}


def native(monkeypatch):
    api = Mock()
    api.OpenProcess.return_value = 100
    api.CloseHandle.return_value = True

    def times(handle, birth, *_):
        birth._obj.dwLowDateTime = 123
        return True

    api.GetProcessTimes.side_effect = times
    monkeypatch.setattr(probe, "kernel", lambda: api)
    monkeypatch.setattr(probe.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(probe.ctypes, "WinError", lambda code: OSError(code, "refused"), raising=False)
    return api


def test_probe_pins_birth_before_control(monkeypatch):
    api = native(monkeypatch)
    with pytest.raises(RuntimeError, match="generation"):
        probe.Process(42, expected_birth=456)
    api.CloseHandle.assert_called_once_with(100)
    api.TerminateProcess.assert_not_called()


def test_probe_never_reopens_pid_for_retirement(monkeypatch):
    api = native(monkeypatch)
    api.WaitForSingleObject.side_effect = [0x102, 0]
    process = probe.Process(42, expected_birth=123)
    process.retire()
    process.close()
    api.OpenProcess.assert_called_once()
    api.TerminateProcess.assert_called_once_with(100, 1)
    api.CloseHandle.assert_called_once_with(100)


def test_probe_retains_custody_on_failed_close(monkeypatch):
    api = native(monkeypatch)
    process = probe.Process(42)
    api.CloseHandle.return_value = False
    with pytest.raises(OSError):
        process.close()
    assert process.handle == 100
    api.CloseHandle.return_value = True
    process.close()
    assert process.handle is None
