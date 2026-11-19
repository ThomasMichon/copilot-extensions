"""Daemon identity covers actual entrypoints without substring ownership."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge import service_process_cli as service
from agent_bridge import service_process_identity as identity
from agent_bridge.service_process_identity import bridge_daemon_argv, split_windows_command


@pytest.mark.parametrize("arguments, expected", [
    (["1.0", "start"], True),
    (["1.0", "--json", "start", "--passive"], True),
    (["start"], False),
    (["1.0"], False),
    (["1.0", "status"], False),
    (["1.0", "--unknown", "start"], False),
])
def test_windows_watchdog_entrypoint_is_recognized_exactly(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], expected: bool,
) -> None:
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="win32"))
    code = identity.WINDOWS_WATCHDOG_START_CODE
    assert bridge_daemon_argv(["python.exe", "-c", code, *arguments]) is expected
    assert not bridge_daemon_argv(["python.exe", "-c", code + ";pass", *arguments])
    assert not bridge_daemon_argv(["python.exe", "-c", "pass", code, *arguments])


def test_windows_watchdog_entrypoint_is_not_posix_exec_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="linux"))
    assert not bridge_daemon_argv([
        "python", "-c", identity.WINDOWS_WATCHDOG_START_CODE, "1.0", "start",
    ])


def test_leased_python_wrapper_identifies_only_the_bridge_start_entrypoint(tmp_path: Path) -> None:
    from agent_procutil._daemon_instance_child import _BOOTSTRAP, leased_python_command

    prefix = ["python", "-c", _BOOTSTRAP, str(tmp_path)]
    assert bridge_daemon_argv([*prefix, "-m", "agent_bridge", "start", "--passive"])
    assert not bridge_daemon_argv([*prefix, "-m", "agent_bridge", "status"])
    assert not bridge_daemon_argv([*prefix, "-m", "other", "start"])
    assert not bridge_daemon_argv(["python", "-c", _BOOTSTRAP, ".", "-m", "agent_bridge", "start"])
    assert not bridge_daemon_argv([
        "python", "-c", _BOOTSTRAP + ";pass", "/runtime", "-m", "agent_bridge", "start",
    ])
    actual, _environment = leased_python_command([sys.executable, "-m", "agent_bridge", "start"])
    assert bridge_daemon_argv(actual)
    if sys.platform == "win32":
        assert bridge_daemon_argv(split_windows_command(subprocess.list2cmdline(actual)))


@pytest.mark.parametrize("arguments", [
    ["python", "-m", "agent_bridge", "start"],
    ["/runtime/bin/python3.10", "-u", "-m", "agent_bridge", "start", "--passive"],
    ["/runtime/bin/python", "/runtime/bin/agent-bridge", "start"],
    ["/runtime with spaces/bin/python", "-I", "-W", "ignore", "-X", "utf8",
     "/runtime with spaces/bin/agent-bridge", "start", "--port", "9011"],
    [r"C:\Runtime\pythonw.exe", "-u", "-m", "agent_bridge", "start"],
    [r"C:\Runtime\python.exe", r"C:\Runtime\Scripts\agent-bridge.exe", "start"],
    [r"C:\Runtime\Scripts\agent-bridge.exe", "start"],
    [r"C:\Runtime\Scripts\AGENT-BRIDGE.EXE", "start"],
    ["python", "--", "/runtime/bin/agent-bridge", "start"],
    ["python", "-m", "agent_bridge", "--json", "start"],
    ["python", "/runtime/bin/agent-bridge", "--json", "start"],
    [r"C:\Runtime\Scripts\agent-bridge.exe", "--json", "start"],
    ["agent-bridge", "--json", "--json", "start"],
])
def test_supported_daemon_entrypoint(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    if "\\" in arguments[0]:
        monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="win32"))
    assert bridge_daemon_argv(arguments)


@pytest.mark.parametrize("arguments", [
    [], ["python"], ["python", "-m", "agent_bridge"],
    ["python", "-m", "agent_bridge", "status"],
    ["python", "-m", "agent_bridge_other", "start"],
    ["python", "-c", "pass", "agent_bridge", "start"],
    ["python", "-c", "pass", "/runtime/bin/agent-bridge", "start"],
    ["python", "/runtime/other.py", "agent_bridge", "start"],
    ["python", "/runtime/other.py", "/runtime/bin/agent-bridge", "start"],
    ["python", "/runtime/bin/agent-bridge-backup", "start"],
    ["python", "/runtime/agent_bridge/other.py", "start"],
    ["python", "/runtime/bin/agent-bridge", "status"],
    ["echo", "/runtime/bin/agent-bridge", "start"],
    ["not-python", "-m", "agent_bridge", "start"],
    ["python", "-W", "/runtime/bin/agent-bridge", "start"],
    ["python", "-X"],
    ["python", "", "/runtime/bin/agent-bridge", "start"],
    ["python", "-m", "agent_bridge", "--json", "status"],
    ["python", "/runtime/bin/agent-bridge", "--json", "status"],
    ["agent-bridge", "--json", "status", "start"],
    ["python", "-c", "pass", "agent_bridge", "--json", "start"],
    ["python", "other.py", "/runtime/bin/agent-bridge", "--json", "start"],
    ["agent-bridge", "--unknown", "start"],
])
def test_other_process_is_not_a_daemon(arguments: list[str]) -> None:
    assert not bridge_daemon_argv(arguments)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path semantics")
@pytest.mark.parametrize("script", ["/runtime/bin/AGENT-BRIDGE", "/runtime/bin/other\\agent-bridge"])
def test_posix_names_are_not_windows_paths(script: str) -> None:
    assert not bridge_daemon_argv(["python", script, "start"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX procfs observation")
@pytest.mark.parametrize("entrypoint", ["module", "console", "argument-only"])
def test_pid_guard_reads_argument_boundaries(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str,
) -> None:
    arguments = ["/runtime/bin/python", "/runtime/bin/agent-bridge", "start"]
    if entrypoint == "module":
        arguments = ["/runtime/bin/python", "-m", "agent_bridge", "start"]
    elif entrypoint == "argument-only":
        arguments = ["/runtime/bin/python", "-c", "pass", *arguments]
    monkeypatch.setattr(
        "builtins.open", lambda *a, **k: io.BytesIO(b"\0".join(x.encode() for x in arguments) + b"\0"),
    )
    assert service._pid_is_agent_bridge(1234) is (entrypoint != "argument-only")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fallback observation")
@pytest.mark.parametrize(("arguments", "expected"), [
    (["/runtime with spaces/bin/python", "/runtime with spaces/bin/agent-bridge", "start"], True),
    (["python", "-m", "agent_bridge", "start"], True),
    (["python", "-c", "pass", "agent_bridge", "start"], False),
])
def test_pid_guard_native_argument_fallback(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], expected: bool,
) -> None:
    import psutil

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    def process(pid: int) -> SimpleNamespace:
        assert pid == 1234
        return SimpleNamespace(cmdline=lambda: arguments)

    monkeypatch.setattr("builtins.open", missing)
    monkeypatch.setattr(psutil, "Process", process)
    assert service._pid_is_agent_bridge(1234, timeout=2) is expected


@pytest.mark.parametrize(("arguments", "expected"), [
    (["python.exe", r"C:\Runtime\Scripts\agent-bridge.exe", "start"], True),
    (["pythonw.exe", "-m", "agent_bridge", "start"], True),
    (["python.exe", "other.py", "agent_bridge", "start"], False),
    ([], False),
])
def test_windows_pid_guard_observes_native_arguments_with_short_probe(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], expected: bool,
) -> None:
    import psutil

    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="win32"))

    def process(pid: int) -> SimpleNamespace:
        assert pid == 1234
        return SimpleNamespace(cmdline=lambda: arguments)

    def slow_shell(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("powershell", 1.0)

    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(subprocess, "run", slow_shell)
    assert service._pid_is_agent_bridge(1234, timeout=1.0) is expected


@pytest.mark.parametrize("failure", ["missing", "denied"])
def test_windows_pid_guard_refuses_unavailable_native_process(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    import psutil

    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="win32"))

    def unavailable(pid: int) -> psutil.Process:
        assert pid == 1234
        if failure == "missing":
            raise psutil.NoSuchProcess(pid)
        raise psutil.AccessDenied(pid)

    def no_shell(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unavailable native identity must not launch another observer")

    monkeypatch.setattr(psutil, "Process", unavailable)
    monkeypatch.setattr(subprocess, "run", no_shell)
    assert service._pid_is_agent_bridge(1234, timeout=1.0) is False
    assert service._pid_is_agent_bridge(0, timeout=1.0) is False


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process observation")
@pytest.mark.parametrize(("name", "command", "expected"), [
    ("agent-bridge", "start", True),
    ("agent-bridge", "status", False),
    ("other", "start", False),
])
def test_windows_pid_guard_reads_real_process_arguments(
    tmp_path: Path, name: str, command: str, expected: bool,
) -> None:
    script = tmp_path / name
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        creationflags = 0
    process = subprocess.Popen(
        [sys.executable, str(script), command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        assert process.poll() is None
        assert service._pid_is_agent_bridge(process.pid, timeout=1.0) is expected
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX native fallback observation")
def test_pid_guard_query_failure_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    import psutil

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    def denied(*args: object, **kwargs: object) -> None:
        raise psutil.AccessDenied(1234)

    monkeypatch.setattr("builtins.open", missing)
    monkeypatch.setattr(psutil, "Process", denied)
    assert service._pid_is_agent_bridge(1234, timeout=2) is False
    assert service._pid_is_agent_bridge(0) is False


@pytest.mark.parametrize("arguments", [
    ["-m", "agent_bridge", "--json", "start"],
    ["/runtime/bin/agent-bridge", "--json", "start"],
])
def test_json_daemon_shape_matches_actual_root_parser(arguments: list[str]) -> None:
    from agent_bridge.__main__ import build_parser

    cli = arguments[2:] if arguments[0] == "-m" else arguments[1:]
    parsed = build_parser().parse_args(cli)
    assert parsed.command == "start"
    assert parsed.json is True
    assert bridge_daemon_argv(["python", *arguments])


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows command-line quoting")
@pytest.mark.parametrize("arguments", [
    [r"C:\Runtime with spaces\python.exe", r"C:\Runtime with spaces\Scripts\agent-bridge.exe", "start"],
    [r"C:\Runtime\pythonw.exe", "-u", "-m", "agent_bridge", "start"],
])
def test_windows_native_command_line_roundtrip(arguments: list[str]) -> None:
    assert split_windows_command(subprocess.list2cmdline(arguments)) == arguments
    assert bridge_daemon_argv(arguments)
