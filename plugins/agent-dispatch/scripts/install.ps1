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
    an auto-starting Windows Scheduled Task running the FULL coordinator. The
    always-on Windows host owns the coordinator (Phase 2, issue #2818); it binds
    adaptively by WSL networking mode (mirrored -> 127.0.0.1; NAT -> the
    vEthernet(WSL) IP, resolved at startup, never 0.0.0.0/LAN). This reverses the
    #2777 model where WSL owned the coordinator and Windows was a client.

    On a coordinator host it ALSO installs the embody SUPERVISOR
    (Scheduled Task 'agent-dispatch-supervisor'), which runs
    `agent-dispatch supervise --all-repos` so dispatched, LABELED tasks are
    turned into host embody autopilots unattended -- the Windows peer of the
    Linux systemd supervisor unit (cross-platform-parity). It is label-gated
    for safety (a label-less supervisor would embody every queued task): the
    task is enabled only when AGENT_DISPATCH_SUPERVISE_LABELS is set in
    supervisor.env; with none set the task is registered but left DISABLED
    (inert), and the generated launcher hard-refuses a label-less run (#2869).

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-dispatch).

.PARAMETER NoService
    Install/update the client (venv + binstub) only; do NOT install/start the
    coordinator Scheduled Task (a deliberately client-only host). Also skips the
    embody supervisor (it needs a local coordinator).

.PARAMETER NoSupervisor
    Install everything EXCEPT the embody supervisor Scheduled Task (the
    coordinator still installs on an eligible host).

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
    [switch]$NoSupervisor,
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
$SupervisorTaskName = 'agent-dispatch-supervisor'
$DefaultPort = 9847

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

    # -- install package (uv pip install; [mcp] extra with graceful fallback) --
    # The [mcp] extra pulls `mcp` -> `pyjwt[crypto]` -> `cryptography`, which has
    # no prebuilt wheel on some platforms (notably win-arm64) and needs a Rust +
    # MSVC toolchain to build from source. Per the plugin-services vision's
    # `degrade-gracefully` behavior, a build failure of the OPTIONAL MCP server
    # surface must not abort the whole install: fall back to the base package so
    # the coordinator CLI still deploys; only `agent-dispatch mcp` stays dark
    # until the toolchain is present.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    $installPkg = {
        param([string]$Spec)
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $out = & uv pip install --python $VenvPython $Spec 2>&1 | Out-String
        } else {
            $out = & $VenvPython -m pip install $Spec 2>&1 | Out-String
        }
        [pscustomobject]@{ Code = $LASTEXITCODE; Output = $out }
    }

    $mcpResult = & $installPkg "$($PluginDir)[mcp]"
    if ($mcpResult.Code -eq 0) {
        Write-Ok 'Package installed: agent-dispatch [mcp]'
    } else {
        Write-Warn 'Could not install the [mcp] extra (its native deps may not build on this platform) -- falling back to a base install without the MCP server surface'
        $baseResult = & $installPkg "$PluginDir"
        if ($baseResult.Code -ne 0) {
            Write-Fail 'Failed to install agent-dispatch package into venv'
            Write-Host $baseResult.Output
            $ErrorActionPreference = $prevEAP
            exit 1
        }
        Write-Ok 'Package installed: agent-dispatch (base -- `agent-dispatch mcp` server unavailable on this platform)'
    }
    $ErrorActionPreference = $prevEAP
    Remove-ConsoleTrampolines -VenvDir $VenvDir

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

function Remove-CoordinatorTask {
    # Returns 'removed' | 'blocked' | 'absent'. Unregister-ScheduledTask may need
    # elevation (Access denied) for a task in the root folder. Used by the
    # -NoService client path and by uninstall.
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        return 'absent'
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        return 'blocked'
    }
    return 'removed'
}

