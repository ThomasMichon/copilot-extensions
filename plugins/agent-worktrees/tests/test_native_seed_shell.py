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
    bootstrap.write_text(
        "import json,os\n"
        "print(json.dumps({'cwd':os.getcwd(),'argv':['2026-01-01T00:00:00Z',''],"
        "'environment':{'DATE_LITERAL':'2026-01-01T00:00:00Z'}}))\n",
        encoding="utf-8",
    )
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
    reply = json.loads(result.stdout)
    assert Path(reply["cwd"]) == selected
    assert reply["argv"] == ["2026-01-01T00:00:00Z", ""]
    assert reply["environment"] == {"DATE_LITERAL": "2026-01-01T00:00:00Z"}


def test_finite_phase_kills_a_stalled_child_at_the_ten_second_bound(tmp_path):
    started = tmp_path / "started.jsonl"
    bootstrap = tmp_path / "stalled.py"
    bootstrap.write_text(
        "import os,time\nfrom pathlib import Path\n"
        f"with Path({str(started)!r}).open('a',encoding='utf-8') as output:\n"
        "    output.write(str(os.getpid())+'\\n')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "deadline.ps1"
    driver.write_text(
        f". {_literal(SCRIPT)}\n"
        "function Get-AwNativeSeedRuntime { param([string]$Root)\n"
        f"return @{{Python={_literal(Path(sys.executable))};Bootstrap={_literal(bootstrap)}}}\n"
        "}\n"
        "$watch=[Diagnostics.Stopwatch]::StartNew()\n"
        f"$reply=Invoke-AwNativeSeedPhase {_literal(tmp_path)} @('--phase','prepare')\n"
        "$watch.Stop()\n"
        "$stopped=$false\ntry {\n"
        f"  $child=[Diagnostics.Process]::GetProcessById([int]([IO.File]::ReadAllText({_literal(started)}).Trim()))\n"
        "  $stopped=$child.HasExited\n  $child.Dispose()\n"
        "} catch [ArgumentException] { $stopped=$true }\n"
        "@{elapsed=$watch.Elapsed.TotalSeconds;reply=$reply;stopped=$stopped} | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(driver)],
        timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reply = json.loads(result.stdout)
    assert reply["reply"] is None
    assert reply["stopped"] is True
    assert 10 <= reply["elapsed"] < 15
    assert len(started.read_text(encoding="utf-8").splitlines()) == 1
    assert not result.stderr


def _run_real_copied_setup(tmp_path, *, admission, absent_end=False, explicit_exit=True):
    scripts = tmp_path / "launch-owned"
    scripts.mkdir()
    for name in ("default-setup.ps1", "native-seed-boundary.ps1", "reconcile-machine-settings.ps1"):
        shutil.copy2(SCRIPT.with_name(name), scripts / name)
    observed = tmp_path / "consumer-phases.jsonl"
    setup_seed = "$null" if admission else "'fixture'"
    with (scripts / SCRIPT.name).open("a", encoding="utf-8") as fixture:
        # Substitute only finite policy replies; run the whole copied consumer,
        # its parameter binder, real native backend, byte pumps and actual wait.
        fixture.write(
            "\nfunction Invoke-AwNativeSeedPhase {\n"
            "param([string]$Root, [string[]]$Arguments)\n"
            f"[IO.File]::AppendAllText({_literal(observed)}, "
            "((ConvertTo-Json -InputObject @($Arguments) -Compress) + \"`n\"))\n"
            "$phase = $Arguments[1]\n"
            "if ($phase -eq 'setup') {\n"
            "return @{version=1; attempt_id=('a'*32); resolution_id=('b'*32); "
            f"operation_id=('c'*32); parent_id=$null; setup_seed_id={setup_seed}}}\n"
            "}\n"
            "if ($phase -eq 'prepare') {\n"
            "$delimiter = [Array]::IndexOf($Arguments,'--')\n"
            "return @{version=1; argv=@($Arguments[($delimiter+1)..($Arguments.Count-1)]); "
            "attempt_id=('a'*32); resolution_id=('b'*32); operation_id=('d'*32); parent_id=('c'*32)}\n"
            "}\n"
            + ("if ($phase -eq 'returned') { return $null }\n" if absent_end else "")
            + "return @{recorded=$true}\n}\n",
        )
    backend = tmp_path / "backend.ps1"
    captured = tmp_path / "backend-arguments.json"
    backend.write_text(
        "$reply=@{args=@($args); pid=$PID; owner=[int]$env:FIXTURE_SETUP_PID} | ConvertTo-Json -Compress\n"
        f"[IO.File]::WriteAllText({_literal(captured)}, $reply)\n"
        "Write-Output 'backend-returned'\n" + ("exit 7\n" if explicit_exit else ""),
        encoding="utf-8",
    )
    argv = ["", "single ' quote", 'double " quote', "; $literal", "\u03a9", "2026-01-01T00:00:00Z", "--", "--allow-all"]
    envelope = json.dumps({"args": argv})
    driver = tmp_path / "consumer.ps1"
    driver.write_text(
        "$env:FIXTURE_SETUP_PID = [string]$PID\n"
        f"$env:PATH = {_literal(Path(shutil.which('pwsh')).parent)}\n"
        f"& {_literal(scripts / 'default-setup.ps1')} -Recovery "
        f"-LaunchSeedPreparation {'admission' if admission else 'seed'} "
        f"-LaunchSeedNativeRoot {_literal(tmp_path / 'engine')} -LaunchSeedRecord {_literal(tmp_path / 'wt-a.yaml')} "
        f"-CopilotPath {_literal(backend)} -RuntimePython {_literal(tmp_path / 'absent-runtime.exe')} "
        f"-NativeArgumentsJson '{envelope.replace(chr(39), chr(39)*2)}'\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(driver)],
        timeout=30, max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024, cwd=tmp_path,
    )
    expected_exit = 7 if explicit_exit else 0
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == "backend-returned"
    backend_reply = json.loads(captured.read_text(encoding="utf-8"))
    assert backend_reply["args"] == argv
    assert (backend_reply["pid"] == backend_reply["owner"]) is admission
    events = [json.loads(line) for line in observed.read_text(encoding="utf-8").splitlines()]
    assert [event[1] for event in events] == (
        ["setup", "returned"] if admission else ["setup", "prepare", "spawned", "returned", "returned"]
    )
    if not admission:
        prepare = events[1]
        assert prepare[prepare.index("--launch-parent-id") + 1] == "c" * 32
        child_return, setup_return = events[-2:]
        assert child_return[child_return.index("--operation-id") + 1] == "d" * 32
        assert setup_return[setup_return.index("--operation-id") + 1] == "c" * 32
    assert events[-1][events[-1].index("--exit-code") + 1] == str(expected_exit)
    assert not result.stderr


@pytest.mark.parametrize("admission", [False, True])
@pytest.mark.parametrize("absent_end", [False, True])
def test_real_copied_setup_uses_existing_owner_and_finite_child_boundaries(tmp_path, admission, absent_end):
    _run_real_copied_setup(tmp_path, admission=admission, absent_end=absent_end)


def test_real_copied_setup_records_normal_script_return_without_explicit_exit(tmp_path):
    _run_real_copied_setup(tmp_path, admission=True, explicit_exit=False)
