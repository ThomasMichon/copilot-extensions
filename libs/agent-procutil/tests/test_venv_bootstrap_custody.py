"""Distinct Windows venv redirectors and pre-qualification pipe custody."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import sys
import venv

import pytest

from agent_procutil.owned_process import run_caller_owned


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows venv redirector custody")
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agent_procutil"


@pytest.fixture(params=["powershell", "pwsh"])
def native_shell(request: pytest.FixtureRequest) -> Path:
    if request.param == "powershell":
        return Path(os.environ["SystemRoot"]) / r"System32\WindowsPowerShell\v1.0\powershell.exe"
    shell = shutil.which("pwsh")
    assert shell, "the admitted matrix requires native PowerShell 7"
    return Path(shell)


@pytest.fixture
def selected_python(tmp_path: Path) -> Path:
    root = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(root)
    selected = root / "Scripts" / "python.exe"
    assert selected.is_file()
    return selected


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


def _redirector_probe(selected: Path) -> str:
    program = "import os,sys;print(os.getpid(),flush=True);sys.stdin.readline()"
    encoded = base64.b64encode(program.encode()).decode("ascii")
    arguments = '-I -S -c "import base64;exec(base64.b64decode(' + repr(encoded) + '))"'
    return (
        "$probe=[Diagnostics.Process]::new();$probeChild=$null;try{"
        "$info=[Diagnostics.ProcessStartInfo]::new();"
        f"$info.FileName={_quoted(selected)};$info.Arguments={_quoted(arguments)};"
        "$info.UseShellExecute=$false;$info.CreateNoWindow=$true;"
        "$info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;"
        "$info.RedirectStandardError=$true;$info.EnvironmentVariables.Remove('__PYVENV_LAUNCHER__');"
        "$probe.StartInfo=$info;$null=$probe.Start();$null=$probe.Handle;"
        "$line=$probe.StandardOutput.ReadLineAsync();"
        "if(-not $line.Wait(3000)){throw 'redirector fixture did not publish its interpreter'};"
        "$probeChild=[Diagnostics.Process]::GetProcessById([int]$line.Result);$null=$probeChild.Handle;"
        "if($probe.Id -eq $probeChild.Id -or $probe.HasExited -or $probeChild.HasExited){"
        "throw 'fixture requires distinct live launcher and interpreter identities'};"
        "$probe.StandardInput.AutoFlush=$true;$probe.StandardInput.WriteLine('release');"
        "if(-not $probeChild.WaitForExit(3000) -or -not $probe.WaitForExit(3000)){"
        "throw 'redirector fixture did not settle normally'};"
        "}finally{foreach($stream in @($probe.StandardInput,$probe.StandardOutput,$probe.StandardError)){"
        "if($null -ne $stream){$stream.Dispose()}};"
        "if($null -ne $probeChild){$probeChild.Dispose()};$probe.Dispose()};"
    )


@pytest.mark.parametrize("failure", ["cancel", "stdout", "stderr"])
def test_direct_venv_bootstrap_owns_the_actual_interpreter_before_qualification(
    tmp_path: Path, native_shell: Path, selected_python: Path, failure: str,
) -> None:
    marker, release = tmp_path / "actual.json", tmp_path / "release"
    helper = tmp_path / "daemon_instances.ps1"
    source = (PACKAGE / helper.name).read_text(encoding="utf-8")
    anchor = "import base64,json,sys\n"
    assert source.count(anchor) == 1
    noise = "" if failure == "cancel" else (
        f"for _ in range(512): os.write({1 if failure == 'stdout' else 2},b'x'*4096)\n"
    )
    source = source.replace(
        anchor, anchor + "import os,pathlib,time\n"
        + f"pending=pathlib.Path({str(marker.with_suffix('.pending'))!r})\n"
        + "pending.write_text(json.dumps({'pid':os.getpid(),'executable':sys.executable}))\n"
        + f"pending.replace({str(marker)!r})\n"
        + f"release=pathlib.Path({str(release)!r})\n"
        + "while not release.exists(): time.sleep(.01)\n"
        + noise + "time.sleep(25)\n",
    )
    helper.write_text(source, encoding="utf-8")
    trigger = (
        "$runner.Stop();"
        if failure == "cancel" else
        f"[IO.File]::WriteAllText({_quoted(release)},'release');"
        "while(-not $pending.IsCompleted){if($clock.Elapsed.TotalSeconds -gt 8){"
        "throw 'venv overflow did not stop the original interpreter'};Start-Sleep -Milliseconds 10};"
        "$errorText=$null;try{$null=$runner.EndInvoke($pending)}catch{$errorText=$_.Exception.Message};"
        f"if(-not $errorText -or -not $errorText.Contains('helper {failure} exceeded')){{"
        "throw ('missing venv overflow refusal: '+$errorText)};"
    )
    body = (
        "$ErrorActionPreference='Stop';" + _redirector_probe(selected_python)
        + "$shared=[hashtable]::Synchronized(@{});$runner=[PowerShell]::Create();$pin=$null;try{"
        "$script={param($helper,$archive,$selected,$shared)"
        "$ErrorActionPreference='Stop';. $helper;$script:DaemonInstanceRuntime=$archive;"
        "$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON=$selected;"
        "$originalStart=(Get-Command Start-DaemonInstanceWorker).ScriptBlock;"
        "function Start-DaemonInstanceWorker {param($State,[switch]$NoTimeout)"
        "$shared.State=$State;& $originalStart $State -NoTimeout:$NoTimeout};"
        "try{Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{timeout_ms=$null}}}"
        "catch{$shared.Failure=$_.Exception;throw}};"
        f"$null=$runner.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(PACKAGE / 'daemon_instance_runtime.pyz')})"
        f".AddArgument({_quoted(selected_python)}).AddArgument($shared);"
        "$pending=$runner.BeginInvoke();$clock=[Diagnostics.Stopwatch]::StartNew();"
        f"while(-not [IO.File]::Exists({_quoted(marker)})){{"
        "if($pending.IsCompleted){try{$null=$runner.EndInvoke($pending)}catch{throw $_.Exception};"
        "throw 'venv bootstrap exited without its identity'};"
        "if($clock.Elapsed.TotalSeconds -gt 12){throw 'venv bootstrap did not start'};"
        "Start-Sleep -Milliseconds 10};"
        f"$actual=Get-Content -Raw -LiteralPath {_quoted(marker)}|ConvertFrom-Json;"
        "if($shared.State.process.Id -ne $actual.pid){throw 'SDK still traversed a venv redirector'};"
        f"if($actual.executable -ine {_quoted(selected_python)}){{throw 'selected venv identity was lost'}};"
        f"if($shared.State.process.StartInfo.FileName -ieq {_quoted(selected_python)}){{"
        "throw 'SDK did not select the physical venv base'};"
        "if($shared.State.offerPermitted -or $null -ne $shared.State.job){throw 'Job was already offered'};"
        "$pin=[Diagnostics.Process]::GetProcessById([int]$actual.pid);$null=$pin.Handle;"
        "if($pin.HasExited){throw 'original interpreter exited before the trigger'};"
        "$clock.Restart();" + trigger
        + "if(-not $pin.WaitForExit(0)){throw 'original interpreter survived SDK cleanup'};"
        "if($clock.Elapsed.TotalSeconds -gt 8){throw 'venv cleanup was not bounded'};"
        "@{distinctProbe=$true;direct=$true;selectionPreserved=$true;exited=$true}"
        "|ConvertTo-Json -Compress"
        "}finally{if($null -ne $pin){$pin.Dispose()};$runner.Dispose()}"
    )
    assert _run(native_shell, body) == {
        "distinctProbe": True, "direct": True, "selectionPreserved": True, "exited": True,
    }


def test_qualification_root_exit_retains_unconfirmed_pipe_custody_without_replay(
    tmp_path: Path, native_shell: Path,
) -> None:
    root_exit, leaf_exit = tmp_path / "root.exit", tmp_path / "leaf.exit"
    leaf_pid, identities = tmp_path / "leaf.pid", tmp_path / "identities.json"
    helper = tmp_path / "daemon_instances.ps1"
    leaf = (
        "import os,pathlib,time;"
        f"pathlib.Path({str(leaf_pid)!r}).write_text(str(os.getpid()));"
        f"release=pathlib.Path({str(leaf_exit)!r});deadline=time.monotonic()+25\n"
        "while not release.exists() and time.monotonic()<deadline: time.sleep(.01)"
    )
    source = (PACKAGE / helper.name).read_text(encoding="utf-8")
    anchor = "import base64,json,sys\n"
    assert source.count(anchor) == 1
    encoded_leaf = base64.b64encode(leaf.encode("utf-8")).decode("ascii")
    source = source.replace(
        anchor, anchor + "import os,pathlib,subprocess,time\n"
        + "child=subprocess.Popen([sys.executable,'-I','-c',"
        + f"base64.b64decode('{encoded_leaf}').decode('utf-8')],"
        "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=subprocess.DEVNULL,creationflags=0x08000000)\n"
        + f"leaf=pathlib.Path({str(leaf_pid)!r})\n"
        + "while not leaf.exists() or not leaf.read_text().isdigit(): time.sleep(.01)\n"
        + f"pending=pathlib.Path({str(identities.with_suffix('.pending'))!r})\n"
        + "pending.write_text(json.dumps([os.getpid(),child.pid,int(leaf.read_text())]))\n"
        + f"pending.replace({str(identities)!r})\n"
        + f"release=pathlib.Path({str(root_exit)!r})\n"
        + "while not release.exists(): time.sleep(.01)\n"
        + "os._exit(7)\n",
    )
    helper.write_text(source, encoding="utf-8")
    body = (
        f"$ErrorActionPreference='Stop';. {_quoted(helper)};"
        "$shared=[hashtable]::Synchronized(@{});$runner=[PowerShell]::Create();"
        "$pins=New-Object 'Collections.Generic.List[Diagnostics.Process]';try{"
        "$script={param($helper,$archive,$python,$shared)"
        "$ErrorActionPreference='Stop';. $helper;$script:DaemonInstanceRuntime=$archive;"
        "$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON=$python;"
        "$originalStart=(Get-Command Start-DaemonInstanceWorker).ScriptBlock;"
        "function Start-DaemonInstanceWorker {param($State,[switch]$NoTimeout)"
        "$shared.State=$State;& $originalStart $State -NoTimeout:$NoTimeout};"
        "try{Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{timeout_ms=$null}}}"
        "catch{$shared.Failure=$_.Exception;throw}};"
        f"$null=$runner.AddScript($script).AddArgument({_quoted(helper)})"
        f".AddArgument({_quoted(PACKAGE / 'daemon_instance_runtime.pyz')})"
        f".AddArgument({_quoted(sys.executable)}).AddArgument($shared);"
        "$pending=$runner.BeginInvoke();$clock=[Diagnostics.Stopwatch]::StartNew();"
        f"while(-not [IO.File]::Exists({_quoted(identities)})){{"
        "if($pending.IsCompleted){try{$null=$runner.EndInvoke($pending)}catch{throw $_.Exception};"
        "throw 'exit fixture stopped without its identities'};"
        "if($clock.Elapsed.TotalSeconds -gt 12){throw 'exit fixture did not start'};"
        "Start-Sleep -Milliseconds 10};"
        f"$ids=Get-Content -Raw -LiteralPath {_quoted(identities)}|ConvertFrom-Json;"
        "foreach($identity in @($ids|Select-Object -Unique)){"
        "$pin=[Diagnostics.Process]::GetProcessById([int]$identity);$null=$pin.Handle;"
        "if($pin.HasExited){throw 'exit fixture identity was not live'};$pins.Add($pin)};"
        f"[IO.File]::WriteAllText({_quoted(root_exit)},'exit');$clock.Restart();"
        "while(-not $pending.IsCompleted){if($clock.Elapsed.TotalSeconds -gt 9){"
        "throw 'qualification waited indefinitely for an inherited pipe'};Start-Sleep -Milliseconds 10};"
        "$failure=$null;try{$null=$runner.EndInvoke($pending)}catch{$failure=$_.Exception};"
        "if($null -eq $failure -or -not $failure.Message.Contains('helper exited before its response')){"
        "throw 'missing original-process exit refusal'};"
        "$reason=$shared.Failure;$retained=$null;"
        "while($null -ne $reason -and $null -eq $retained){"
        "$retained=$reason.Data['DaemonInstanceState'];"
        "if($null -ne $retained){break};"
        "if($reason -is [Management.Automation.RuntimeException] -and "
        "-not [object]::ReferenceEquals($reason.ErrorRecord.Exception,$reason)){"
        "$reason=$reason.ErrorRecord.Exception}else{$reason=$reason.InnerException}};"
        "if(-not [object]::ReferenceEquals($retained,$shared.State)){"
        "throw ('unconfirmed pipe state was not retained: '+$failure.Message+'; outputDone='+"
        "$shared.State.output.Active.IsCompleted)};"
        "if($retained.output.Active.IsCompleted){throw 'pending pipe capture was falsely settled'};"
        "if(@($pins|Where-Object{-not $_.HasExited}).Count -eq 0){throw 'retained-pipe fixture was not live'};"
        f"[IO.File]::WriteAllText({_quoted(leaf_exit)},'exit');"
        "foreach($pin in $pins){if(-not $pin.WaitForExit(3000)){throw 'fixture writer did not settle'}};"
        "Close-DaemonInstanceWorker $retained;"
        "@{bounded=$true;retained=$true;allExitedBeforeOuterCleanup=$true}|ConvertTo-Json -Compress"
        "}finally{foreach($pin in $pins){$pin.Dispose()};$runner.Dispose()}"
    )
    assert _run(native_shell, body) == {
        "bounded": True, "retained": True, "allExitedBeforeOuterCleanup": True,
    }
