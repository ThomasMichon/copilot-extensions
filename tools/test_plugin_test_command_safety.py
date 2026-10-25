"""Command refusal controls never execute a real destructive backend."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import plugin_test_command_safety as safety


@pytest.mark.parametrize("command", [
    ["psmux", "kill-server"],
    ["tmux", "-S", "/private/mux.sock", "kill-server"],
    ["C:\\Program Files\\PSMux\\PSMUX.EXE", "kill-server"],
    ["pkill", "-f", "copilot"],
    ["killall", "python"],
    ["taskkill.exe", "/IM", "python.exe", "/F"],
    ["taskkill", "/FI", "USERNAME eq example", "/F"],
    ["taskkill", "/PID", "*", "/F"],
    ["Stop-Process", "-Name", "python"],
    ["Stop-Process", "-n:python"],
    ["kill", "-TERM", "-1"],
    ["wsl.exe", "--shutdown"],
    ["wsl", "--terminate", "fixture"],
    ["systemctl", "--user", "stop", "example"],
    ["sc.exe", "stop", "example"],
    ["docker", "system", "prune", "-af"],
    ["podman", "container", "prune"],
    ["docker", "stop", "$(docker ps -aq)"],
    ["ssh", "fixture-host", "pkill -f copilot"],
    ["sudo", "systemctl", "stop", "example"],
])
def test_global_cleanup_is_refused_even_with_host_state_opt_in(monkeypatch, tmp_path, command):
    monkeypatch.setenv("COPILOT_EXTENSIONS_ALLOW_HOST_STATE", "1")
    assert safety.command_problem(command, sandbox=tmp_path)


@pytest.mark.parametrize("command", [
    ["taskkill", "/PID", "123", "/T", "/F"],
    ["git", "status", "--porcelain"],
    [sys.executable, "-I", "-c", "print('fixture')"],
    ["psmux", "-V"],
    ["tmux", "--help"],
])
def test_bounded_commands_and_inspection_are_not_globally_banned(command):
    assert safety.command_problem(command) is None


def test_nul_command_is_refused_without_truncating_its_inspection():
    with pytest.raises(safety.UnsafeTestCommand):
        safety.command_problem("psmux\0kill-server")


@pytest.mark.skipif(os.name != "nt", reason="native Windows command-line semantics")
def test_windows_audit_command_line_roundtrips_nested_python_quotes_and_newlines():
    script = "import json\nvalue = '\\\\quoted\\\\'\nprint(json.dumps({'value': value}))"
    arguments = [sys.executable, "-c", script, 'spaced "argument"', r"C:\path with spaces\end\\"]
    rendered = subprocess.list2cmdline(arguments)
    assert safety._tokens(rendered) == arguments
    assert safety.command_problem(rendered) is None


@pytest.mark.parametrize("command", [
    ["psmux", "kill-session", "-t", "wt-live"],
    ["psmux", "new-session", "-d", "-s", "fixture"],
    ["psmux", "-L", "default", "kill-session", "-t", "fixture"],
    ["tmux", "-S", "/outside/mux.sock", "kill-session", "-t", "fixture"],
    ["psmux", "-L"],
    ["psmux", "kill-session", "-t", "wt-live", "--help"],
    ["psmux", "-S", "private.sock", "-L", "default", "kill-session", "-t", "owned"],
    ["psmux", "-Ldefault", "kill-session", "-t", "owned"],
])
def test_redirected_home_and_data_directory_do_not_qualify_a_mux(monkeypatch, tmp_path, command):
    environment = {
        "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "PSMUX_DATA_DIR": str(tmp_path / "mux"),
    }
    assert safety.command_problem(command, environment=environment, sandbox=tmp_path)


def test_namespaces_are_bound_to_one_runner_and_validated_before_use(monkeypatch, tmp_path):
    monkeypatch.setenv(safety.SANDBOX_ENV, str(tmp_path))
    root = tmp_path / "fixture"
    root.mkdir()
    arguments = safety.private_mux_args(root)
    assert safety.validate_private_mux_args(arguments, sandbox=tmp_path) == arguments
    command = ["psmux", *arguments, "kill-session", "-t", "owned"]
    assert safety.command_problem(command, sandbox=tmp_path) is None
    assert safety.command_problem(command, sandbox=tmp_path / "foreign")
    for invalid in (None, [], ["-L", "default"], ["-L", 1]):
        with pytest.raises(safety.UnsafeTestCommand):
            safety.validate_private_mux_args(invalid, sandbox=tmp_path)
    with pytest.raises(safety.UnsafeTestCommand):
        safety.private_mux_args(tmp_path.parent / "foreign")
    monkeypatch.delenv(safety.SANDBOX_ENV)
    with pytest.raises(safety.UnsafeTestCommand):
        safety.private_mux_args(root)


def test_windows_namespace_is_fresh_and_not_an_environment_only_override(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "os", SimpleNamespace(
        name="nt", environ={safety.SANDBOX_ENV: str(tmp_path)},
        path=os.path, fsdecode=os.fsdecode,
    ))
    first = safety.private_mux_args(tmp_path)
    second = safety.private_mux_args(tmp_path)
    assert first[0] == second[0] == "-L" and first != second
    assert safety.command_problem(["psmux", *first, "has-session"], sandbox=tmp_path) is None
    assert safety.command_problem(
        ["psmux", "has-session"], sandbox=tmp_path,
        environment={"TMUX": str(tmp_path / "mux.sock") + ",1,0"},
    )
    assert safety.command_problem(
        ["psmux", "-S", str(tmp_path / "mux.sock"), "kill-session", "-t", "owned"],
        sandbox=tmp_path,
    )


def test_posix_tmux_accepts_only_its_documented_private_socket(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "os", SimpleNamespace(
        name="posix", environ={}, path=os.path, fsdecode=os.fsdecode,
    ))
    environment = {"TMUX": str(tmp_path / "mux.sock") + ",1,0"}
    assert safety.command_problem(
        ["tmux", "display-message"], sandbox=tmp_path, environment=environment,
    ) is None
    assert safety.command_problem(
        ["psmux", "display-message"], sandbox=tmp_path, environment=environment,
    )
    assert safety.command_problem(
        ["tmux", "display-message"], sandbox=tmp_path,
        environment={"TMUX": str(tmp_path.parent / "foreign.sock") + ",1,0"},
    )
    assert safety.command_problem(
        ["tmux", "-vLdefault", "display-message"], sandbox=tmp_path,
        environment=environment,
    )


def test_child_arguments_cannot_qualify_the_parent_mux_namespace(monkeypatch, tmp_path):
    monkeypatch.setenv(safety.SANDBOX_ENV, str(tmp_path))
    private = safety.private_mux_args(tmp_path)
    for command in (
        ["psmux", "new-session", "--", sys.executable, *private],
        ["psmux", "kill-session", "-t", "owned", *private],
        ["tmux", "new-session", "--", sys.executable, "-S", str(tmp_path / "mux.sock")],
    ):
        assert safety.command_problem(command, sandbox=tmp_path, environment={})


@pytest.mark.parametrize("command", [
    ["pwsh", "-NoProfile", "-Command", "Stop-Process -Name python -Force"],
    ["bash", "-c", "pkill -f copilot"],
    ["cmd.exe", "/c", "taskkill /IM python.exe /F"],
    ["pwsh", "-Command", "psmux kill-session -t wt-live"],
    ["sh", "-c", "docker system prune -af"],
    ["pwsh", "-Command", "& 'C:\\Program Files\\PSMux\\psmux.exe' kill-session -t wt-live"],
    ["pwsh", "-Command", "kill python -Force"],
    ["powershell.exe", "-Command", "spps python -Force"],
    ["pwsh", "-Command", "Restart-Service example"],
])
def test_shell_wrappers_do_not_hide_global_commands(command, tmp_path):
    assert safety.command_problem(command, sandbox=tmp_path)


@pytest.mark.parametrize("flag", ["-EncodedCommand", "-enc", "-ec", "-e"])
def test_encoded_powershell_cleanup_is_inspected(tmp_path, flag):
    encoded = base64.b64encode("psmux kill-server".encode("utf-16-le")).decode("ascii")
    assert safety.command_problem(["pwsh", flag, encoded], sandbox=tmp_path)
    assert safety.command_problem(["pwsh", "-EncodedCommand", "!invalid!"], sandbox=tmp_path)
    safe = base64.b64encode("Write-Output fixture".encode("utf-16-le")).decode("ascii")
    assert safety.command_problem(["pwsh", "-EncodedCommand", safe], sandbox=tmp_path) is None
    assert safety.command_problem(["pwsh", "-Command", "Stop-Process -Id 123"], sandbox=tmp_path) is None


@pytest.mark.parametrize("body", [
    "command('kill-server')",
    "subprocess.run([tmux, '-S', socket, 'kill-server'])",
    "subprocess.run(['taskkill', '/IM', 'python.exe', '/F'])",
    "os.system('pkill -f copilot')",
    "subprocess.run(['wsl', '--shutdown'])",
    "os.kill(-1, 15)",
    "os.system(b'pkill -f copilot')",
    "subprocess.run([tmux, b'kill-server'])",
    r"command('ki\x6cl-server')",
    "subprocess.run(['w' 'sl', '--shut' 'down'])",
])
def test_source_preflight_rejects_child_cleanup_without_importing_it(tmp_path, body):
    source = tmp_path / "child.py"
    sentinel = tmp_path / "imported"
    source.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n{body}\n")
    with pytest.raises(safety.UnsafeTestCommand, match="unsafe test cleanup"):
        safety.check_test_sources(tmp_path)
    assert not sentinel.exists()


@pytest.mark.parametrize("name,body", [
    ("child.ps1", "Stop-Process -Name python -Force\n"),
    ("child.sh", "#!/bin/sh\npkill -f copilot\n"),
    ("child.cmd", "taskkill /IM python.exe /F\n"),
    ("child.py", "SCRIPT = '''\npsmux kill-server\n'''\n"),
])
def test_shell_helpers_and_generated_script_literals_are_checked(tmp_path, name, body):
    (tmp_path / name).write_text(body)
    with pytest.raises(safety.UnsafeTestCommand):
        safety.check_test_sources(tmp_path)


def test_comments_mock_expectations_and_owned_process_handles_are_not_calls(tmp_path):
    source = tmp_path / "test_fixture.py"
    source.write_text(
        "# Never call psmux kill-server.\n"
        "expected = ['psmux', 'kill-server']\n"
        "def test_cleanup(process):\n"
        "    assert expected[1] == 'kill-server'\n"
        "    process.terminate()\n"
    )
    assert safety.source_problems(source) == []


def test_benign_source_does_not_pay_for_ast_analysis(tmp_path, monkeypatch):
    source = tmp_path / "helper.py"
    source.write_text("def helper(): return 1\n")
    monkeypatch.setattr(
        safety.ast, "parse", lambda *a, **k: pytest.fail("benign helper was parsed"),
    )
    assert safety.source_problems(source) == []


def test_real_popen_is_refused_before_even_a_harmless_standin_can_execute(tmp_path):
    sentinel = tmp_path / "executed"
    (tmp_path / "kill-server").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    )
    # Even if refusal regresses, executable is pinned to Python, never psmux.
    command = ["psmux", "kill-server"]
    with pytest.raises(safety.UnsafeTestCommand):
        subprocess.run(
            command, executable=sys.executable, cwd=tmp_path,
            capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    assert not sentinel.exists()


def test_guard_prevents_global_signal_without_sending_one():
    with pytest.raises(safety.UnsafeTestCommand):
        sys.audit("os.kill", -1, 15)
    with pytest.raises(safety.UnsafeTestCommand):
        sys.audit("os.killpg", 0, 15)


def test_guard_covers_exec_and_spawn_audit_boundaries_without_executing_them():
    for event in ("os.exec", "os.posix_spawn"):
        with pytest.raises(safety.UnsafeTestCommand):
            sys.audit(event, "pkill", ["pkill", "-f", "fixture"], {})
    with pytest.raises(safety.UnsafeTestCommand):
        sys.audit("os.spawn", 0, "pkill", ["pkill", "-f", "fixture"], {})


def test_guard_is_active_during_conftest_import_before_sessionstart(tmp_path):
    from plugin_test_containment import isolated_environment

    tools = Path(__file__).resolve().parent
    sentinel = tmp_path / "executed"
    (tmp_path / "kill-server").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    )
    (tmp_path / "conftest.py").write_text(
        "import subprocess, sys\n"
        "command = ['psmux', 'kill-server']\n"
        "subprocess.run(command, executable=sys.executable, check=True)\n"
    )
    (tmp_path / "test_noop.py").write_text("def test_noop(): pass\n")
    environment = isolated_environment(os.environ, tmp_path / "sandbox")
    environment["PYTHONPATH"] = str(tools)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_portfolio_guard"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    assert "UnsafeTestCommand" in result.stdout + result.stderr
    assert not sentinel.exists()


def test_native_startup_failure_keeps_bounded_diagnostics_without_native_execution(tmp_path):
    import ast
    import json
    import re

    source = Path(__file__).resolve().parents[1] / "worktree-manager" / "tests" / "native" / "test_warm_picker_lifetime.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"_read", "_startup_diagnostics"}
    ]
    namespace = {"json": json, "re": re, "subprocess": subprocess}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    (tmp_path / "owner.log").write_text("l" * 16000)
    session = "picker-" + "a" * 24
    (tmp_path / "owner-debug.json").write_text(json.dumps({
        "states": [{"slots": [{"session": session}, {"session": "foreign-session"}]}],
    }))
    calls = []

    def command(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "p" * 16000, "")

    report = json.loads(namespace["_startup_diagnostics"](tmp_path, command))
    assert report["owner_log_tail"] == "l" * 4096
    assert report["panes"][0]["tail"] == "p" * 4096
    assert calls == [(("capture-pane", "-p", "-t", session), {"timeout": 0.5})]
