<#
.SYNOPSIS
    agent-dispatch installer / lifecycle manager. PS5+ compatible.

.DESCRIPTION
    Canonical installer for the agent-dispatch runtime -- the same lifecycle
    shape as the agent-bridge installer (install|update|status|start|stop|
    uninstall), so the agent-worktrees plugin reconciler (runtimeScope:
    machine-gated) and `aperture-labs services agent-dispatch <action>` both
    drive it.

    Creates the runtime at ~/.agent-dispatch/ (venv + package), a
    ~/.local/bin/agent-dispatch.cmd binstub, and -- on its deploy machines --
    an auto-starting Windows Scheduled Task running the coordinator (loopback
    127.0.0.1:9330), the analogue of the Linux systemd user unit.

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-dispatch).

.PARAMETER NoService
    Install/update the client (venv + binstub) only; do NOT install/start the
    coordinator Scheduled Task (client-only host).

.PARAMETER Purge
    On uninstall: also delete config, DB, and the env file.

.PARAMETER Force
    On update: bypass the downgrade guard (deliberate rollback). Env:
    AGENT_DISPATCH_ALLOW_DOWNGRADE=1.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$NoService,
    [switch]$Purge,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:AGENT_DISPATCH_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

# -- Paths --------------------------------------------------------------

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_dispatch'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-dispatch'
}
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$TaskName = 'agent-dispatch'

# === install-contract:v3 strip-trampolines -- keep byte-identical across plugins ===
function Remove-ConsoleTrampolines {
    <# Strip the uv-regenerated Scripts\<name>.exe console-script trampolines from
       the venv after install. They are unsigned, zero-reputation PEs that Smart
       App Control blocks (CodeIntegrity 3077); nothing launches them (binstubs,
       services, and probes all use "python.exe -m <pkg>"), so remove every
       agent-*.exe. Best-effort -- rename a locked copy aside, then sweep stale
       stashes. Windows-only: POSIX console scripts are the sanctioned launch
       path and must be preserved. #>
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe') -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item $_.FullName -Force -ErrorAction Stop
        } catch {
            try { Rename-Item $_.FullName "$($_.FullName).old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {}
        }
    }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
# === end install-contract:v3 strip-trampolines ===

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
function Get-SourceKind {
    param([string]$PluginPath)
    if (($PluginPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        if (git -C $Path status --porcelain 2>$null) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

# -- Version helpers + downgrade guard (parity with agent-bridge #1790) ------

function Get-InstalledVersion {
    if (-not (Test-Path $VenvPython)) { return $null }
    try {
        $v = & $VenvPython -c 'from importlib.metadata import version; print(version("agent-dispatch"))' 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return $v.Trim() }
    } catch {}
    return $null
}

function Get-SourceVersion {
    $manifest = Join-Path $PluginDir 'plugin.json'
    if (-not (Test-Path $manifest)) { return $null }
    $m = Select-String -Path $manifest -Pattern '"version"\s*:\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return ($m.Line -replace '.*"version"\s*:\s*"([^"]+)".*', '$1') }
    return $null
}

# Integer tuple from a version (extract every run of digits). [0,1,0,19] for
# 0.1.0-dev19. Compared element-wise so the devN build stream orders correctly.
function Get-VerTuple {
    param([string]$v)
    $nums = [regex]::Matches($v, '\d+') | ForEach-Object { [int]$_.Value }
    return , @($nums)
}

function Test-VersionLt {
    param([string]$A, [string]$B)
    if ($A -eq $B) { return $false }
    $ta = Get-VerTuple $A; $tb = Get-VerTuple $B
    $n = [Math]::Max($ta.Count, $tb.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $x = if ($i -lt $ta.Count) { $ta[$i] } else { 0 }
        $y = if ($i -lt $tb.Count) { $tb[$i] } else { 0 }
        if ($x -lt $y) { return $true }
        if ($x -gt $y) { return $false }
    }
    return $false
}

