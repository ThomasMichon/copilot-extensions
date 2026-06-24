<#
.SYNOPSIS
    Agent Logger -- session-sync installer (Windows).

.DESCRIPTION
    Creates a venv at ~/.agent-logger, installs the agent-logger package, and
    registers a Scheduled Task that runs `session-sync run --prune` every 4
    hours. Windows-first by design: the runtime is the venv's python invoked
    as `python -m agent_logger.sync.engine` (the console-script .exe is not
    relied upon, matching the other plugins' Smart App Control posture). The
    scheduled task runs under the windowless pythonw.exe host so the sync flow
    never flashes a console window.

    Run from the repo root:
      pwsh -File plugins\agent-logger\scripts\install.ps1 install
      pwsh -File plugins\agent-logger\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action: install | update | uninstall | status.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'update', 'uninstall', 'status')]
    [string]$Action = 'status'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Ok      { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Changed { param([string]$m) Write-Host "  [->]   $m" -ForegroundColor Yellow }
function Write-Warn2   { param([string]$m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Step    { param([string]$m) Write-Host "  ...    $m" }
function Write-Warn    { param([string]$m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Fail    { param([string]$m) Write-Host "  [FAIL] $m" -ForegroundColor Red }

$InstallDir = Join-Path $env:USERPROFILE '.agent-logger'
$VenvDir    = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
# pythonw.exe is the GUI-subsystem (windowless) Python host. Running the
# scheduled sync under it -- rather than console python.exe -- stops the
# engine's own console window from flashing on each 4-hourly run. The engine's
# rsync/ssh children are kept windowless separately via CREATE_NO_WINDOW.
$VenvPythonw = Join-Path $VenvDir 'Scripts\pythonw.exe'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$TaskName   = 'Agent Logger Session Sync'
$BinstubPs1 = Join-Path $LocalBin 'session-sync.ps1'
$BinstubCmd = Join-Path $LocalBin 'session-sync.cmd'
$AltBinstubPs1 = Join-Path $LocalBin 'agent-logger.ps1'
$AltBinstubCmd = Join-Path $LocalBin 'agent-logger.cmd'

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

# agent-logger ships console scripts that do not match agent-*.exe (session-sync,
# collate-session, read-session-digest, prepare-session-log). They are likewise
# unsigned, never launched (binstubs use python -m), and SAC-blocked, so sweep
# them too. The shared block above stays byte-identical; this is an additive,
# plugin-specific cleanup.
function Remove-LoggerTrampolines {
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    foreach ($n in @('session-sync', 'collate-session', 'read-session-digest', 'prepare-session-log')) {
        $exe = Join-Path $scriptsDir "$n.exe"
        if (Test-Path $exe) {
            try { Remove-Item $exe -Force -ErrorAction Stop }
            catch { try { Rename-Item $exe "$exe.old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {} }
        }
    }
    Get-ChildItem (Join-Path $scriptsDir '*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}

function Get-SignedBasePython {
    <# Return a SAC-trusted (Authenticode-signed) base Python (>=3.10), or $null.
       Smart App Control blocks the unsigned uv-managed Python and console-script
       trampoline; a venv built from a signed base with `--copies` has a signed
       python.exe that SAC allows. #>
    if ($env:OS -ne 'Windows_NT') { return $null }
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p }
        }
    }
    foreach ($c in ($cands | Select-Object -Unique)) {
        if (Test-Path $c) {
            try { if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') { return $c } } catch {}
        }
    }
    return $null
}

function New-SignedVenv {
    <# Create or rebuild $VenvDir so its python.exe is SAC-trusted. Prefers a
       signed base Python via `--copies`; rebuilds an existing unsigned venv;
       falls back to uv (unsigned) when no signed Python exists. Returns $true
       if $VenvPython is present afterward. #>
    if ((Test-Path $VenvPython) -and ($env:OS -eq 'Windows_NT')) {
        $sig = try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        if ($sig -ne 'Valid' -and (Get-SignedBasePython)) {
            Write-Step 'Existing venv python is unsigned (Smart App Control-incompatible) -- rebuilding from signed Python'
            try { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop }
            catch { Write-Warn "Could not remove existing venv (in use?): $_" }
        }
    }
    if (Test-Path $VenvPython) { return $true }

    $signedBase = Get-SignedBasePython
    if ($signedBase) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        if (Test-Path $VenvPython) {
            Write-Ok "Venv created from signed Python ($signedBase)"
            return $true
        }
        Write-Warn 'Signed-Python venv creation failed -- falling back to uv'
    } elseif ($env:OS -eq 'Windows_NT') {
        Write-Warn 'No signed system Python found -- using uv (unsigned). On Smart App Control machines, install python.org Python 3.10+ and re-run.'
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv venv $VenvDir --python 3.10 --allow-existing 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { & uv venv $VenvDir --allow-existing 2>&1 | Out-Null }
    $ErrorActionPreference = $prevEAP
    return (Test-Path $VenvPython)
}

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        $dirtyOut = git -C $Path status --porcelain 2>$null
        if ($dirtyOut) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. `update` re-installs from whatever
# the recorded footprint is, because the same installer is invoked from the
# same place.
function Get-SourceKind {
    param([string]$PluginPath)
    if (($PluginPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
function Write-DeployManifestFor {
    param(
        [string]$Service,
        [string]$Plugin,
        [string]$InstallPath,
        [string]$PluginPath,
        [string]$VenvPath
    )
    $manifestPath = Join-Path $InstallPath 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginPath

    $ver = '0.0.0'
    $pyproj = Join-Path $PluginPath 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }

    # Git provenance only applies to a local checkout -- the marketplace vendor
    # copy is not a git repo.
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $gitInfo = Get-GitInfo -Path (Split-Path $PluginPath)
        $commit = $gitInfo.commit; $branch = $gitInfo.branch; $dirty = $gitInfo.dirty
    }

    $manifest = [ordered]@{
        schema_version = 3
        service        = $Service
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginPath -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = $Plugin
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($VenvPath -replace '\\', '/')
        runtime        = 'python'
    }

    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
}

function Write-DeployManifest {
    Write-DeployManifestFor -Service 'agent-logger' -Plugin 'agent-logger' `
        -InstallPath $InstallDir -PluginPath $PluginDir -VenvPath $VenvDir
}

function Write-Binstubs {
    <# Deploy the agent-logger CLI binstubs into ~/.local/bin as a .ps1 primary
       plus a .cmd fallback. PowerShell resolves a .ps1 (ExternalScript) ahead of
       a .cmd (Application) in the same dir; both launch the venv's signed python
       via `-m`, never the unsigned console-script trampoline .exe that Smart App
       Control blocks (3077). #>
    param([Parameter(Mandatory)][string]$PythonExe)

    $stubs = @{
        'session-sync' = 'agent_logger.sync.engine'
        'agent-logger' = 'agent_logger'
    }
    foreach ($name in $stubs.Keys) {
        $mod = $stubs[$name]
        $ps1Path = Join-Path $LocalBin "$name.ps1"
        $cmdPath = Join-Path $LocalBin "$name.cmd"
        $ps1 = "`$env:PYTHONUTF8 = '1'`r`n& `"$PythonExe`" -m $mod @args`r`nexit `$LASTEXITCODE"
        [System.IO.File]::WriteAllText($ps1Path, $ps1, (New-Object System.Text.UTF8Encoding($false)))
        $cmd = "@echo off`r`nset `"PYTHONUTF8=1`"`r`n`"$PythonExe`" -m $mod %*"
        [System.IO.File]::WriteAllText($cmdPath, $cmd)
    }
    Write-Ok "wrote binstubs to $LocalBin (.ps1 + .cmd)"
}

function Install-Package {
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    if (-not (Test-Path $LocalBin))   { New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null }

    # Prerequisite: uv (venv + package management per the install contract).
    try { uv --version 2>&1 | Out-Null } catch {
        Write-Fail 'uv not found on PATH (required for venv + package management)'
        Write-Fail 'Install: https://docs.astral.sh/uv/getting-started/installation/'
        exit 1
    }

    # SAC-safe venv: prefer a signed base Python via --copies; rebuild unsigned.
    if (-not (New-SignedVenv)) {
        Write-Fail "Failed to create venv at $VenvDir"
        exit 1
    }

    # Pre-strip any locked console-script trampoline so uv can overwrite it
    # (Windows denies overwriting an in-use .exe -- os error 5).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Remove-LoggerTrampolines -VenvDir $VenvDir

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1
    $result = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($result -ne 0) {
        Write-Fail "Package install failed (exit $result)"
        if ($out) { Write-Host ($out | Out-String) }
        exit 1
    }
    Write-Ok "installed agent-logger package"

    # Strip the uv-regenerated console-script trampolines (SAC-blocked, unused).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Remove-LoggerTrampolines -VenvDir $VenvDir

    # Binstubs: .ps1 primary + .cmd fallback that invoke `python -m`
    # (never the SAC-blocked console-script trampolines).
    Write-Binstubs -PythonExe $VenvPython

    # Record the deploy footprint (source: local vs marketplace).
    Write-DeployManifest
}

function Register-SyncTask {
    # Prefer the windowless host so the task never flashes a console; fall back
    # to console python.exe only if pythonw.exe is somehow absent.
    $runHost = if (Test-Path $VenvPythonw) { $VenvPythonw } else { $VenvPython }
    $action = New-ScheduledTaskAction -Execute $runHost `
        -Argument '-m agent_logger.sync.engine run --prune'
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours 4)
    $trigger.Repetition.StopAtDurationEnd = $false
    # 30-min cap: the first sync cold-copies the whole session history (can take
    # 10+ min over a network/CIFS path); a 10-min limit killed it mid-copy.
    # Incremental runs finish in seconds.
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    # Interactive logon: runs as the current user when logged on, and -- unlike
    # an S4U principal -- registers without elevation. Right default for a
    # per-user roaming workstation. (Run-when-logged-off would need admin.)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal | Out-Null
        Write-Changed "scheduled task updated (every 4h)"
    } else {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal `
            -Description 'Agent Logger -- push Copilot session data to the configured target every 4 hours.' | Out-Null
        Write-Changed "scheduled task registered (every 4h)"
    }
}

switch ($Action) {
    'install' {
        Install-Package
        Register-SyncTask
        Write-Ok "install complete"
    }
    'update' {
        Install-Package
        Write-Ok "package updated (task unchanged)"
    }
    'uninstall' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Changed "scheduled task removed (config at $InstallDir kept)"
        } else {
            Write-Warn2 "no scheduled task found"
        }
        foreach ($f in @($BinstubPs1, $BinstubCmd, $AltBinstubPs1, $AltBinstubCmd)) {
            if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
        }
        Write-Changed "binstubs removed from $LocalBin"
    }
    'status' {
        if (Test-Path $VenvPython) {
            Write-Ok ("installed: " + (& $VenvPython -m agent_logger version))
            & $VenvPython -m agent_logger.sync.engine status
        } else {
            Write-Warn2 "not installed (run: install.ps1 install)"
        }
        if (Test-Path $BinstubPs1) {
            Write-Ok "binstub present (session-sync.ps1)"
        } elseif (Test-Path $BinstubCmd) {
            Write-Warn2 "only the .cmd binstub is present (missing session-sync.ps1)"
        } else {
            Write-Warn2 "binstub not deployed"
        }
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Write-Ok "scheduled task present"
        } else {
            Write-Warn2 "scheduled task not registered"
        }
    }
}
