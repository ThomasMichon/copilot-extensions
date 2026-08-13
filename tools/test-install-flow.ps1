#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Turn-key mini end-to-end install-flow test (dotfiles #935).

.DESCRIPTION
    Exercises a plugin installer's self-stage / lock behavior in an ISOLATED
    sandbox (a throwaway USERPROFILE) WITHOUT a heavy venv build, using the
    installer's COPILOT_PLUGIN_INSTALL_SMOKE seam. Asserts the recurring
    failure-class invariants that keep biting us:

      1. STAGED          the installer re-execs out of the singleton payload into
                         a per-invocation ~/.<name>/.install-stage/<ts>-<pid> dir.
      2. NOT-IN-PAYLOAD  the running (post-stage) process is NOT under the
                         installed-plugins payload dir.
      3. PAYLOAD-FREE    while a (simulated-slow) install runs, the SINGLETON
                         payload dir is still renamable -- i.e. a concurrent
                         `copilot plugin update` would NOT hit os error 32.
      4. MARKETPLACE     the marketplace source-kind is preserved across staging
                         (staged_from is under installed-plugins).
      5. NO-COLLISION    two concurrent installs get DISTINCT stage dirs and
                         neither blocks the other or the payload.
      6. NO-ORPHANS      no installer process is left holding the payload after.
      7. BOUNDED         everything completes within a timeout (catches stalls).

    Exit code 0 iff every assertion passes.

.PARAMETER Plugin
    Plugin folder name under plugins/ (default agent-bridge).
#>
[CmdletBinding()]
param(
    [string]$Plugin = 'agent-bridge',
    [string]$RepoRoot,
    [int]$SmokeSleep = 8,
    [int]$TimeoutSec = 60
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$srcPayload = Join-Path $RepoRoot "plugins\$Plugin"
# Canonical entry script: install.ps1 if present (carries an `install` action),
# else init.ps1 for plugins that ship only an idempotent bootstrap (matches
# tools/check-install-contract.py's _entrypoint_base). init.ps1 takes no action arg.
if (Test-Path (Join-Path $srcPayload 'scripts\install.ps1')) {
    $entryRel = 'scripts\install.ps1'; $entryArgs = @('install')
} elseif (Test-Path (Join-Path $srcPayload 'scripts\init.ps1')) {
    $entryRel = 'scripts\init.ps1'; $entryArgs = @()
} else {
    Write-Host "no scripts/install.ps1 or scripts/init.ps1 for plugin '$Plugin' under $RepoRoot" -ForegroundColor Red
    exit 2
}

$results = [System.Collections.Generic.List[object]]::new()
function Assert([string]$name, [bool]$ok, [string]$detail = '') {
    $results.Add([pscustomobject]@{ name = $name; ok = $ok; detail = $detail })
    $tag = if ($ok) { '[PASS]' } else { '[FAIL]' }
    $col = if ($ok) { 'Green' } else { 'Red' }
    Write-Host "  $tag $name $(if($detail){"-- $detail"})" -ForegroundColor $col
}

$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("iflow-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$payloadRoot = Join-Path $sandbox '.copilot\installed-plugins\copilot-extensions'
$payload = Join-Path $payloadRoot $Plugin
$pwshExe = (Get-Process -Id $PID).Path
$launched = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Start-Install([int]$sleep, [int]$deadline = 0, [bool]$grandchild = $false) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $pwshExe
    # Reproduce the REAL sessionStart-hook condition: Copilot launches the hook
    # (and thus the installer) with CWD = the SINGLETON payload dir. Without this
    # the child inherits the TEST's cwd, so the PAYLOAD-FREE assertion passed
    # vacuously and never caught the process-CWD lock (#1366). With it, an
    # installer that fails to re-root its own CWD off the payload leaves the
    # payload non-renamable -> PAYLOAD-FREE fails, exactly as a real
    # `copilot plugin update` would hit os error 32.
    $psi.WorkingDirectory = $payload
    foreach ($a in (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $payload $entryRel)) + $entryArgs)) { [void]$psi.ArgumentList.Add($a) }
    foreach ($e in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $psi.EnvironmentVariables[[string]$e.Key] = [string]$e.Value
    }
    $psi.EnvironmentVariables['USERPROFILE'] = $sandbox
    $psi.EnvironmentVariables['HOME'] = $sandbox
    $psi.EnvironmentVariables['COPILOT_PLUGIN_INSTALL_SMOKE'] = '1'
    $psi.EnvironmentVariables['COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP'] = "$sleep"
    if ($deadline -gt 0) { $psi.EnvironmentVariables['COPILOT_PLUGIN_INSTALL_DEADLINE_SEC'] = "$deadline" }
    if ($grandchild) { $psi.EnvironmentVariables['COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD'] = '1' }
    # ensure a clean guard state so staging actually fires
    $psi.EnvironmentVariables.Remove('COPILOT_PLUGIN_INSTALL_STAGED') | Out-Null
    $psi.EnvironmentVariables.Remove('COPILOT_PLUGIN_STAGED_FROM') | Out-Null
    $psi.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($psi)
    $launched.Add($p)
    return $p
}

