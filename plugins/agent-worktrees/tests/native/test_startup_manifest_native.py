"""Explicit Windows copied-startup transport qualification, not product uninstall."""

import json
import os
import shutil
from pathlib import Path

import pytest

from agent_procutil.owned_process import run_caller_owned
from agent_worktrees import session_shell
from test_session_shell import launch as launch
from test_startup_manifest import startup as startup

pytestmark = pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None, reason="Native Windows PowerShell required",
)


def environment(runtime):
    return {
        **os.environ,
        "PATH": str(Path(shutil.which("pwsh")).parent),
        "USERPROFILE": str(runtime.with_name("isolated-home")),
        "AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT": str(runtime),
        "AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED": "1",
    }


def run(command, record, runtime):
    return run_caller_owned(
        command, cwd=record.parent, env=environment(runtime), timeout=30,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )


@pytest.mark.parametrize("wrapped", [False, True])
def test_actual_copied_setup_consumes_before_backend_with_no_rooted_process_argv(startup, wrapped):
    record, runtime, prepared = startup
    backend = runtime.with_name("backend.ps1")
    backend.write_text(
        "[Console]::WriteLine((ConvertTo-Json -Depth 4 -Compress -InputObject @{\n"
        " args = @($args); process = [Environment]::GetCommandLineArgs(); pid = $PID\n"
        " manifests = @(Get-ChildItem -LiteralPath $env:TEST_STARTUP_ARGS).Count\n"
        "}))\nexit 7\n",
        encoding="utf-8",
    )
    literal = ["", "two words", "2026-01-01", "single ' quote", 'double " quote', "; $literal", "--"]
    pwsh = shutil.which("pwsh")
    command = [
        pwsh, "-NoProfile", "-NoLogo", "-File", str(prepared.wrapper),
        "-CopilotPath", str(backend), "-RuntimePython", str(runtime / "missing-python.exe"),
        "-NativeArgumentsJson", json.dumps({"args": literal}),
    ]
    if wrapped:
        command = [
            *command[:4], str(prepared.directory / "launch-command.ps1"), "--", *command,
        ]
    compact = session_shell.startup_command(command)
    manifest = Path(compact[-1])
    env = environment(runtime)
    env["TEST_STARTUP_ARGS"] = str(manifest.parent)
    # Startup still has the transient provider values, but not a late shell read.
    shutil.rmtree(runtime)
    result = run_caller_owned(
        compact, cwd=record.parent, env=env, timeout=30,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    assert result.returncode == 7, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed["args"] == literal
    assert "--startup-manifest" in observed["process"]
    assert all(str(runtime).casefold() not in arg.casefold() for arg in observed["process"])
    assert observed["manifests"] == 0
    assert not manifest.exists()
    assert not result.stderr


def test_pane_appended_prompt_and_recovery_keep_same_host_semantics(startup):
    record, runtime, prepared = startup
    prepared.wrapper.write_text(
        "# agent-worktrees:native-setup-owner-v1\n"
        "[CmdletBinding(PositionalBinding=$false)]\n"
        "param([string]$RuntimePython,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
        "[Console]::WriteLine((ConvertTo-Json -Compress -InputObject @{\n"
        "args = @($Rest); process = [Environment]::GetCommandLineArgs()\n"
        "recovery = $env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED\n"
        "}))\nexit 9\n",
        encoding="utf-8",
    )
    pwsh = shutil.which("pwsh")
    command = [
        pwsh, "-NoProfile", "-NoLogo", "-File",
        str(prepared.directory / "launch-command.ps1"), "--recovery", "--",
        pwsh, "-NoProfile", "-NoLogo", "-File", str(prepared.wrapper),
        "-RuntimePython", str(runtime / "missing-python.exe"),
    ]
    compact = session_shell.startup_command(command)
    prompt = "two words; $literal 'single' \"double\"\nnext line"
    result = run([*compact, "--interactive", prompt], record, runtime)
    assert result.returncode == 9, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed["args"] == ["--interactive", prompt]
    assert "--startup-manifest" in observed["process"]
    assert not Path(compact[-1]).exists()
    assert not result.stderr


def test_non_inert_inner_host_options_keep_original_spawn_semantics(startup):
    record, runtime, prepared = startup
    prepared.wrapper.write_text(
        "# agent-worktrees:native-setup-owner-v1\n"
        "param([string]$RuntimePython)\n"
        "[Console]::WriteLine((ConvertTo-Json -Compress -InputObject ([Environment]::GetCommandLineArgs())))\n"
        "exit 11\n",
        encoding="utf-8",
    )
    pwsh = shutil.which("pwsh")
    command = [
        pwsh, "-NoProfile", "-NoLogo", "-File",
        str(prepared.directory / "launch-command.ps1"), "--",
        pwsh, "-NoProfile", "-NoLogo", "-Mta", "-File", str(prepared.wrapper),
        "-RuntimePython", str(runtime / "missing-python.exe"),
    ]
    compact = session_shell.startup_command(command)
    result = run(compact, record, runtime)
    assert result.returncode == 11, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert "-Mta" in observed
    assert "--startup-manifest" not in observed
    assert str(runtime / "missing-python.exe") in observed
    assert not Path(compact[-1]).exists()
    assert not result.stderr


@pytest.mark.parametrize("recovery", [False, True])
def test_direct_setup_retains_reconciliation_policy_with_marker_unset(startup, recovery):
    record, runtime, prepared = startup
    backend = runtime.with_name("backend.ps1")
    backend.write_text("[Console]::WriteLine('backend-started')\nexit 7\n", encoding="utf-8")
    commands = runtime.with_name("commands")
    commands.mkdir()
    calls = runtime.with_name("restore-calls")
    (commands / "agent-machines.ps1").write_text(
        "[IO.File]::AppendAllText($env:TEST_RESTORE_CALLS, 'called' + [Environment]::NewLine)\n"
        "exit 23\n",
        encoding="utf-8",
    )
    command = [
        shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(prepared.wrapper),
        *(["-Recovery"] if recovery else []),
        "-CopilotPath", str(backend), "-RuntimePython", str(runtime / "missing-python.exe"),
    ]
    compact = session_shell.startup_command(command)
    env = environment(runtime)
    env.pop("AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED", None)
    env.update(PATH=str(commands) + os.pathsep + env["PATH"], TEST_RESTORE_CALLS=str(calls))
    baseline_calls = calls.with_name("baseline-restore-calls")
    baseline = run_caller_owned(
        command, cwd=record.parent, env={**env, "TEST_RESTORE_CALLS": str(baseline_calls)}, timeout=30,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    result = run_caller_owned(
        compact, cwd=record.parent, env=env, timeout=30,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    assert result.returncode == baseline.returncode, result.stdout + result.stderr
    assert result.stdout == baseline.stdout
    assert result.stderr == baseline.stderr
    assert (baseline_calls.read_text().splitlines() if baseline_calls.exists() else []) == (
        [] if recovery else ["called"]
    )
    assert (calls.read_text().splitlines() if calls.exists() else []) == ([] if recovery else ["called"])
    assert not Path(compact[-1]).exists()


@pytest.mark.parametrize("invalid", ["version", "float", "wrapper", "array", "value", "empty", "json"])
def test_invalid_manifest_is_consumed_without_backend_or_private_diagnostics(startup, invalid):
    record, runtime, prepared = startup
    wrapper = prepared.directory / "launch-command.ps1"
    manifest = prepared.directory / "invalid.json"
    document = {"version": 1, "wrapper": str(wrapper), "argv": ["private-value"]}
    if invalid == "version":
        document["version"] = 2
    elif invalid == "float":
        document["version"] = 1.5
    elif invalid == "wrapper":
        document["wrapper"] = str(prepared.wrapper)
    elif invalid == "array":
        document["argv"] = "private-value"
    elif invalid == "value":
        document["argv"] = [42]
    elif invalid == "empty":
        document["argv"] = []
    manifest.write_text("private-value" if invalid == "json" else json.dumps(document), encoding="utf-8")
    result = run([
        shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(wrapper),
        "--startup-manifest", str(manifest),
    ], record, runtime)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Could not consume startup argument handoff." in result.stderr
    assert "private-value" not in result.stderr + result.stdout
    assert not manifest.exists()
