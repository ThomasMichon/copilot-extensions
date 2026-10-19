"""Windows supervisor service-generation retirement."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_dispatch import supervisor_processes
from agent_dispatch.supervisor_processes import (
    WindowsProcess,
    parse_windows_process_json,
    retire_windows_supervisor_generations,
    select_supervisor_generation_pids,
)


INSTALL_DIR = r"C:\Users\user\.agent-dispatch"


def _proc(pid, parent, executable, command_line):
    return WindowsProcess(
        pid=pid,
        parent_pid=parent,
        executable=executable,
        command_line=command_line,
    )


def _prefixed_processes():
    python = INSTALL_DIR + r"\versions\1\Scripts\python.exe"
    spec = INSTALL_DIR + r"\run\supervisor\scope\producer.json"
    processes = [
        _proc(70111, 1, python, rf'"{python}" -m agent_dispatch --shared supervise serve'),
        _proc(70113, 1, python, rf'"{python}" -m agent_dispatch --local supervise'),
        _proc(70115, 1, python, rf'"{python}" -m agent_dispatch --local schedule serve "{spec}"'),
        _proc(70117, 1, python, rf'"{python}" -m agent_dispatch --url "http://writer/path with spaces" --token "supervise" --control-token=value webhook --config "{spec}"'),
        _proc(70119, 70111, r"C:\Tools\child.exe", "owned-child"),
        _proc(70121, 1, python, rf'"{python}" -m agent_dispatch --local emitter serve external.json'),
        _proc(70123, 1, r"C:\foreign\python.exe", 'python -m agent_dispatch --shared supervise serve'),
        _proc(70125, 1, python, rf'"{python}" -m agent_dispatch --url supervise emitter serve external.json'),
        _proc(70127, 1, python, rf'"{python}" -m agent_dispatch --unknown supervise serve'),
        _proc(70129, 1, python, rf'"{python}" -m agent_dispatch --token'),
        _proc(70131, 1, python, rf'"{python}" -m agent_dispatch --url "{spec}" emitter serve external.json'),
        _proc(70133, 1, python, rf'"{python}" -m agent_dispatch --local emitter serve "{INSTALL_DIR}\run\supervisor\..\outside.json"'),
        _proc(70135, 1, python, rf'"{python}" -m agent_dispatch --shared webhook --config "{spec}" --config external.json'),
        _proc(70137, 1, python, rf'"{python}" -m agent_dispatch --control-token "{spec}" schedule serve external.json'),
        _proc(70139, 1, python, rf'"{python}" -m unrelated --token agent_dispatch --local supervise serve'),
        _proc(70141, 1, python, rf'"{python}" -c "pass" agent_dispatch --shared supervise serve'),
        _proc(70143, 1, python, rf'"{python}" -I -X utf8 -m agent_dispatch --local supervise serve'),
    ]
    if os.name == "nt":
        processes.append(_proc(70145, 1, python, subprocess.list2cmdline([
            python, "-m", "agent_dispatch", "--token", 'quoted "value" with spaces',
            "--local", "supervise", "serve",
        ])))
    return processes


def _prefixed_expected():
    return {70111, 70113, 70115, 70117, 70119, 70143} | ({70145} if os.name == "nt" else set())


def test_target_prefixes_preserve_exact_supervisor_retirement_scope():
    selected = select_supervisor_generation_pids(
        _prefixed_processes(), INSTALL_DIR, run_dir=INSTALL_DIR + r"\run",
    )
    assert set(selected) == _prefixed_expected()
    assert selected.index(70111) < selected.index(70119)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows argv parsing and installer fallback")
def test_native_fallback_matches_prefixed_python_scope_without_stopping_real_processes(tmp_path):
    plugin = Path(__file__).resolve().parents[1]
    text = (plugin / "scripts/install.ps1").read_text()
    start = text.index("function Retire-SupervisorProcessesFallback {")
    function = text[start:text.index("\nfunction ", start + 1)]
    rows = tmp_path / "processes.json"
    rows.write_text(json.dumps([
        {"ProcessId": p.pid, "ParentProcessId": p.parent_pid,
         "ExecutablePath": p.executable, "CommandLine": p.command_line}
        for p in _prefixed_processes()
    ]), encoding="utf-8")
    script = tmp_path / "retire.ps1"
    script.write_text("\n".join([
        "$ErrorActionPreference='Stop'; $env:OS='Windows_NT'",
        f"$InstallDir='{INSTALL_DIR}'; $env:AGENT_DISPATCH_RUN_DIR=Join-Path $InstallDir 'run'",
        f"$script:Rows=@(Get-Content -Raw '{rows}' | ConvertFrom-Json)",
        "$script:Stopped=@()",
        "function Get-CimInstance { param($ClassName,$ErrorAction) $script:Rows }",
        "function Stop-Process { param([int]$Id,[switch]$Force,$ErrorAction) $script:Stopped+=$Id }",
        "function Write-Warn { param($message) throw $message }",
        "function Stop-DispatchProcess { throw 'unexpected legacy fallback' }",
        f". '{plugin / 'scripts/supervisor-lifecycle.ps1'}'",
        function,
        "$count=Retire-SupervisorProcessesFallback",
        f"if($count -ne {len(_prefixed_expected())}){{throw \"unexpected retirement count $count\"}}",
        "[Console]::Out.Write((ConvertTo-Json -InputObject @($script:Stopped) -Compress))",
    ]), encoding="utf-8")
    result = subprocess.run(
        [supervisor_processes.powershell_host(), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=20, **supervisor_processes.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(json.loads(result.stdout)) == _prefixed_expected()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows installer lifecycle health probes")
@pytest.mark.parametrize("local_healthy", [False, True])
def test_installer_health_probes_never_use_opposite_central_health(tmp_path, local_healthy):
    plugin = Path(__file__).resolve().parents[1]
    text = (plugin / "scripts/install.ps1").read_text()
    functions = []
    for name in ("Test-CoordinatorHealthy", "Confirm-CoordinatorRunning"):
        start = text.index(f"function {name} {{")
        functions.append(text[start:text.index("\nfunction ", start + 1)])
    script = tmp_path / "health.ps1"
    script.write_text("\n".join([
        "$ErrorActionPreference='Stop'; $env:AGENT_DISPATCH_TASK_AUTHORITY='central'",
        "$VenvPython='Invoke-FixtureRuntime'; $script:Calls=@(); $script:Ok=@(); $script:Warnings=@()",
        f"$script:LocalHealthy=${str(local_healthy).lower()}",
        "function Test-Path { param($Path) $true }",
        "function Start-Sleep { param($Seconds) }",
        "function Write-Ok { param($message) $script:Ok+=$message }",
        "function Write-Warn { param($message) $script:Warnings+=$message }",
        "function Invoke-FixtureRuntime {",
        "  $script:Calls+=,@($args)",
        "  if($args[0] -eq '-c'){$global:LASTEXITCODE=0; return 'candidate'}",
        "  $local=$args -contains '--local'",
        "  $healthy=if($local){$script:LocalHealthy}else{-not $script:LocalHealthy}",
        "  $global:LASTEXITCODE=if($healthy){0}else{1}",
        "  if($healthy){'{\"version\":\"candidate\"}'}",
        "}",
        *functions,
        "$healthy=Test-CoordinatorHealthy; Confirm-CoordinatorRunning",
        "[Console]::Out.Write((@{healthy=$healthy;ok=$script:Ok;warnings=$script:Warnings;calls=$script:Calls}|ConvertTo-Json -Depth 5 -Compress))",
    ]), encoding="utf-8")
    result = subprocess.run(
        [supervisor_processes.powershell_host(), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=20, **supervisor_processes.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["healthy"] is local_healthy
    probes = [argv for argv in evidence["calls"] if argv[0] == "-m"]
    assert probes and all(argv == ["-m", "agent_dispatch", "--local", "health"] for argv in probes)
    assert bool(evidence["ok"]) is local_healthy
    assert bool(evidence["warnings"]) is not local_healthy


def test_selects_old_wrapper_master_children_and_descendants_across_slots():
    old_python = INSTALL_DIR + r"\versions\0.1.0-dev1\Scripts\python.exe"
    current_python = INSTALL_DIR + r"\versions\0.1.0-dev2\Scripts\python.exe"
    launcher = INSTALL_DIR + r"\supervise-service.ps1"
    processes = [
        _proc(
            100,
            1,
            r"C:\Windows\System32\conhost.exe",
            rf'conhost.exe --headless powershell.exe -File "{launcher}"',
        ),
        _proc(
            101,
            100,
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            rf'powershell.exe -File "{launcher}"',
        ),
        _proc(
            102,
            101,
            old_python,
            rf'"{old_python}" -m agent_dispatch supervise serve --legacy-env',
        ),
        _proc(
            103,
            102,
            old_python,
            rf'"{old_python}" -m agent_dispatch emitter serve "emitter.json"',
        ),
        _proc(
            104,
            102,
            old_python,
            rf'"{old_python}" -m agent_dispatch supervise --all-repos --label queue',
        ),
        # The old master is gone, but the child still carries the materialized
        # supervisor-owned spec path and must be retired.
        _proc(
            200,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch schedule serve '
            rf'"{INSTALL_DIR}\run\supervisor\scope\lane.schedule.json"',
        ),
        # A standalone producer on the current slot is a supported public surface
        # and must not be killed merely because it uses the installed runtime.
        _proc(
            201,
            1,
            current_python,
            rf'"{current_python}" -m agent_dispatch emitter serve "current.json"',
        ),
        # Runtime age alone does not prove ownership: an older-slot standalone
        # producer with an external config remains outside service reconciliation.
        _proc(
            202,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch emitter serve "standalone.json"',
        ),
        # A registered emitter's in-flight external command is a descendant and
        # must not survive the generation that owned it.
        _proc(105, 103, r"C:\Tools\producer.exe", "producer.exe tick"),
        # Unrelated agent-dispatch processes are not supervisor-owned.
        _proc(
            300,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch serve',
        ),
        _proc(
            301,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch emitter tick "emitter.json"',
        ),
        _proc(
            302,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch supervise list',
        ),
    ]

    selected = select_supervisor_generation_pids(
        processes,
        INSTALL_DIR,
        run_dir=INSTALL_DIR + r"\run",
    )

    assert set(selected) == {100, 101, 102, 103, 104, 105, 200}
    assert selected.index(102) < selected.index(103) < selected.index(105)
    assert 300 not in selected
    assert 301 not in selected
    assert 302 not in selected
    assert 201 not in selected
    assert 202 not in selected


def test_parse_windows_process_inventory_accepts_single_or_array_payload():
    single = parse_windows_process_json(
        '{"ProcessId":10,"ParentProcessId":1,'
        '"ExecutablePath":"C:\\\\Python\\\\python.exe","CommandLine":"python -V"}'
    )
    assert single == [
        WindowsProcess(
            pid=10,
            parent_pid=1,
            executable=r"C:\Python\python.exe",
            command_line="python -V",
        )
    ]
    array = parse_windows_process_json(
        '[{"ProcessId":20,"ParentProcessId":2,'
        '"ExecutablePath":"C:\\\\Python\\\\python.exe","CommandLine":"python -V"},'
        '{"ProcessId":21,"ParentProcessId":20,'
        '"ExecutablePath":null,"CommandLine":null}]'
    )
    assert array == [
        WindowsProcess(
            pid=20,
            parent_pid=2,
            executable=r"C:\Python\python.exe",
            command_line="python -V",
        ),
        WindowsProcess(pid=21, parent_pid=20),
    ]


def test_inventory_uses_explicit_utf8_stdout_without_host_formatting(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(supervisor_processes.subprocess, "run", run)
    assert supervisor_processes.iter_windows_processes() == []
    argv, options = calls[0]
    assert options["encoding"] == "utf-8"
    assert "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)" in argv[-1]
    assert "[Console]::Out.Write((ConvertTo-Json -InputObject $rows -Compress))" in argv[-1]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows PowerShell JSON transport")
def test_inventory_transport_preserves_large_unicode_and_control_character_fields(monkeypatch):
    command_line = "synthetic \u03bb\r\n\t\x1b" + ("long-field " * 40000)
    executable = "C:\\synthetic\\\u03bb.exe"
    payload = json.dumps({
        "ProcessId": 42, "ParentProcessId": 7,
        "ExecutablePath": executable, "CommandLine": command_line,
    })
    run = supervisor_processes.subprocess.run

    def synthetic_inventory(argv, **kwargs):
        script = (
            "function Get-CimInstance { "
            "[Console]::In.ReadToEnd() | ConvertFrom-Json }; " + argv[-1]
        )
        return run([*argv[:-1], script], input=payload, **kwargs)

    monkeypatch.setattr(supervisor_processes.subprocess, "run", synthetic_inventory)
    assert supervisor_processes.iter_windows_processes() == [
        WindowsProcess(pid=42, parent_pid=7, executable=executable, command_line=command_line),
    ]


def test_retirement_terminates_every_selected_generation():
    python = INSTALL_DIR + r"\versions\0.1.0-dev1\Scripts\python.exe"
    launcher = INSTALL_DIR + r"\supervise-service.ps1"
    processes = [
        _proc(10, 1, r"C:\Windows\System32\conhost.exe", rf'conhost -File "{launcher}"'),
        _proc(11, 10, python, rf'"{python}" -m agent_dispatch supervise serve'),
        _proc(12, 11, python, rf'"{python}" -m agent_dispatch webhook --config hook.json'),
    ]
    terminated: list[int] = []

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        return True

    result = retire_windows_supervisor_generations(
        INSTALL_DIR,
        list_processes=lambda: processes,
        terminate=terminate,
        platform_name="nt",
    )

    assert result.ok
    assert result.selected == [10, 11, 12]
    assert result.retired == [10, 11, 12]
    assert terminated == [10, 11, 12]


def test_materialized_child_honors_run_dir_override():
    python = INSTALL_DIR + r"\versions\0.1.0-dev1\Scripts\python.exe"
    custom_run = r"D:\agent-dispatch-runtime"
    processes = [
        _proc(
            10,
            1,
            python,
            rf'"{python}" -m agent_dispatch emitter serve '
            rf'"{custom_run}\supervisor\scope\lane.emitter.json"',
        )
    ]

    assert select_supervisor_generation_pids(
        processes,
        INSTALL_DIR,
        run_dir=custom_run,
    ) == [10]
