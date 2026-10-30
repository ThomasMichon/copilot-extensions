"""Daemon identity covers actual entrypoints without substring ownership."""

from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_bridge import service_process_cli as service
from agent_bridge import service_process_identity as identity
from agent_bridge.service_process_identity import bridge_daemon_argv, split_windows_command


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
def test_supported_daemon_entrypoint(monkeypatch, arguments):
    if "\\" in arguments[0]:
        monkeypatch.setattr(identity.sys, "platform", "win32")
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
def test_other_process_is_not_a_daemon(arguments):
    assert not bridge_daemon_argv(arguments)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path semantics")
@pytest.mark.parametrize("script", ["/runtime/bin/AGENT-BRIDGE", "/runtime/bin/other\\agent-bridge"])
def test_posix_names_are_not_windows_paths(script):
    assert not bridge_daemon_argv(["python", script, "start"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX procfs observation")
@pytest.mark.parametrize("entrypoint", ["module", "console", "argument-only"])
def test_pid_guard_reads_argument_boundaries(monkeypatch, entrypoint):
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
def test_pid_guard_native_argument_fallback(monkeypatch, arguments, expected):
    import psutil

    def missing(*args, **kwargs):
        raise FileNotFoundError

    def process(pid):
        assert pid == 1234
        return SimpleNamespace(cmdline=lambda: arguments)

    monkeypatch.setattr("builtins.open", missing)
    monkeypatch.setattr(psutil, "Process", process)
    assert service._pid_is_agent_bridge(1234, timeout=2) is expected


@pytest.mark.parametrize(("output", "returncode", "expected"), [
    ("console", 0, True), ("module", 0, True),
    ("unrelated", 0, False), ("console", 1, False), ("", 0, False),
])
def test_windows_pid_guard_is_bounded_and_headless(monkeypatch, output, returncode, expected):
    observed = []
    monkeypatch.setattr(service.sys, "platform", "win32")
    monkeypatch.setattr(service, "_powershell_host", lambda: "pwsh")
    monkeypatch.setattr(
        service, "_core", lambda: SimpleNamespace(no_window_kwargs=lambda: {"creationflags": 123}),
    )

    def split(command):
        return {
            "console": ["python.exe", r"C:\Runtime\Scripts\agent-bridge.exe", "start"],
            "module": ["pythonw.exe", "-m", "agent_bridge", "start"],
            "unrelated": ["python.exe", "other.py", "agent_bridge", "start"],
        }[command]

    def run(command, **kwargs):
        assert command[0] == "pwsh"
        assert "-NonInteractive" in command
        assert kwargs["creationflags"] == 123
        assert kwargs["timeout"] == 2
        observed.append(command)
        return SimpleNamespace(stdout=output, returncode=returncode)

    monkeypatch.setattr(identity, "split_windows_command", split)
    monkeypatch.setattr(subprocess, "run", run)
    assert service._pid_is_agent_bridge(1234, timeout=2) is expected
    assert len(observed) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX native fallback observation")
def test_pid_guard_query_failure_refuses(monkeypatch):
    import psutil

    def missing(*args, **kwargs):
        raise FileNotFoundError

    def denied(*args, **kwargs):
        raise psutil.AccessDenied(1234)

    monkeypatch.setattr("builtins.open", missing)
    monkeypatch.setattr(psutil, "Process", denied)
    assert service._pid_is_agent_bridge(1234, timeout=2) is False
    assert service._pid_is_agent_bridge(0) is False


@pytest.mark.parametrize("arguments", [
    ["-m", "agent_bridge", "--json", "start"],
    ["/runtime/bin/agent-bridge", "--json", "start"],
])
def test_json_daemon_shape_matches_actual_root_parser(arguments):
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
def test_windows_native_command_line_roundtrip(arguments):
    assert split_windows_command(subprocess.list2cmdline(arguments)) == arguments
    assert bridge_daemon_argv(arguments)
