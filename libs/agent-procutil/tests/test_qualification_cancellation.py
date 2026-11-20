"""Bounded helper transport and original native custody on failure/cancellation."""

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
        "function Wait-DaemonInstanceTask {param($Task,$Clock,$Seconds,$State)"
        "$shared.Waiting=$true;& $originalWait $Task $Clock $Seconds $State};"
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


@pytest.mark.parametrize(
    ("phase", "stream"),
    [("qualification", "stdout"), ("qualification", "stderr"), ("active", "stderr")],
)
def test_noisy_helper_streams_are_bounded_and_retired(
    tmp_path: Path, native_shell: Path, phase: str, stream: str,
) -> None:
    marker, release, attempts = (
        tmp_path / "noise.pid", tmp_path / "noise.release", tmp_path / "noise.attempts",
    )
    helper = tmp_path / "daemon_instances.ps1"
    source = (PACKAGE / helper.name).read_text(encoding="utf-8")
    anchor = "import base64,json,sys\n"
    assert source.count(anchor) == 1
    noise = (
        "import os,pathlib,time\n"
        + f"pathlib.Path({str(attempts)!r}).open('a').write(str(os.getpid())+'\\n')\n"
        + f"pending=pathlib.Path({str(marker.with_suffix('.pending'))!r})\n"
        + "pending.write_text(str(os.getpid()))\n"
        + f"pending.replace({str(marker)!r})\n"
        + f"release=pathlib.Path({str(release)!r})\n"
        + "while not release.exists(): time.sleep(.01)\n"
        + f"for _ in range(512): os.write({1 if stream == 'stdout' else 2},b'fixture-noise-'+b'x'*4082)\n"
        + "time.sleep(25)\n"
    )
    if phase == "qualification":
        source = source.replace(anchor, anchor + noise)
    else:
        dispatch = "from agent_procutil._daemon_instance_shell import main\n"
        assert source.count(dispatch) == 1
        source = source.replace(
            dispatch, dispatch
            + "from agent_procutil._daemon_instance_shell import ShellInstanceSession\n"
            + "def noisy_dispatch(self,request):\n"
            + "".join("    " + line + "\n" for line in noise.splitlines())
            + "ShellInstanceSession.dispatch=noisy_dispatch\n",
        )
    helper.write_text(source, encoding="utf-8")
    phase_guard = (
        "if($shared.State.offerPermitted -or $null -ne $shared.State.job){"
        "throw 'qualification already offered Job custody'};"
        if phase == "qualification" else
        "if(-not $shared.State.offerPermitted -or $null -eq $shared.State.job){"
        "throw 'active helper did not adopt its original Job'};"
    )
    cleanup_guard = (
        "if(-not $shared.State.job.IsClosed){throw 'original noisy Job custody did not close'};"
        if phase == "active" else ""
    )
    body = (
        "$ErrorActionPreference='Stop';"
        "$shared=[hashtable]::Synchronized(@{});$runner=[PowerShell]::Create();"
        "$pins=New-Object 'Collections.Generic.List[Diagnostics.Process]';try{"
        "$script={param($helper,$archive,$python,$shared)"
        "$ErrorActionPreference='Stop';. $helper;$script:DaemonInstanceRuntime=$archive;"
        "$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON=$python;"
        "$originalStart=(Get-Command Start-DaemonInstanceWorker).ScriptBlock;"
        "function Start-DaemonInstanceWorker {param($State,[switch]$NoTimeout)"
        "$shared.State=$State;& $originalStart $State -NoTimeout:$NoTimeout};"
        "$originalWait=(Get-Command Wait-DaemonInstanceTask).ScriptBlock;"
        "function Wait-DaemonInstanceTask {param($Task,$Clock,$Seconds,$State)"
        "$shared.Waiting=$true;& $originalWait $Task $Clock $Seconds $State};"
        "Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{timeout_ms=$null}}};"
        f"$null=$runner.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(PACKAGE / 'daemon_instance_runtime.pyz')})"
        f".AddArgument({_quoted(sys.executable)}).AddArgument($shared);"
        "$pending=$runner.BeginInvoke();$clock=[Diagnostics.Stopwatch]::StartNew();"
        f"while(-not $shared.Waiting -or -not [IO.File]::Exists({_quoted(marker)})){{"
        "if($pending.IsCompleted -or $clock.Elapsed.TotalSeconds -gt 12){"
        "throw ('noisy qualification fixture did not start: '+$runner.Streams.Error.ToString())};"
        "Start-Sleep -Milliseconds 10};"
        f"{phase_guard}"
        f"$actual=[int](Get-Content -Raw -LiteralPath {_quoted(marker)});"
        "$ids=@($shared.State.process.Id,$actual);"
        "if($null -ne $shared.State.worker){$ids+=@($shared.State.worker.Id)};"
        "foreach($identity in @($ids|Select-Object -Unique)){"
        "$pin=[Diagnostics.Process]::GetProcessById([int]$identity);$null=$pin.Handle;"
        "if($pin.HasExited){throw 'original noisy process exited before release'};$pins.Add($pin)};"
        f"[IO.File]::WriteAllText({_quoted(release)},'release');$clock.Restart();"
        "while(-not $pending.IsCompleted){"
        "if($clock.Elapsed.TotalSeconds -gt 8){throw 'noisy helper did not fail within its capture bound'};"
        "Start-Sleep -Milliseconds 10};"
        "$failure=$null;try{$null=$runner.EndInvoke($pending)}catch{$failure=$_.Exception.Message};"
        f"if(-not $failure -or -not $failure.Contains('helper {stream} exceeded')){{"
        "throw ('missing bounded overflow report: '+$failure)};"
        "if($failure.Contains('fixture-noise')){throw 'overflow error disclosed captured content'};"
        "foreach($pin in $pins){if(-not $pin.WaitForExit(0)){"
        "throw 'original noisy identity survived the reported failure'}};"
        f"{cleanup_guard}"
        f"if(@(Get-Content -LiteralPath {_quoted(attempts)}).Count -ne 1){{throw 'noisy selection was replayed'}};"
        "$out=$shared.State.output.Active;$err=$shared.State.diagnostics;"
        "if($out.Builder.Length -gt $out.Limit -or $err.Builder.Length -gt $err.Limit){"
        "throw 'capture retained content beyond its memory bound'};"
        # All witnesses precede runspace disposal and the outer Python Job's cleanup.
        "@{overflow=$true;exited=$true;attempts=1;"
        "stdoutCharacters=$out.Builder.Length;stderrCharacters=$err.Builder.Length;"
        "stdoutBuffer=$shared.State.output.Buffer.Length;stderrBuffer=$shared.State.error.Buffer.Length}"
        "|ConvertTo-Json -Compress"
        "}finally{foreach($pin in $pins){$pin.Dispose()};$runner.Dispose()}"
    )
    outcome = _run(native_shell, body)
    assert isinstance(outcome, dict)
    assert outcome["overflow"] and outcome["exited"] and outcome["attempts"] == 1
    assert 0 <= outcome["stdoutCharacters"] <= (1024 if phase == "qualification" else 524288)
    assert 0 <= outcome["stderrCharacters"] <= 65536
    assert outcome["stdoutBuffer"] == outcome["stderrBuffer"] == 4096