function Invoke-DowngradeGuard {
    $installed = Get-InstalledVersion
    if (-not $installed) { return }
    $source = Get-SourceVersion
    if (-not $source) {
        Write-Warn 'Could not read source version from plugin.json -- skipping downgrade guard'
        return
    }
    if (Test-VersionLt -A $source -B $installed) {
        if ($Force) {
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_DISPATCH_ALLOW_DOWNGRADE)"
            return
        }
        Write-Host ''
        Write-Fail "Refusing to downgrade agent-dispatch: installed $installed > source $source"
        Write-Fail 'This checkout is OLDER than the deployed runtime. Use the sanctioned path:'
        Write-Fail '    aperture-labs services agent-dispatch update'
        Write-Fail 'Or override intentionally (deliberate rollback):'
        Write-Fail "    install.ps1 -Action $Action -Force"
        Write-Host ''
        exit 1
    }
}

# -- Runtime install (venv + package + binstub + manifest + verify + pivot) --

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) {
        Write-Fail "Package source not found at $PkgSrcDir"
        exit 1
    }

    $hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

    # Find a Python interpreter (skip Windows Store aliases that aren't real)
    $pythonCmd = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $testOut = & $found.Source --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') { $pythonCmd = $found.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
            if ($pythonCmd) { break }
        }
    }
    if (-not $pythonCmd) {
        Write-Fail 'Python not found on PATH (need 3.10+)'
        Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
        exit 1
    }
    Write-Ok "Python: $pythonCmd"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if ($hasWinget) {
            Write-Step 'uv not found -- installing via winget...'
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP
            $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
            if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Ok 'uv installed' }
        }
    }

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    # -- venv (SAC-trusted signed base python preferred; then uv; then venv) --
    if (-not (Test-Path $VenvPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $signedBase = $null
        if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
            foreach ($v in '3.13', '3.12', '3.11') {
                $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
                if ($LASTEXITCODE -eq 0 -and $cand -and (Test-Path $cand)) {
                    try { if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break } } catch {}
                }
            }
        }
        if ($signedBase -and -not (Test-Path $VenvPython)) {
            & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        }
        if (-not (Test-Path $VenvPython)) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-Step 'Creating venv via uv...'
                & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Write-Step 'uv venv failed -- falling back to python -m venv'
                    & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
                }
            } else {
                Write-Step 'Creating venv via python -m venv...'
                & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
            }
        }
        $ErrorActionPreference = $prevEAP
        if (-not (Test-Path $VenvPython)) {
            Write-Fail "Venv creation failed -- $VenvPython not found"
            exit 1
        }
        Write-Ok 'Venv created'
    } else {
        Write-Skip 'Venv already exists'
    }

    # -- install package (uv pip install; [mcp] extra) --
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv pip install --python $VenvPython "$($PluginDir)[mcp]" --quiet 2>&1 | Out-Null
    } else {
        & $VenvPython -m pip install --quiet "$($PluginDir)[mcp]" 2>&1 | Out-Null
    }
    $pkgResult = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($pkgResult -ne 0) {
        Write-Fail 'Failed to install agent-dispatch package into venv'
        exit 1
    }
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Write-Ok 'Package installed: agent-dispatch'

    # -- binstub (.cmd on Windows -- see init history; POSIX shell elsewhere) --
    $stubName = 'agent-dispatch'
    if ($env:OS -eq 'Windows_NT') {
        $ps1Path = Join-Path $LocalBin "$stubName.ps1"
        if (Test-Path $ps1Path) { Remove-Item $ps1Path -Force -ErrorAction SilentlyContinue }
        $stubPath = Join-Path $LocalBin "$stubName.cmd"
        $stubContent = @"
@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-dispatch\.venv\Scripts\python.exe" -m agent_dispatch %*
"@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    } else {
        $stubPath = Join-Path $LocalBin $stubName
        $stubContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "`$HOME/.agent-dispatch/.venv/bin/python" -m agent_dispatch "`$@"
"@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    }
    Write-Ok "Binstub: $stubPath"

    Write-Manifest

    # -- verify --
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $importOk = $false
    for ($i = 0; $i -lt 3; $i++) {
        & $VenvPython -c 'import agent_dispatch' 2>$null
        if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
        Start-Sleep -Seconds 1
    }
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    # -- PATH --
    $pathDirs = $env:PATH -split ';'
    if ($pathDirs -contains $LocalBin) {
        Write-Ok "PATH: $LocalBin is on PATH"
    } else {
        $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
            [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
            $env:PATH = "$LocalBin;$env:PATH"
            Write-Ok "PATH: Added $LocalBin to User PATH"
        }
    }

    Register-PickerPivot
}