function Wait-Smoke([int]$timeoutSec) {
    $smoke = Join-Path (Join-Path $sandbox ".$Plugin") 'smoke.json'
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $smoke) { try { return (Get-Content $smoke -Raw | ConvertFrom-Json) } catch {} }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Test-Renamable([string]$dir) {
    $aside = "$dir.__locktest"
    try { Rename-Item -LiteralPath $dir -NewName (Split-Path -Leaf $aside) -ErrorAction Stop
        Rename-Item -LiteralPath $aside -NewName (Split-Path -Leaf $dir) -ErrorAction Stop
        return $true
    } catch { return $false }
}

$overallOk = $true
try {
    Write-Host "== install-flow test: $Plugin ==" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $payloadRoot | Out-Null
    Copy-Item -LiteralPath $srcPayload -Destination $payloadRoot -Recurse -Force
    Assert 'payload staged into sandbox' (Test-Path (Join-Path $payload $entryRel))

    # --- single install: staging + lock invariants ---
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $p1 = Start-Install $SmokeSleep
    $smoke = Wait-Smoke $TimeoutSec
    Assert 'smoke seam reached (bounded)' ($null -ne $smoke) "waited $([int]$sw.Elapsed.TotalSeconds)s"

    if ($smoke) {
        Assert 'STAGED (re-exec fired)' ([bool]$smoke.staged)
        $ranFrom = [string]$smoke.ran_from
        Assert 'NOT-IN-PAYLOAD (ran from stage)' (($ranFrom -replace '\\', '/') -match '/\.install-stage/') "ran_from=$ranFrom"
        Assert 'MARKETPLACE preserved (staged_from under installed-plugins)' `
        ((([string]$smoke.staged_from) -replace '\\', '/') -match '/\.copilot/installed-plugins/') "staged_from=$($smoke.staged_from)"
        # The staged installer is now sleeping -> the singleton payload must be free.
        Assert 'PAYLOAD-FREE while install runs (renamable)' (Test-Renamable $payload)
        # unique stage dir present
        $stageRoot = Join-Path (Join-Path $sandbox ".$Plugin") '.install-stage'
        $stageDirs = @(Get-ChildItem $stageRoot -Directory -ErrorAction SilentlyContinue)
        Assert 'stage dir is unique per invocation' ($stageDirs.Count -ge 1) "stage dirs: $($stageDirs.Count)"
    }
    $p1.WaitForExit([Math]::Max(1, $TimeoutSec) * 1000) | Out-Null
    Assert 'install exited cleanly' ($p1.HasExited -and $p1.ExitCode -eq 0) "exit=$(if($p1.HasExited){$p1.ExitCode}else{'running'})"

    # --- collision: two concurrent installs -> distinct stage dirs, both free ---
    Remove-Item -Recurse -Force (Join-Path (Join-Path $sandbox ".$Plugin") '.install-stage') -ErrorAction SilentlyContinue
    $c1 = Start-Install $SmokeSleep
    $c2 = Start-Install $SmokeSleep
    Start-Sleep -Seconds ([Math]::Min(4, $SmokeSleep))
    $stageRoot = Join-Path (Join-Path $sandbox ".$Plugin") '.install-stage'
    # wait until at least 2 stage dirs (both staged) or timeout
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline -and @(Get-ChildItem $stageRoot -Directory -EA SilentlyContinue).Count -lt 2) { Start-Sleep -Milliseconds 200 }
    $dirs = @(Get-ChildItem $stageRoot -Directory -ErrorAction SilentlyContinue)
    Assert 'NO-COLLISION (2 concurrent -> >=2 distinct stage dirs)' ($dirs.Count -ge 2) "stage dirs: $($dirs.Count)"
    Assert 'PAYLOAD-FREE under concurrent installs' (Test-Renamable $payload)
    $c1.WaitForExit($TimeoutSec * 1000) | Out-Null
    $c2.WaitForExit($TimeoutSec * 1000) | Out-Null

    # --- no orphaned installer processes left holding the payload ---
    $orphans = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and ($_.CommandLine -replace '\\', '/') -match ([regex]::Escape(($payload -replace '\\', '/'))) })
    Assert 'NO-ORPHANS (none holding payload)' ($orphans.Count -eq 0) "orphans: $($orphans.Count)"
    Assert 'NO-ORPHANS (payload renamable after)' (Test-Renamable $payload)

    # --- WATCHDOG: a stalled install self-terminates (the (4) failure class) ---
    # Short deadline + long smoke sleep + a grandchild sleeper. The staging
    # parent watchdog must kill the WHOLE tree before the sleep elapses, leave
    # the payload free, and leave no orphan.
    Remove-Item -Recurse -Force (Join-Path (Join-Path $sandbox ".$Plugin") '.install-stage') -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path (Join-Path $sandbox ".$Plugin") 'smoke.json') -ErrorAction SilentlyContinue
    $wdDeadline = 3
    $w1 = Start-Install -sleep 90 -deadline $wdDeadline -grandchild $true
    $smokeW = Wait-Smoke $TimeoutSec
    $childPid = if ($smokeW) { [int]$smokeW.child_pid } else { 0 }
    $grandPid = if ($smokeW) { [int]$smokeW.grandchild_pid } else { 0 }
    Assert 'WATCHDOG smoke reached (child+grandchild spawned)' (($null -ne $smokeW) -and $grandPid -gt 0) "child=$childPid grand=$grandPid"
    # Parent (watchdog) should exit ~deadline; wait a bounded grace beyond it.
    $w1.WaitForExit(($wdDeadline + 20) * 1000) | Out-Null
    Assert 'WATCHDOG parent exited on deadline (124)' ($w1.HasExited -and $w1.ExitCode -eq 124) "exit=$(if($w1.HasExited){$w1.ExitCode}else{'running'})"
    Start-Sleep -Milliseconds 800
    $childAlive = [bool](Get-Process -Id $childPid -ErrorAction SilentlyContinue)
    $grandAlive = ($grandPid -gt 0) -and [bool](Get-Process -Id $grandPid -ErrorAction SilentlyContinue)
    Assert 'WATCHDOG killed the staged child' (-not $childAlive)
    Assert 'WATCHDOG killed the GRANDCHILD (whole tree)' (-not $grandAlive) "grand=$grandPid"
    Assert 'WATCHDOG payload still free after kill' (Test-Renamable $payload)

    # --- MARKER: watchdog-killed build left NO completion marker; a real (non-
    # smoke) rebuild would toss it. Assert versioned_runtime sees it incomplete. ---
    $vr = Join-Path $payload 'scripts\versioned_runtime.py'
    $srcVer = ((Get-Content (Join-Path $payload 'pyproject.toml') | Where-Object { $_ -match '^\s*version\s*=' } | Select-Object -First 1) -replace '.*=\s*"([^"]+)".*', '$1')
    $abRoot = Join-Path $sandbox ".$Plugin"
    # simulate a killed build's corpse slot (dir present, no marker)
    $corpse = Join-Path (Join-Path $abRoot 'versions') $srcVer
    New-Item -ItemType Directory -Force -Path $corpse | Out-Null
    $bootPy = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $bootPy) { $bootPy = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
    if ($bootPy) {
        & $bootPy $vr --root $abRoot is-complete $srcVer 2>$null; $incompleteRc = $LASTEXITCODE
        Assert 'MARKER absent on a killed/partial slot (is-complete => 1)' ($incompleteRc -eq 1) "rc=$incompleteRc ver=$srcVer"
        & $bootPy $vr --root $abRoot slot $srcVer --clean-incomplete 2>$null | Out-Null
        $corpseItems = @(Get-ChildItem $corpse -Force -ErrorAction SilentlyContinue)
        $tossed = (-not (Test-Path (Join-Path $corpse 'pyvenv.cfg'))) -and ($corpseItems.Count -eq 0)
        Assert 'TOSS: corpse slot cleaned for rebuild (slot --clean-incomplete)' $tossed "items=$($corpseItems.Count)"
        & $bootPy $vr --root $abRoot mark-complete $srcVer 2>$null | Out-Null
        & $bootPy $vr --root $abRoot is-complete $srcVer 2>$null; $completeRc = $LASTEXITCODE
        Assert 'MARKER present after mark-complete (is-complete => 0)' ($completeRc -eq 0) "rc=$completeRc"
    } else {
        Assert 'MARKER test skipped (no system python)' $true 'no python on PATH'
    }
}
catch {
    Assert 'test harness error' $false "$_"
}
finally {
    foreach ($p in $launched) { try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } } catch {} }
    # Reap any straggler process still referencing this sandbox (e.g. a smoke
    # grandchild if a watchdog assertion failed) so the test never leaks.
    try {
        $sbNorm = ($sandbox -replace '\\', '/')
        Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and (($_.CommandLine -replace '\\', '/') -match [regex]::Escape($sbNorm)) } |
            ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
    } catch {}
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $sandbox -ErrorAction SilentlyContinue
}

$failed = @($results | Where-Object { -not $_.ok })
Write-Host ""
Write-Host "== $($results.Count - $failed.Count)/$($results.Count) passed ==" -ForegroundColor $(if ($failed.Count) { 'Red' } else { 'Green' })
if ($failed.Count) { exit 1 } else { exit 0 }
