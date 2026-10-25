"""Fresh private PSMux registries; never control the user's default namespace."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest


PWSH = shutil.which("pwsh")
PSMUX = shutil.which("psmux")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not PWSH or not PSMUX,
    reason="Native Windows PowerShell and PSMux are required",
)

DRIVER = r"""
$ErrorActionPreference='Stop'
$binary=$args[0]; $root=$args[1]; $namespace=$args[2]; $mode=$args[3]
$worktree=Join-Path $root 'checkout with spaces'
$observed=Join-Path $root 'pane-cwd.txt'
$began=[DateTime]::UtcNow
$prior=@{}
foreach($row in @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'psmux.exe' -and
    $_.CommandLine -match '\s(?:server|attach-session)\s'
})){
    $p=Get-Process -Id $row.ProcessId -ErrorAction Stop
    $prior[$p.Id]=$p.StartTime.ToFileTimeUtc()
}
$owned=[Collections.Generic.HashSet[int]]::new()
function Wait-For([scriptblock]$Check,[string]$Message){
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    do {
        $value=& $Check
        if($value){return $value}
        Start-Sleep -Milliseconds 50
    } while([DateTime]::UtcNow -lt $deadline)
    throw $Message
}
function Stop-OwnedTestMux {
    $all=@(Get-CimInstance Win32_Process)
    $roots=@($all | Where-Object {
        $_.Name -eq 'psmux.exe' -and
        $_.ExecutablePath -eq $binary -and
        $_.CreationDate.ToUniversalTime() -ge $began -and
        $_.CommandLine -match [regex]::Escape($namespace)
    })
    $selected=@{}
    foreach($row in $roots){$selected[[int]$row.ProcessId]=$row}
    do {
        $changed=$false
        foreach($row in $all){
            if($selected.ContainsKey([int]$row.ParentProcessId) -and
                -not $selected.ContainsKey([int]$row.ProcessId)){
                $parent=$selected[[int]$row.ParentProcessId]
                if($null -eq $row.CreationDate -or $null -eq $parent.CreationDate -or
                    $row.CreationDate.ToUniversalTime() -lt $parent.CreationDate.ToUniversalTime()){
                    continue
                }
                $selected[[int]$row.ProcessId]=$row
                $changed=$true
            }
        }
    } while($changed)
    $held=@{}
    try {
        foreach($entry in $selected.GetEnumerator()){
            $targetId=[int]$entry.Key
            $p=Get-Process -Id $targetId -ErrorAction SilentlyContinue
            if(-not $p){continue}
            $held[$targetId]=$p
            $null=$p.Handle
            if([Math]::Abs(($p.StartTime.ToUniversalTime() -
                $entry.Value.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1){
                throw 'Test process identity changed'
            }
            if($prior.ContainsKey($targetId)){throw 'Refusing a pre-existing process'}
            $null=$owned.Add($targetId)
        }
        # Stop the owned servers first so their spare pools cannot refill.
        $order=@($roots | ForEach-Object {[int]$_.ProcessId}) +
            @($held.Keys | Where-Object {$_ -notin @($roots.ProcessId)})
        foreach($targetId in $order){
            if($held.ContainsKey($targetId) -and -not $held[$targetId].HasExited){
                try {
                    Stop-Process -Id $targetId -Force -ErrorAction Stop
                } catch {
                    if(-not $held[$targetId].WaitForExit(1000)){throw}
                }
            }
        }
        foreach($p in $held.Values){
            if(-not $p.WaitForExit(5000)){throw 'Owned test process did not exit'}
        }
    } finally {
        foreach($p in $held.Values){$p.Dispose()}
    }
}
$report=$null
try {
    $commandArgs=@('-L',$namespace,'new-session','-d','-s','fixture',
        '-c',$worktree)
    $clock=[Diagnostics.Stopwatch]::StartNew()
    if($mode -eq 'home'){
        Invoke-AwPsmuxFromHome -CommandArgs $commandArgs
    } else {
        & $binary @commandArgs
    }
    $clock.Stop()
    if($LASTEXITCODE -ne 0){throw 'PSMux creation failed'}
    $registry=Join-Path $HOME '.psmux'
    $warmFile=Join-Path $registry ($namespace+'____warm__.port')
    $sessionFile=Join-Path $registry ($namespace+'__fixture.port')
    $null=Wait-For {Test-Path -LiteralPath $sessionFile} 'Real session did not register'
    $null=Wait-For {Test-Path -LiteralPath $warmFile} 'Standby did not register'
    $warmPort=[int]([IO.File]::ReadAllText($warmFile).Trim())
    $realPort=[int]([IO.File]::ReadAllText($sessionFile).Trim())
    $warm=Get-Process -Id (Get-NetTCPConnection -State Listen -LocalPort $warmPort).OwningProcess
    $real=Get-Process -Id (Get-NetTCPConnection -State Listen -LocalPort $realPort).OwningProcess
    $null=$warm.Handle; $null=$real.Handle
    $capture='cd > "'+$observed+'"'
    & $binary -L $namespace send-keys -t fixture -l $capture
    if($LASTEXITCODE -ne 0){throw 'Owned pane directory request failed'}
    & $binary -L $namespace send-keys -t fixture Enter
    $null=Wait-For {
        (Test-Path -LiteralPath $observed) -and (Get-Item -LiteralPath $observed).Length -gt 0
    } 'Pane did not report its directory'
    $pane=@{cwd=[IO.File]::ReadAllText($observed).Trim()}
    $restored=(Get-Location).ProviderPath -eq $worktree
    & $binary -L $namespace send-keys -t fixture exit Enter
    if(-not $real.WaitForExit(2000)){
        & $binary -L $namespace kill-session -t fixture
        if($LASTEXITCODE -ne 0){throw 'Owned real-session close failed'}
    }
    if(-not $real.WaitForExit(5000)){throw 'Real test session did not exit'}
    if($warm.HasExited){throw 'Standby exited before deletion proof'}
    Set-Location -LiteralPath $HOME
    [IO.Directory]::SetCurrentDirectory($HOME)
    $deleted=$false
    try {
        Remove-Item -LiteralPath $worktree -ErrorAction Stop
        $deleted=$true
    } catch [IO.IOException], [UnauthorizedAccessException] {}
    $report=@{deleted=$deleted;restored=$restored;pane=$pane;
        standby_alive=(-not $warm.HasExited);mode=$mode;launch_ms=$clock.ElapsedMilliseconds}
    $warm.Dispose();$real.Dispose()
} finally {
    Stop-OwnedTestMux
}
foreach($entry in $prior.GetEnumerator()){
    $p=Get-Process -Id $entry.Key -ErrorAction Stop
    if($p.StartTime.ToFileTimeUtc() -ne $entry.Value){
        throw 'A pre-existing PSMux process changed'
    }
}
$report.owned=@($owned)
ConvertTo-Json -InputObject $report -Depth 4 -Compress
"""


def test_real_standby_does_not_pin_retired_checkout(tmp_path, capsys):
    from mux_windows_observer import descendants, processes, windows

    source = Path(__file__).resolve().parents[2] / "bin" / "launch-session.ps1"
    text = source.read_text("utf-8")
    start = text.index("function Invoke-AwPsmuxFromHome {")
    end = text.index("\n}\n", start) + 3
    helper = text[start:end]
    results = {}
    for mode in ("legacy", "home"):
        root = tmp_path / mode
        home = root / "home"
        worktree = root / "checkout with spaces"
        registry = home / ".psmux"
        registry.mkdir(parents=True)
        worktree.mkdir()
        (home / ".psmux.conf").write_text(
            'set -g default-shell "cmd.exe /D /Q"\nset -g warm on\n',
            encoding="utf-8",
        )
        script = root / "driver.ps1"
        script.write_text(helper + "\n$script:AwPsmuxBin=$args[0]\n" + DRIVER,
                          encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC",
                                  "PATHEXT", "SYSTEMDRIVE", "TEMP", "TMP"}}
        env.update(
            HOME=str(home), USERPROFILE=str(home), PSMUX_DATA_DIR=str(registry),
        )
        namespace = "home-proof-" + uuid.uuid4().hex
        visible, _ = windows()
        seen_windows = {}
        foregrounds = set()
        owned = set()
        child = subprocess.Popen(
            [PWSH, "-NoProfile", "-File", str(script), PSMUX, str(root),
             namespace, mode], cwd=worktree, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 60
        while child.poll() is None and time.monotonic() < deadline:
            owned |= descendants(child.pid, processes())
            current, foreground = windows()
            seen_windows.update({hwnd: pid for hwnd, pid in current.items()
                                 if hwnd not in visible})
            foregrounds.add(foreground)
            time.sleep(0.02)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stdout + stderr
        result = json.loads(stdout)
        owned |= set(result["owned"])
        assert not set(seen_windows.values()) & owned
        assert not foregrounds & owned
        assert result["restored"] and result["standby_alive"]
        assert Path(result["pane"]["cwd"]) == worktree
        results[mode] = result
    assert results["home"]["deleted"]
    version = subprocess.run(
        [PSMUX, "--version"], capture_output=True, text=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if "3.3.5" in version.stdout:
        assert not results["legacy"]["deleted"], "The affected-binary control did not reproduce"
    with capsys.disabled():
        print("PSMux creation milliseconds:",
              {mode: result["launch_ms"] for mode, result in results.items()})