function Write-Manifest {
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
        $git = Get-GitInfo -Path $repoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-dispatch'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-dispatch'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($VenvDir -replace '\\', '/')
        runtime        = 'python'
    }
    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
}

function Register-PickerPivot {
    $pivotSrc = Join-Path $PluginDir 'pivots\agent-dispatch.json'
    $pivotDir = Join-Path $env:USERPROFILE '.agent-worktrees\pivots'
    if (Test-Path $pivotSrc) {
        try {
            if (-not (Test-Path $pivotDir)) { New-Item -ItemType Directory -Force -Path $pivotDir | Out-Null }
            Copy-Item -Force $pivotSrc (Join-Path $pivotDir 'agent-dispatch.json')
            Write-Ok "Picker pivot registered: $pivotDir\agent-dispatch.json"
        } catch {
            Write-Skip 'Could not register picker pivot (agent-worktrees runtime root not writable)'
        }
    } else {
        Write-Skip "Picker pivot manifest not found at $pivotSrc"
    }
}

# -- Coordinator Scheduled Task (default-on on deploy machines) --------------

function Test-WslAgentDispatch {
    # True if a WSL distro on this Windows host has an agent-dispatch coordinator
    # installed. On such a box the coordinator MUST live in WSL: a WSL-bound
    # loopback port is reachable from BOTH Windows (via WSL2 localhost-forwarding)
    # and WSL, whereas a Windows-bound port is NOT reachable from WSL over
    # localhost. So the Windows coordinator must not run, or the two collide on
    # 127.0.0.1:9330 (issue #2777). The Windows CLI still reaches the WSL
    # coordinator through the default 127.0.0.1:9330 (forwarded), so a Windows
    # client needs no URL config.
    if ($env:OS -ne 'Windows_NT') { return $false }
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        & wsl.exe -e bash -lc 'test -x "$HOME/.agent-dispatch/.venv/bin/agent-dispatch"' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Remove-CoordinatorTask {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        return $true
    }
    return $false
}

function Install-CoordinatorTask {
    # Decide full-vs-client. Explicit -NoService always wins; otherwise a Windows
    # box whose WSL peer already runs the coordinator becomes a client so the two
    # don't collide on 127.0.0.1:9330 (issue #2777).
    $clientOnly = [bool]$NoService
    $reason = 'this host is a client only (-NoService)'
    if (-not $clientOnly -and (Test-WslAgentDispatch)) {
        $clientOnly = $true
        $reason = 'the WSL peer owns the coordinator on this box (issue #2777)'
    }
    if ($clientOnly) {
        # Remove a coordinator task left from a prior full install so a host that
        # became a client stops colliding on the port.
        if (Remove-CoordinatorTask) {
            Write-Ok "Removed local coordinator Scheduled Task -- $reason"
        } else {
            Write-Skip "Coordinator service skipped -- $reason"
        }
        return
    }
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- skipping service (run: agent-dispatch serve)'
        return
    }
    $envFile = Join-Path $InstallDir 'service.env'
    if (-not (Test-Path $envFile)) {
        $envDefault = @"
# agent-dispatch coordinator service environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch
AGENT_DISPATCH_HOST=127.0.0.1
AGENT_DISPATCH_PORT=9330
# AGENT_DISPATCH_DB=%USERPROFILE%\.agent-dispatch\tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                                       # set to require bearer auth
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Service env: $envFile (defaults; edit to expose on the network / add a token)"
    } else {
        Write-Skip "Service env already exists: $envFile"
    }

    $launcher = Join-Path $InstallDir 'serve-service.ps1'
    $launcherBody = @"
