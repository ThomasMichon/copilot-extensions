"""Windows native backend execution, distinct from production caller activation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from agent_procutil.owned_process import run_caller_owned

pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell required"),
]

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native-seed-boundary.ps1"


def _literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(
    tmp_path, *, child_exit=7, missing=False, uncertain=False, absent_end=False,
    stdin=None, child_stderr="", location=None, failed_sink=None,
):
    observed = tmp_path / "observed.jsonl"
    payload = tmp_path / "prepared.json"
    admitted = tmp_path / "admitted"
    argv = ["", "single ' quote", 'double " quote', "; $literal", "\u03a9", "--", "--allow-all"]
    command = [
        sys.executable, "-I", "-X", "utf8", "-c",
        "import json,sys,time,pathlib,os\n"
        f"while not pathlib.Path({str(admitted)!r}).exists(): time.sleep(0.01)\n"
        + (f"sys.{failed_sink}.write('x' * (2 * 1024 * 1024))\n" if failed_sink else "")
        +
        "print(json.dumps(sys.argv[1:],ensure_ascii=False))\n"
        + ("print(json.dumps(sys.stdin.read(),ensure_ascii=False))\n" if stdin is not None else "")
        + ("print(json.dumps(os.getcwd()))\n" if location is not None else "")
        + f"sys.stderr.write({child_stderr!r})\n"
        f"sys.exit({child_exit})",
        *argv,
    ]
    if missing:
        command = [str(tmp_path / "missing-backend.exe")]
    origin = tmp_path / "original-launcher"
    origin.mkdir()
    if location is not None:
        location.mkdir()
    payload.write_text(json.dumps({
        "version": 1, "argv": command, "environment": None,
        "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": None,
    }), encoding="utf-8")
    driver = tmp_path / "driver.ps1"
    driver.write_text(
        f". {_literal(SCRIPT)}\n"
        # Substitute only the finite provider; the actual native child path remains real.
        "function Invoke-AwNativeSeedPhase {\n"
        "  param([string]$Root, [string[]]$Arguments)\n"
        "  $phase = $Arguments[1]\n"
        f"  [IO.File]::AppendAllText({_literal(observed)}, "
        "((ConvertTo-Json -InputObject @($Arguments) -Compress) + \"`n\"))\n"
        f"  if ($phase -eq 'prepare') {{ return (Get-Content -Raw {_literal(payload)} | ConvertFrom-Json) }}\n"
        "  if ($phase -eq 'spawned') {\n"
        "    $childPid = [int]$Arguments[3]\n"
        "    $birth = [Diagnostics.Process]::GetProcessById($childPid).StartTime.ToUniversalTime().ToFileTimeUtc()\n"
        "    if ([string]$birth -ne $Arguments[5]) { throw 'native child birth mismatch' }\n"
        f"    [IO.File]::WriteAllText({_literal(admitted)}, 'ready')\n"
        + ("    return $null\n" if uncertain else "")
        + "  }\n"
        + ("  if ($phase -eq 'returned') { return $null }\n" if absent_end else "")
        + "  return @{ recorded = $true }\n"
        "}\n"
        + (f"Set-Location -LiteralPath {_literal(location)}\n" if location is not None else "")
        + f"[Environment]::CurrentDirectory = {_literal(origin)}\n"
        +
        f"$code = Invoke-AwNativeSeedBackend -Root {_literal(tmp_path)} "
        f"-Record {_literal(tmp_path / 'wt-a.yaml')} -SeedId fixture -Command @('copilot')\n"
        "exit $code\n",
        encoding="utf-8",
    )
    executable_script = driver
    if failed_sink:
        executable_script = tmp_path / "close-output-sink.ps1"
        sink = "StandardOutput" if failed_sink == "stdout" else "StandardError"
        captured = "StandardError" if failed_sink == "stdout" else "StandardOutput"
        executable_script.write_text(
            "$info = [Diagnostics.ProcessStartInfo]::new((Get-Process -Id $PID).Path)\n"
            "$info.UseShellExecute = $false\n$info.CreateNoWindow = $true\n"
            "$info.RedirectStandardInput = $true\n"
            "$info.RedirectStandardOutput = $true\n$info.RedirectStandardError = $true\n"
            f"foreach ($argument in @('-NoProfile','-NoLogo','-File',{_literal(driver)})) "
            "{ $info.ArgumentList.Add($argument) }\n"
            "$child = [Diagnostics.Process]::Start($info)\n"
            "$child.StandardInput.Close()\n"
            f"$child.{sink}.Dispose()\n"
            f"$captured = $child.{captured}.ReadToEndAsync()\n"
            "try {\n"
            "  if (-not $child.WaitForExit(20000)) { $child.Kill($true); throw 'sink failure blocked child' }\n"
            "  if (-not $captured.Wait(1000)) { throw 'capture remained open' }\n"
            "  @{ code=$child.ExitCode; captured=$captured.Result } | ConvertTo-Json -Compress\n"
            "} finally { $child.Dispose() }\n",
            encoding="utf-8",
        )
    result = run_caller_owned(
        [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(executable_script)],
        timeout=30, max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
        input_text=stdin, cwd=origin,
    )
    events = [json.loads(line) for line in observed.read_text(encoding="utf-8").splitlines()]
    return result, events, argv


def test_native_backend_preserves_literal_argv_actual_identity_and_exit(tmp_path):
    result, events, argv = _run(tmp_path)
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout.strip()) == argv
    assert [event[1] for event in events] == ["prepare", "spawned", "returned"]
    assert events[-1][events[-1].index("--exit-code") + 1] == "7"
    assert "--receipt-uncertain" not in events[-1]


def test_native_definite_os_spawn_failure_is_the_only_restore_callback(tmp_path):
    result, events, _ = _run(tmp_path, missing=True)
    assert result.returncode == 3
    assert [event[1] for event in events] == ["prepare", "spawn-failed"]
    assert not result.stdout


def test_native_uncertain_spawn_receipt_keeps_wait_and_actual_exit(tmp_path):
    result, events, argv = _run(tmp_path, child_exit=0, uncertain=True)
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout.strip()) == argv
    assert [event[1] for event in events] == ["prepare", "spawned", "returned"]
    assert "--receipt-uncertain" in events[-1]
    assert events[-1][events[-1].index("--exit-code") + 1] == "0"


def test_native_absent_end_handler_preserves_actual_exit_quietly(tmp_path):
    result, events, _ = _run(tmp_path, child_exit=0, absent_end=True)
    assert result.returncode == 0, result.stderr
    assert [event[1] for event in events] == ["prepare", "spawned", "returned"]
    assert not result.stderr


def test_native_consoleless_backend_preserves_all_redirected_standard_streams(tmp_path):
    text = "input ' \" \u03a9\nsecond line\n"
    result, events, argv = _run(tmp_path, child_exit=0, stdin=text, child_stderr="stderr fixture\n")
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [argv, text]
    assert result.stderr == "stderr fixture\n"
    assert [event[1] for event in events] == ["prepare", "spawned", "returned"]


def test_native_backend_uses_powershell_location_not_environment_cwd(tmp_path):
    selected = tmp_path / "selected worktree"
    result, events, argv = _run(tmp_path, child_exit=0, location=selected)
    assert result.returncode == 0, result.stderr
    actual_argv, cwd = [json.loads(line) for line in result.stdout.splitlines()]
    assert actual_argv == argv and Path(cwd) == selected
    assert [event[1] for event in events] == ["prepare", "spawned", "returned"]


@pytest.mark.parametrize("sink", ["stdout", "stderr"])
def test_native_failed_output_sink_is_drained_and_keeps_correlated_exit(tmp_path, sink):
    result, events, _ = _run(tmp_path, child_exit=7, failed_sink=sink)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["code"] == 7
    assert [event[1] for event in events] == ["prepare", "spawned", "returned"]
    assert events[-1][events[-1].index("--exit-code") + 1] == "7"


def test_finite_phase_uses_powershell_location_not_environment_cwd(tmp_path):
    bootstrap = tmp_path / "fixture-bootstrap.py"
    bootstrap.write_text("import json,os\nprint(json.dumps({'cwd':os.getcwd()}))\n", encoding="utf-8")
    selected = tmp_path / "selected worktree"
    selected.mkdir()
    driver = tmp_path / "phase-cwd.ps1"
    driver.write_text(
        f". {_literal(SCRIPT)}\n"
        "function Get-AwNativeSeedRuntime { param([string]$Root)\n"
        f"return @{{ Python={_literal(Path(sys.executable))}; Bootstrap={_literal(bootstrap)} }}\n"
        "}\n"
        f"Set-Location -LiteralPath {_literal(selected)}\n"
        f"[Environment]::CurrentDirectory = {_literal(tmp_path)}\n"
        f"$reply = Invoke-AwNativeSeedPhase {_literal(tmp_path)} @('--phase','prepare')\n"
        "if (-not $reply) { throw 'no finite reply' }\n"
        "$reply | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(driver)],
        timeout=30, max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["cwd"]) == selected