function Install-CoordinatorTask {
    # Windows OWNS the coordinator (Phase 2, issue #2818): the always-on Windows
    # host runs the full coordinator and the WSL guest is a client. This reverses
    # the #2777 model (WSL-owned, Windows client). Explicit -NoService still forces
    # a client-only host (e.g. a box that intentionally has no coordinator).
    if ($NoService) {
        # Remove a coordinator task left from a prior full install so a host asked
        # to be client-only stops running one. Removal may be blocked without
        # elevation -- log and continue.
        switch (Remove-CoordinatorTask) {
            'removed' { Write-Ok   'Removed local coordinator Scheduled Task (-NoService: client-only host)' }
            'blocked' { Write-Skip 'Coordinator task present but not removable without elevation (-NoService) -- run elevated to remove it' }
            default   { Write-Skip 'Coordinator service skipped (-NoService: client-only host)' }
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
# AGENT_DISPATCH_HOST is resolved dynamically at startup by serve-service.ps1
# (mirrored -> 127.0.0.1; NAT -> the vEthernet(WSL) IP). Uncomment only to pin it.
# AGENT_DISPATCH_HOST=127.0.0.1
AGENT_DISPATCH_PORT=$DefaultPort
# AGENT_DISPATCH_DB=%USERPROFILE%\.agent-dispatch\tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                                       # set to require bearer auth
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Service env: $envFile (defaults; edit to pin the bind host / add a token)"
    } else {
        # Migrate a stale Phase-1 host pin (#2888). Early (dev39) installs wrote
        # an ACTIVE `AGENT_DISPATCH_HOST=127.0.0.1` line into service.env. On a
        # NAT box that pin makes the coordinator bind loopback -- unreachable
        # from WSL -- because both the launcher and `serve` skip bind-host
        # resolution whenever AGENT_DISPATCH_HOST is set. Comment out that exact
        # old-default line so dynamic resolution takes over; leave any other
        # (operator-chosen) AGENT_DISPATCH_HOST value untouched.
        $envLines = Get-Content $envFile
        $migrated = $false
        $newEnvLines = foreach ($envLine in $envLines) {
            if ($envLine -match '^\s*AGENT_DISPATCH_HOST\s*=\s*127\.0\.0\.1\s*$') {
                $migrated = $true
                '# AGENT_DISPATCH_HOST=127.0.0.1  # migrated (#2888): now resolved dynamically at startup (mirrored -> 127.0.0.1; NAT -> vEthernet(WSL) IP)'
            } else {
                $envLine
            }
        }
        if ($migrated) {
            [System.IO.File]::WriteAllText($envFile, (($newEnvLines -join "`r`n") + "`r`n"), $utf8NoBom)
            Write-Ok "Service env: migrated stale AGENT_DISPATCH_HOST=127.0.0.1 pin (#2888) -> dynamic bind-host resolution"
        } else {
            Write-Skip "Service env already exists: $envFile"
        }
    }

    $launcher = Join-Path $InstallDir 'serve-service.ps1'
    $launcherBody = @"
# agent-dispatch coordinator launcher (generated by install.ps1). Do not edit;
# edit service.env instead. Loads service.env, then runs `serve`. `serve`
# resolves the bind host per WSL networking mode (mirrored -> 127.0.0.1; NAT ->
# the dynamic vEthernet(WSL) IP, re-resolved on each start) with a bounded retry
# that rides out the logon-before-WSL race on NAT (#2889). All output is teed to
# serve-service.log so a NAT bind failure / retry is diagnosable -- the Scheduled
# Task runs headless (conhost --headless), so console output is otherwise lost.
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
`$logFile = Join-Path `$PSScriptRoot 'serve-service.log'
try {
    if ((Test-Path `$logFile) -and ((Get-Item `$logFile).Length -gt 1MB)) {
        Move-Item -Force `$logFile "`$logFile.1"
    }
} catch { }
`$pinned = if (`$env:AGENT_DISPATCH_HOST) { `$env:AGENT_DISPATCH_HOST } else { 'auto (resolved by serve)' }
`$portShown = if (`$env:AGENT_DISPATCH_PORT) { `$env:AGENT_DISPATCH_PORT } else { 'default' }
"[`$(Get-Date -Format o)] agent-dispatch coordinator launch (host=`$pinned port=`$portShown)" |
    Out-File -FilePath `$logFile -Append -Encoding utf8
# Tee every stream (stdout/stderr/warning/info) to the log while still writing
# through, so the retry lines from serve's bind-host resolution are captured.
# serve logs via uvicorn to STDERR; under `$ErrorActionPreference = 'Stop'`
# PowerShell wraps a native command's stderr as a terminating NativeCommandError
# and would kill the long-lived coordinator on its very first log line (observed
# on Lambda-Core: task launched, banner written, no listener). Drop to
# 'Continue' for the serve invocation so stderr is captured, never fatal.
`$ErrorActionPreference = 'Continue'
& '$VenvPython' -m agent_dispatch serve 2>&1 | Out-File -FilePath `$logFile -Append -Encoding utf8
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

    # Use conhost --headless to prevent Windows Terminal from capturing the
    # task's powershell as a visible window/tab when Terminal is the default
    # terminal app. -WindowStyle Hidden alone is ignored by Windows Terminal, so
    # a bare `powershell -WindowStyle Hidden` task surfaces a real console window
    # -- and because the launcher runs the long-lived `-m agent_dispatch serve`
    # in-process, that window persists for the life of the coordinator.
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    # Two triggers: -AtStartup makes the coordinator a true always-on service that
    # comes up at boot with NO interactive login (essential for a headless box
    # like Borealis, accessed only over SSH); -AtLogOn additionally (re)starts it
    # when the operator logs in (covers a manually-stopped task on a
    # console-driven box). The dev58 bounded bind-host retry
    # (_resolve_bind_host_resilient) rides out the boot-before-WSL race on NAT,
    # where the vEthernet(WSL) IP isn't up yet the instant -AtStartup fires.
    $trigger = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogOn)
    )
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # LogonType S4U ("run whether the user is logged on or not", no stored
    # password): the coordinator must run headless. The prior Interactive logon
    # type only ran while the user had an interactive console session, so on a
    # headless SSH-only box the task registered but never fired (observed on
    # Borealis: State=Ready, LastRunTime=never). S4U runs it in a non-interactive
    # session at boot; validated binding the vEthernet(WSL) IP on Borealis NAT and
    # loopback on mirrored hosts. Set-ScheduledTask/Register with S4U succeeds
    # non-elevated (unlike a password-backed Password logon). NOTE: the supervisor
    # task below deliberately stays Interactive -- it spawns embody CLI sessions
    # that need an interactive session, which S4U's non-interactive station lacks.
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType S4U -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Register-ScheduledTask raises a TERMINATING "Access is denied" on a
    # non-elevated host, which would abort the whole installer with a non-zero
    # exit even though the client (venv + binstub + manifest) is already fully
    # deployed above. Per the plugin-services vision's `degrade-gracefully`
    # behavior, a client-only host (e.g. a field terminal that is not a
    # coordinator) must still complete: trap the failure into the existing
    # non-fatal $regOk path instead of terminating.
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- portable agent task-queue coordinator' | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    if ($regOk) { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    $ErrorActionPreference = $prevEAP

    if ($regOk) { Write-Ok "Coordinator service installed + started (Scheduled Task '$TaskName')" }
    else { Write-Warn "Coordinator service not registered (needs elevation) -- client is installed; run elevated, or 'agent-dispatch serve' to run the coordinator manually" }
}

# -- Embody supervisor Scheduled Task (Windows; label-gated) ----------------
#
# The Windows peer of the Linux systemd supervisor unit (cross-platform-parity).
# Runs `agent-dispatch supervise --all-repos`, turning queued LABELED tasks into
# host embody autopilots. `--all-repos` avoids the lane-scoping gotcha (a short
# `--repo owner/name` filters every task out), which makes the label opt-in the
# ONLY thing between the supervisor and embodying *every* queued task -- so the
# task is enabled only when supervisor.env sets AGENT_DISPATCH_SUPERVISE_LABELS.
# See #2869.

function Test-SupervisorLabelsConfigured {
    # True when supervisor.env declares a non-empty AGENT_DISPATCH_SUPERVISE_LABELS.
    $envFile = Join-Path $InstallDir 'supervisor.env'
    if (-not (Test-Path $envFile)) { return $false }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*AGENT_DISPATCH_SUPERVISE_LABELS\s*=\s*(.+?)\s*$') {
            $val = $Matches[1].Trim().Trim('"').Trim("'")
            $val = ($val -replace '[\s,]', '')
            if ($val -ne '') { return $true }
        }
    }
    return $false
}

function Remove-SupervisorTask {
    # Returns 'removed' | 'blocked' | 'absent'. Mirrors Remove-CoordinatorTask.
    if (-not (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue)) {
        return 'absent'
    }
    Stop-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $SupervisorTaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) {
        return 'blocked'
    }
    return 'removed'
}

function Install-SupervisorTask {
    # Install only where the full coordinator lives (a client-only host has no
    # local coordinator for the supervisor to talk to). -NoSupervisor opts a full
    # host out; -NoService (client-only) skips it too. Remove a stale task in
    # either case so a host that became client-only stops supervising.
    if ($NoSupervisor -or $NoService) {
        switch (Remove-SupervisorTask) {
            'removed' { Write-Ok   'Removed embody supervisor task (client-only / -NoSupervisor)' }
            'blocked' { Write-Skip 'Supervisor task present but not removable without elevation -- run elevated to remove it' }
            default   { Write-Skip 'Embody supervisor skipped (client-only / -NoSupervisor)' }
        }
        return
    }
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- run "agent-dispatch supervise --all-repos --label <L>" manually'
        return
    }

    $envFile = Join-Path $InstallDir 'supervisor.env'
    if (-not (Test-Path $envFile)) {
        $envDefault = @"
# agent-dispatch embody supervisor environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch-supervisor
#
# SAFETY: the supervisor turns queued tasks into AUTONOMOUS embody sessions. It
# runs with --all-repos, so it is GATED by an explicit label opt-in: only queued
# tasks carrying one of these labels are embodied. With NO labels set, the task
# is left DISABLED -- a label-less supervisor would embody EVERY queued task
# (handoffs, interactive worktree-pinned tasks, ...), which is unsafe.
#
# Opt-in labels, comma- or space-separated (REQUIRED to enable the task):
AGENT_DISPATCH_SUPERVISE_LABELS=
# Poll interval, seconds (default 30):
AGENT_DISPATCH_SUPERVISE_INTERVAL=30
# Max concurrent in-flight embodies (default 1 = max-one-active):
AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=1
# Max failed spawn attempts before a task is dead-lettered (default 3; 0=disable):
AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS=3
# Extra raw flags appended to the invocation (advanced; e.g. fleet mode:
#   --pool host-a,host-b --origin lambda-core):
AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Supervisor env: $envFile (no labels -> task stays inert; add a label to enable)"
    } else {
        Write-Skip "Supervisor env already exists: $envFile"
    }

    # Launcher: loads supervisor.env, builds the supervise argv (labels -> repeated
    # --label flags), and hard-refuses a label-less run (defense-in-depth: the
    # registration below leaves it disabled without labels, but a hand-enable must
    # not embody everything). supervise logs to STDERR, so -- as with the
    # coordinator launcher -- drop to 'Continue' for the invocation so native
    # stderr is captured, not a terminating NativeCommandError.
    $launcher = Join-Path $InstallDir 'supervise-service.ps1'
    $launcherBody = @"
# agent-dispatch embody supervisor launcher (generated by install.ps1; #2869).
# Do not edit; edit supervisor.env instead.
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$envFile = Join-Path `$PSScriptRoot 'supervisor.env'
`$labels = ''
`$interval = '30'
`$maxConcurrent = '1'
`$maxAttempts = '3'
`$extra = ''
if (Test-Path `$envFile) {
    foreach (`$line in Get-Content `$envFile) {
        `$t = `$line.Trim()
        if (`$t -eq '' -or `$t.StartsWith('#')) { continue }
        `$kv = `$t -split '=', 2
        if (`$kv.Count -ne 2) { continue }
        `$k = `$kv[0].Trim(); `$v = `$kv[1].Trim()
        switch (`$k) {
            'AGENT_DISPATCH_SUPERVISE_LABELS'         { `$labels = `$v }
            'AGENT_DISPATCH_SUPERVISE_INTERVAL'       { if (`$v) { `$interval = `$v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT' { if (`$v) { `$maxConcurrent = `$v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS'   { if (`$v) { `$maxAttempts = `$v } }
            'AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS'     { `$extra = `$v }
        }
    }
}
`$argsList = @('supervise', '--all-repos', '--interval', `$interval,
    '--max-concurrent', `$maxConcurrent, '--max-attempts', `$maxAttempts)
`$haveLabel = `$false
foreach (`$l in (`$labels -split '[\s,]+')) {
    if (`$l) { `$argsList += @('--label', `$l); `$haveLabel = `$true }
}
if (-not `$haveLabel) {
    Write-Error 'agent-dispatch-supervisor: refusing to run with no opt-in label. A label-less supervisor would embody EVERY queued task. Set AGENT_DISPATCH_SUPERVISE_LABELS in supervisor.env.'
    exit 78  # EX_CONFIG
}
if (`$extra) { `$argsList += (`$extra -split '\s+') }
`$logFile = Join-Path `$PSScriptRoot 'supervise-service.log'
try {
    if ((Test-Path `$logFile) -and ((Get-Item `$logFile).Length -gt 1MB)) {
        Move-Item -Force `$logFile "`$logFile.1"
    }
} catch { }
"[`$(Get-Date -Format o)] agent-dispatch supervisor launch (labels=`$labels interval=`$interval)" |
    Out-File -FilePath `$logFile -Append -Encoding utf8
`$ErrorActionPreference = 'Continue'
& '$VenvPython' -m agent_dispatch @argsList 2>&1 | Out-File -FilePath `$logFile -Append -Encoding utf8
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $SupervisorTaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- embody spawn supervisor (labeled queued tasks -> host embody autopilots)' | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    if (-not $regOk) {
        $ErrorActionPreference = $prevEAP
        Write-Warn "Embody supervisor not registered (needs elevation) -- coordinator is installed; run elevated to add it"
        return
    }

    if (Test-SupervisorLabelsConfigured) {
        Enable-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue | Out-Null
        Start-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        $ErrorActionPreference = $prevEAP
        Write-Ok "Embody supervisor installed + started (Scheduled Task '$SupervisorTaskName')"
    } else {
        # No opt-in label -> leave the task registered but DISABLED (inert), the
        # Windows analogue of an installed-but-not-enabled systemd unit.
        Disable-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue | Out-Null
        $ErrorActionPreference = $prevEAP
        Write-Ok "Embody supervisor installed (INERT: no opt-in label; task disabled). To enable: set"
        Write-Step "AGENT_DISPATCH_SUPERVISE_LABELS in $envFile, then re-run update"
        Write-Step "(or: Enable-ScheduledTask -TaskName $SupervisorTaskName; Start-ScheduledTask -TaskName $SupervisorTaskName)"
    }
}

# -- Port reservation (Windows) ---------------------------------------------

function Test-Elevated {
    # True when the current process holds the Administrators role. Windows-only.
    if ($env:OS -ne 'Windows_NT') { return $false }
    try {
        $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Test-PortExcluded {
    # True if $Port falls within any TCP excluded/reserved range that netsh lists
    # (persistent reservations plus live dynamic Hyper-V/WSL exclusions).
    param([Parameter(Mandatory)][int]$Port)
    $out = & netsh.exe int ipv4 show excludedportrange protocol=tcp 2>$null
    foreach ($line in $out) {
        if ($line -match '^\s*(\d+)\s+(\d+)') {
            $start = [int]$Matches[1]
            $end = [int]$Matches[2]
            if ($Port -ge $start -and $Port -le $end) { return $true }
        }
    }
    return $false
}

function Add-PortReservation {
    # Persistently reserve the coordinator port so the Windows dynamic port
    # allocator (Hyper-V/WSL/HNS) never steals it -- the durable fix for the
    # transient WinError 10013 collisions (issue #2818). Idempotent: skips when
    # the port is already excluded. Needs elevation; degrades to a logged SKIP
    # (with the one-time command) when not admin.
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Get-Command netsh.exe -ErrorAction SilentlyContinue)) {
        Write-Skip 'netsh unavailable -- cannot reserve coordinator port'
        return
    }
    $port = $DefaultPort
    if ($env:AGENT_DISPATCH_PORT) {
        try { $port = [int]$env:AGENT_DISPATCH_PORT } catch { $port = $DefaultPort }
    }
    if (Test-PortExcluded -Port $port) {
        Write-Skip "Coordinator port $port already reserved/excluded (netsh)"
        return
    }
    if (-not (Test-Elevated)) {
        Write-Skip "Coordinator port $port not reserved -- needs elevation (run once, elevated: netsh int ipv4 add excludedportrange protocol=tcp startport=$port numberofports=1)"
        return
    }
    $null = & netsh.exe int ipv4 add excludedportrange protocol=tcp startport=$port numberofports=1 2>&1
    if (Test-PortExcluded -Port $port) {
        Write-Ok "Coordinator port $port reserved (netsh excludedportrange)"
    } else {
        Write-Warn "Could not reserve coordinator port $port (netsh add failed -- may be held by a live dynamic exclusion; retry after a WSL/Hyper-V restart)"
    }
}

# -- Coordinator firewall (Windows, NAT mode only) --------------------------

function Add-CoordinatorFirewallRule {
    # In NAT mode the coordinator binds the vEthernet(WSL) IP, so inbound WSL
    # traffic arrives on the vEthernet(WSL) interface. Add an inbound allow rule
    # SCOPED to that interface (never profile-wide, never the LAN) so a WSL client
    # can reach the coordinator while the LAN stays isolated. Mirrored mode needs
    # no rule (shared loopback). Idempotent; needs elevation -- degrades to a
    # logged SKIP with the one-time command, mirroring Add-PortReservation.
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Test-Path $VenvPython)) { return }

    # Determine the WSL networking mode from the single source of truth (the
    # Python detector). Only NAT needs a firewall rule.
    $mode = ''
    try {
        $mode = (& $VenvPython -c "from agent_dispatch.netinfo import get_wsl_networking_mode; print(get_wsl_networking_mode())" 2>$null).Trim()
    } catch { $mode = '' }
    if ($mode -ne 'nat') {
        Write-Skip "Coordinator firewall rule not needed (WSL networking mode: $(if ($mode) { $mode } else { 'unknown' }); rule is NAT-only)"
        return
    }

    $port = $DefaultPort
    if ($env:AGENT_DISPATCH_PORT) {
        try { $port = [int]$env:AGENT_DISPATCH_PORT } catch { $port = $DefaultPort }
    }
    $ruleName = 'agent-dispatch coordinator (WSL)'

    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Write-Skip 'NetSecurity module unavailable -- cannot add coordinator firewall rule'
        return
    }

    # Resolve the vEthernet(WSL) interface alias (exact, else the (WSL*) match).
    $alias = $null
    try {
        $ipObj = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } |
            Select-Object -First 1
        if ($ipObj) { $alias = $ipObj.InterfaceAlias }
    } catch { $alias = $null }
    if (-not $alias) {
        Write-Skip 'Coordinator firewall rule skipped -- no vEthernet(WSL) interface found (WSL networking not up?)'
        return
    }

    if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
        Write-Skip "Coordinator firewall rule already present ('$ruleName')"
        return
    }
    if (-not (Test-Elevated)) {
        Write-Skip "Coordinator firewall rule not added -- needs elevation (run once, elevated: New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -InterfaceAlias '$alias')"
        return
    }
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $port -InterfaceAlias $alias -Profile Any `
            -Description 'agent-dispatch coordinator -- WSL-only, interface-scoped (issue #2818)' `
            -ErrorAction Stop | Out-Null
        Write-Ok "Coordinator firewall rule added ('$ruleName' on '$alias', TCP $port, WSL-only)"
    } catch {
        Write-Warn "Could not add coordinator firewall rule: $_"
    }
}

# -- Actions ----------------------------------------------------------------

function Invoke-Install {
    Write-Host ''; Write-Host '=== agent-dispatch install ===' -ForegroundColor Cyan; Write-Host ''
    Install-Runtime
    Add-PortReservation
    Install-CoordinatorTask
    if (-not $NoService) { Add-CoordinatorFirewallRule }
    Install-SupervisorTask
    Write-Host ''; Write-Host '=== agent-dispatch install complete ===' -ForegroundColor Cyan
}

function Invoke-Update {
    Write-Host ''; Write-Host '=== agent-dispatch update ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-DowngradeGuard
    Install-Runtime
    Add-PortReservation
    Install-CoordinatorTask
    if (-not $NoService) { Add-CoordinatorFirewallRule }
    Install-SupervisorTask
    Write-Host ''; Write-Host '=== agent-dispatch update complete ===' -ForegroundColor Cyan
}

function Invoke-Start {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Write-Fail "No coordinator task installed -- run: install.ps1 -Action install"; exit 1
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Ok 'Coordinator started'
    # Start the supervisor too, but only if it is enabled (label-gated). A
    # disabled/inert supervisor is left alone.
    $sup = Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    if ($sup -and $sup.State -ne 'Disabled') {
        Start-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        Write-Ok 'Embody supervisor started'
    }
}

function Invoke-Stop {
    if (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        Write-Ok 'Embody supervisor stopped'
    }
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
    $sup = Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    if ($sup) {
        if (Test-SupervisorLabelsConfigured) {
            Write-Ok "Embody supervisor task: $($sup.State)"
        } else {
            Write-Ok "Embody supervisor task: $($sup.State) (INERT: no opt-in label set)"
        }
    } else {
        Write-Skip 'No embody supervisor task (client-only host, -NoSupervisor, or unavailable)'
    }
}

function Invoke-Uninstall {
    Write-Host ''; Write-Host '=== agent-dispatch uninstall ===' -ForegroundColor Cyan; Write-Host ''
    if (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $SupervisorTaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Embody supervisor task removed'
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Coordinator task removed'
    }
    if (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue) {
        $fwRule = 'agent-dispatch coordinator (WSL)'
        if (Get-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue
            Write-Ok 'Coordinator firewall rule removed'
        }
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