# agent-dispatch coordinator launcher (generated by install.ps1). Do not edit;
# edit service.env instead. Loads service.env, then runs the coordinator.
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$envFile = Join-Path `$PSScriptRoot 'service.env'
if (Test-Path `$envFile) {
    foreach (`$line in Get-Content `$envFile) {
        `$t = `$line.Trim()
        if (`$t -eq '' -or `$t.StartsWith('#')) { continue }
        `$kv = `$t -split '=', 2
        if (`$kv.Count -eq 2) {
            [Environment]::SetEnvironmentVariable(`$kv[0].Trim(), [Environment]::ExpandEnvironmentVariables(`$kv[1].Trim()), 'Process')
        }
    }
}
& '$VenvPython' -m agent_dispatch serve
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force `
        -Description 'agent-dispatch -- portable agent task-queue coordinator' | Out-Null
    $regOk = $?
    if ($regOk) { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    $ErrorActionPreference = $prevEAP

    if ($regOk) { Write-Ok "Coordinator service installed + started (Scheduled Task '$TaskName')" }
    else { Write-Fail "Failed to register Scheduled Task '$TaskName' (run: agent-dispatch serve)" }
}

# -- Actions ----------------------------------------------------------------

function Invoke-Install {
    Write-Host ''; Write-Host '=== agent-dispatch install ===' -ForegroundColor Cyan; Write-Host ''
    Install-Runtime
    Install-CoordinatorTask
    Write-Host ''; Write-Host '=== agent-dispatch install complete ===' -ForegroundColor Cyan
}

function Invoke-Update {
    Write-Host ''; Write-Host '=== agent-dispatch update ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-DowngradeGuard
    Install-Runtime
    Install-CoordinatorTask
    Write-Host ''; Write-Host '=== agent-dispatch update complete ===' -ForegroundColor Cyan
}

function Invoke-Start {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Write-Fail "No coordinator task installed -- run: install.ps1 -Action install"; exit 1
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Ok 'Coordinator started'
}

function Invoke-Stop {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Ok 'Coordinator stopped'
    } else {
        Write-Skip 'Coordinator task not installed'
    }
}

function Invoke-Status {
    Write-Host ''; Write-Host '=== agent-dispatch status ===' -ForegroundColor Cyan
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-Ok "Deployed: $($m.source.version) (source: $($m.source.kind))"
        } catch { Write-Skip 'Deploy manifest unreadable' }
    } else {
        Write-Skip 'No deploy manifest -- not installed?'
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Ok "Coordinator task: $($task.State)"
    } else {
        Write-Skip 'No coordinator task (client-only host)'
    }
}

function Invoke-Uninstall {
    Write-Host ''; Write-Host '=== agent-dispatch uninstall ===' -ForegroundColor Cyan; Write-Host ''
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Coordinator task removed'
    }
    foreach ($n in @('agent-dispatch.cmd', 'agent-dispatch.ps1', 'agent-dispatch')) {
        $p = Join-Path $LocalBin $n
        if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue }
    }
    Write-Ok 'Binstub removed'
    $pivot = Join-Path $env:USERPROFILE '.agent-worktrees\pivots\agent-dispatch.json'
    if (Test-Path $pivot) { Remove-Item $pivot -Force -ErrorAction SilentlyContinue }
    if ($Purge) {
        if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
        Write-Ok "Runtime purged: $InstallDir (config + DB deleted)"
    } else {
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue }
        Write-Ok 'Venv removed (config + DB kept; -Purge to delete)'
    }
}

switch ($Action) {
    'install'   { Invoke-Install }
    'update'    { Invoke-Update }
    'start'     { Invoke-Start }
    'stop'      { Invoke-Stop }
    'status'    { Invoke-Status }
    'uninstall' { Invoke-Uninstall }
}
exit 0