def test_bounded_line_capture_preserves_split_crlf_and_final_eof(native_shell: Path) -> None:
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(PACKAGE / 'daemon_instances.ps1')};"
        "$text=('x'*4095)+\"`r`nlast\";$memory=[IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes($text));"
        "$raw=[IO.StreamReader]::new($memory);$reader=New-DaemonInstanceReader $raw 'stdout' 10000;"
        "try{$one=$reader.ReadLineAsync();if(-not $one.Wait(1000)){throw 'first line stalled'};"
        "$two=$reader.ReadLineAsync();if(-not $two.Wait(1000)){throw 'last line stalled'};"
        "$three=$reader.ReadLineAsync();if(-not $three.Wait(1000)){throw 'EOF stalled'};"
        "@{length=$one.Result.Length;last=$two.Result;eof=($null -eq $three.Result)}|ConvertTo-Json -Compress"
        "}finally{$reader.Dispose()}"
    )
    assert _run(native_shell, body) == {"length": 4095, "last": "last", "eof": True}


def test_refusal_exit_grace_keeps_draining_allowed_diagnostics(
    tmp_path: Path, native_shell: Path,
) -> None:
    helper, release = tmp_path / "daemon_instances.ps1", tmp_path / "diagnostics.release"
    source = (PACKAGE / helper.name).read_text(encoding="utf-8")
    anchor = "import base64,json,sys\n"
    token = "    print('daemon-python-unsupported',flush=True)\n"
    assert source.count(anchor) == source.count(token) == 1
    source = source.replace(anchor, anchor + "sys.version_info=(3,9)\n")
    source = source.replace(
        token, token + "    import os,pathlib,time\n"
        + f"    release=pathlib.Path({str(release)!r})\n"
        + "    while not release.exists(): time.sleep(.01)\n"
        + "    os.write(2,b'x'*48000)\n",
    )
    helper.write_text(source, encoding="utf-8")
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(helper)};"
        f"$script:DaemonInstanceRuntime={_quoted(PACKAGE / 'daemon_instance_runtime.pyz')};"
        f"$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON={_quoted(sys.executable)};"
        "$originalExit=(Get-Command Wait-DaemonInstanceProcessExit).ScriptBlock;"
        "function Wait-DaemonInstanceProcessExit {param($Process,$Clock,$Seconds,$State)"
        f"[IO.File]::WriteAllText({_quoted(release)},'release');"
        "& $originalExit $Process $Clock $Seconds $State};"
        "$state=@{process=$null;worker=$null;job=$null;input=$null;output=$null;error=$null;"
        "diagnostics=$null;nonce=$null;sequence=0;broken=$true;offerPermitted=$false};"
        "$failure=$null;try{Start-DaemonInstanceWorker $state -NoTimeout}catch{$failure=$_.Exception.Message};"
        "$exited=$state.process.HasExited;$characters=$state.diagnostics.Builder.Length;"
        "$cleanup=$false;try{Close-DaemonInstanceWorker $state}catch{$cleanup=$true};"
        "@{exited=$exited;characters=$characters;cleanupReported=$cleanup;"
        "prerequisite=($failure -like '*working Python 3.10+*')}|ConvertTo-Json -Compress"
    )
    outcome = _run(native_shell, body)
    assert isinstance(outcome, dict)
    assert outcome["exited"] and outcome["prerequisite"] and outcome["cleanupReported"]
    assert 48000 <= outcome["characters"] <= 65536
