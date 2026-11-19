"""Cancellation before a helper offers native Job custody."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from agent_procutil.owned_process import run_caller_owned


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows pre-qualification custody")
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agent_procutil"


@pytest.fixture(params=["powershell", "pwsh"])
def native_shell(request: pytest.FixtureRequest) -> Path:
    if request.param == "powershell":
        return Path(os.environ["SystemRoot"]) / r"System32\WindowsPowerShell\v1.0\powershell.exe"
    shell = shutil.which("pwsh")
    assert shell, "the admitted matrix requires native PowerShell 7"
    return Path(shell)


def _quoted(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(native_shell: Path, body: str) -> object:
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    result = run_caller_owned(
        [str(native_shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        timeout=45, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0, result.stderr
    assert isinstance(result.stdout, str)
    return json.loads(result.stdout)


@pytest.mark.parametrize("interpreter", ["selected", "base"])
def test_cancellation_before_qualification_retires_original_pinned_processes(
    tmp_path: Path, native_shell: Path, interpreter: str,
) -> None:
    base = getattr(sys, "_base_executable", sys.executable)
    assert isinstance(base, str)
    python = sys.executable if interpreter == "selected" else base
    marker, attempts = tmp_path / "qualification.pid", tmp_path / "attempts.txt"
    helper = tmp_path / "daemon_instances.ps1"
    source = (PACKAGE / helper.name).read_text(encoding="utf-8")
    anchor = "import base64,json,sys\n"
    assert source.count(anchor) == 1
    source = source.replace(
        anchor, anchor
        + "import os,pathlib,time\n"
        + f"pathlib.Path({str(attempts)!r}).open('a').write(str(os.getpid())+'\\n')\n"
        + f"pending=pathlib.Path({str(marker.with_suffix('.pending'))!r})\n"
        + "pending.write_text(str(os.getpid()))\n"
        + f"pending.replace({str(marker)!r})\n"
        + "time.sleep(25)\n",
    )
    helper.write_text(source, encoding="utf-8")
    body = (
        "$ErrorActionPreference='Stop';"
        "$shared=[hashtable]::Synchronized(@{});$runner=[PowerShell]::Create();"
        "$pins=New-Object 'Collections.Generic.List[Diagnostics.Process]';try{"
        "$script={param($helper,$archive,$python,$shared)"
        "$ErrorActionPreference='Stop';. $helper;"
        "$script:DaemonInstanceRuntime=$archive;"
        "$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON=$python;"
        "$originalStart=(Get-Command Start-DaemonInstanceWorker).ScriptBlock;"
        "function Start-DaemonInstanceWorker {param($State,[switch]$NoTimeout)"
        "$shared.State=$State;& $originalStart $State -NoTimeout:$NoTimeout};"
        "$originalWait=(Get-Command Wait-DaemonInstanceTask).ScriptBlock;"
        "function Wait-DaemonInstanceTask {param($Task,$Clock,$Seconds)"
        "$shared.Waiting=$true;& $originalWait $Task $Clock $Seconds};"
        "Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{timeout_ms=$null}}};"
        f"$null=$runner.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(PACKAGE / 'daemon_instance_runtime.pyz')})"
        f".AddArgument({_quoted(python)}).AddArgument($shared);"
        "$pending=$runner.BeginInvoke();$clock=[Diagnostics.Stopwatch]::StartNew();"
        f"while(-not $shared.Waiting -or -not [IO.File]::Exists({_quoted(marker)})){{"
        "if($pending.IsCompleted -or $clock.Elapsed.TotalSeconds -gt 12){"
        "throw ('qualification fixture did not start: '+$runner.Streams.Error.ToString())};"
        "Start-Sleep -Milliseconds 10};"
        "if($shared.State.offerPermitted -or $null -ne $shared.State.job -or "
        "$null -ne $shared.State.worker){throw 'a Job was already offered'};"
        f"$actual=[int](Get-Content -Raw -LiteralPath {_quoted(marker)});"
        "$ids=@($shared.State.process.Id,$actual);"
        "foreach($identity in @($ids|Select-Object -Unique)){"
        "$pin=[Diagnostics.Process]::GetProcessById([int]$identity);$null=$pin.Handle;"
        "if($pin.HasExited){throw 'original interpreter exited before cancellation'};"
        "$pins.Add($pin)};"
        "$clock.Restart();$runner.Stop();"
        "foreach($pin in $pins){$remaining=[Math]::Max(0,3000-$clock.ElapsedMilliseconds);"
        "if(-not $pin.WaitForExit([int]$remaining)){"
        "throw 'original pre-qualification interpreter survived cancellation'}};"
        "if($clock.Elapsed.TotalSeconds -gt 6){throw 'pre-qualification cancellation was not bounded'};"
        "if($runner.InvocationStateInfo.State -ne 'Stopped' -or -not $shared.State.broken){"
        "throw 'the original request was not cancelled'};"
        f"if(@(Get-Content -LiteralPath {_quoted(attempts)}).Count -ne 1){{throw 'qualification was replayed'}};"
        # Exit witnesses are collected while the host, outer Job and runspace
        # object are still live. Their disposal cannot rescue this assertion.
        "@{cancelled=$true;exited=$true;jobOffered=$shared.State.offerPermitted;"
        "pinned=$pins.Count;attempts=1}|ConvertTo-Json -Compress"
        "}finally{foreach($pin in $pins){$pin.Dispose()};$runner.Dispose()}"
    )
    outcome = _run(native_shell, body)
    assert isinstance(outcome, dict)
    assert outcome == {
        "cancelled": True, "exited": True, "jobOffered": False,
        "pinned": outcome["pinned"], "attempts": 1,
    }
    assert outcome["pinned"] >= 1


def test_unconfirmed_pre_offer_cleanup_retains_the_original_state(native_shell: Path) -> None:
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(PACKAGE / 'daemon_instances.ps1')};"
        "$process=[pscustomobject]@{HasExited=$false;disposed=$false;killed=0};"
        "$process|Add-Member ScriptMethod Kill {$this.killed++;throw 'fixture kill refused'};"
        "$process|Add-Member ScriptMethod WaitForExit {param($timeout);return $false};"
        "$process|Add-Member ScriptMethod Dispose {$this.disposed=$true};"
        "function Start-DaemonInstanceWorker {param($State,[switch]$NoTimeout)"
        "$State.process=$process;throw 'fixture qualification failed'};"
        "$failure=$null;try{"
        "Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{timeout_ms=$null}}"
        "}catch{$failure=$_.Exception};"
        "if($null -eq $failure){throw 'unconfirmed cleanup was reported as success'};"
        "$retained=$failure.Data['DaemonInstanceState'];"
        "@{retained=([object]::ReferenceEquals($retained.process,$process));"
        "disposed=$process.disposed;killed=$process.killed;"
        "originalError=$failure.Message.Contains('fixture qualification failed');"
        "unconfirmed=$failure.Message.Contains('unconfirmed')}"
        "|ConvertTo-Json -Compress"
    )
    assert _run(native_shell, body) == {
        "retained": True, "disposed": False, "killed": 1,
        "originalError": True, "unconfirmed": True,
    }
