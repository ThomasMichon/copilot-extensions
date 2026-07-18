<#
.SYNOPSIS
    agent-vault installer / lifecycle manager. PS5+ compatible.

.DESCRIPTION
    Canonical installer for the agent-vault runtime. Creates the runtime at
    ~/.agent-vault/ (.venv + state), deploys ~/.local/bin/agent-vault.ps1 and
    agent-vault.cmd binstubs, and registers a windowless Scheduled Task named
    AgentVault that runs the persistent daemon at logon unless -NoService is
    specified.

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-vault).

.PARAMETER NoService
    Install/update the client (venv + binstub) only; do NOT register/start the
    AgentVault Scheduled Task (client-only host).

.PARAMETER Purge
    On uninstall: also delete daemon state under the install directory.

.PARAMETER Force
    On update: bypass the downgrade guard (deliberate rollback). Env:
    AGENT_VAULT_ALLOW_DOWNGRADE=1.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall')]
    [string]$Action = 'install',

    [Alias('install-dir')]
    [string]$InstallDir,

    [Alias('no-service')]
    [switch]$NoService,

    [switch]$Purge,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:AGENT_VAULT_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_vault'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-vault'
}
$VenvDir     = Join-Path $InstallDir '.venv'
$LocalBin    = Join-Path $env:USERPROFILE '.local\bin'
$VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'
$BinstubPs1  = Join-Path $LocalBin 'agent-vault.ps1'
$BinstubCmd  = Join-Path $LocalBin 'agent-vault.cmd'
$Binstub     = $BinstubPs1
$TaskName    = 'AgentVault'
$utf8NoBom   = New-Object System.Text.UTF8Encoding $false

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

function Get-InstalledVersion {
    if (-not (Test-Path $VenvPython)) { return $null }
    try {
        $v = & $VenvPython -c 'from importlib.metadata import version; print(version("agent-vault"))' 2>$null
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
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_VAULT_ALLOW_DOWNGRADE)"
            return
        }
        Write-Host ''
        Write-Fail "Refusing to downgrade agent-vault: installed $installed > source $source"
        Write-Fail 'Override intentionally (deliberate rollback):'
        Write-Fail "    install.ps1 -Action $Action -Force"
        Write-Host ''
        exit 1
    }
}

function Resolve-PythonCommand {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $testOut = & $found.Source --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') { return $found.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
        }
    }
    return $null
}

function Test-KeePassXCCli {
    return ($null -ne (Get-Command keepassxc-cli -ErrorAction SilentlyContinue))
}

function Write-Binstubs {
    param([Parameter(Mandatory)][string]$PythonExe)

    $ps1 = "`$env:PYTHONUTF8 = '1'`r`n& `"$PythonExe`" -m agent_vault @args`r`nexit `$LASTEXITCODE`r`n"
    [System.IO.File]::WriteAllText($BinstubPs1, $ps1, $utf8NoBom)

    $cmd = "@echo off`r`nset `"PYTHONUTF8=1`"`r`n`"$PythonExe`" -m agent_vault %*`r`n"
    [System.IO.File]::WriteAllText($BinstubCmd, $cmd, $utf8NoBom)

    Write-Ok "Binstub: $BinstubPs1 (+ .cmd fallback)"
}

function Write-Manifest {
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    if ($ver -eq '0.0.0') {
        $sourceVersion = Get-SourceVersion
        if ($sourceVersion) { $ver = $sourceVersion }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
        $git = Get-GitInfo -Path $repoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-vault'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-vault'
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

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) {
        Write-Fail "Package source not found at $PkgSrcDir"
        exit 1
    }

    $pythonCmd = Resolve-PythonCommand
    if (-not $pythonCmd) {
        Write-Fail 'Python not found on PATH (need 3.10+)'
        exit 1
    }
    Write-Ok "Python: $pythonCmd"

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    if (-not (New-SignedVenv)) {
        Write-Step 'Creating venv via python -m venv...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
    }
    if (-not (Test-Path $VenvPython)) {
        Write-Fail "Venv creation failed -- $VenvPython not found"
        exit 1
    }
    Write-Ok 'Venv ready'

    Write-Step 'Installing agent-vault package...'
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $pkgOut = & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1
    } else {
        $pkgOut = & $VenvPython -m pip install --quiet "$PluginDir" 2>&1
    }
    $pkgResult = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($pkgResult -ne 0) {
        Write-Fail "Package install failed (exit $pkgResult)"
        if ($pkgOut) { Write-Host ($pkgOut | Out-String) }
        exit 1
    }
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Write-Ok 'Package installed: agent-vault'

    Write-Binstubs -PythonExe $VenvPython
    Write-Manifest

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_vault' 2>$null
    $importOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    if (Test-KeePassXCCli) {
        Write-Ok 'Prerequisite: keepassxc-cli found'
    } else {
        Write-Warn 'Prerequisite missing: keepassxc-cli (KeePassXC). agent-vault installed, but unlocks will fail until KeePassXC is present.'
    }

    $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

function Register-AgentVaultTask {
    if ($NoService) {
        Write-Skip 'agent-vault service skipped (-NoService): this host is a client only'
        return
    }
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- skipping service'
        return
    }
    if (-not (Test-Path $VenvPython)) {
        Write-Warn 'agent-vault venv not found -- skipping scheduled task'
        return
    }

    $action = New-ScheduledTaskAction `
        -Execute 'conhost.exe' `
        -Argument "--headless `"$VenvPython`" -m agent_vault.service --foreground --persistent" `
        -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT15S'
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
        Write-Ok "Scheduled task updated ($TaskName, at logon, 15s delay)"
    } else {
        Register-ScheduledTask -TaskName $TaskName `
            -Action $action -Trigger $trigger -Settings $settings `
            -Description 'agent-vault -- local KeePassXC-backed secret store.' | Out-Null
        Write-Ok "Scheduled task registered ($TaskName, at logon, 15s delay)"
    }

    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Invoke-Install {
    Write-Host ''; Write-Host '=== agent-vault install ===' -ForegroundColor Cyan; Write-Host ''
    Install-Runtime
    Register-AgentVaultTask
    Write-Host ''; Write-Host '=== agent-vault install complete ===' -ForegroundColor Cyan
}

function Invoke-Update {
    Write-Host ''; Write-Host '=== agent-vault update ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-DowngradeGuard
    Install-Runtime
    Register-AgentVaultTask
    Write-Host ''; Write-Host '=== agent-vault update complete ===' -ForegroundColor Cyan
}

function Invoke-Start {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Fail "No AgentVault task installed -- run: install.ps1 -Action install"
        exit 1
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Ok 'agent-vault service started'
}

function Invoke-Stop {
    if (Test-Path $VenvPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -m agent_vault.service --stop 2>$null | Out-Null
        $ErrorActionPreference = $prevEAP
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Ok 'agent-vault service stopped'
    } else {
        Write-Skip 'AgentVault task not installed'
    }
}

function Invoke-Status {
    Write-Host ''; Write-Host '=== agent-vault status ===' -ForegroundColor Cyan
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-Ok "Deployed: $($m.source.version) (source: $($m.source.kind))"
        } catch { Write-Skip 'Deploy manifest unreadable' }
    } else {
        Write-Skip 'No deploy manifest -- not installed?'
    }
    if (Test-Path $BinstubPs1) { Write-Ok "Binstub: $BinstubPs1 (+ .cmd fallback)" }
    elseif (Test-Path $BinstubCmd) { Write-Warn "Only fallback binstub exists: $BinstubCmd" }
    else { Write-Skip "No binstub at $Binstub" }

    if (Test-KeePassXCCli) { Write-Ok 'Prerequisite: keepassxc-cli found' }
    else { Write-Warn 'Prerequisite missing: keepassxc-cli (KeePassXC)' }

    if (Test-Path $VenvPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $ping = & $VenvPython -m agent_vault.service --ping 2>$null
        $pingCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($pingCode -eq 0 -and $ping) { Write-Ok ($ping | Out-String).Trim() }
        else { Write-Skip 'Daemon not responding to ping' }
    } else {
        Write-Skip 'Venv not installed'
    }

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Write-Ok "Scheduled task: $($task.State)" }
    else { Write-Skip 'No AgentVault scheduled task (client-only host)' }
}

function Invoke-Uninstall {
    Write-Host ''; Write-Host '=== agent-vault uninstall ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-Stop
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Scheduled task removed'
    }
    foreach ($stub in @($BinstubPs1, $BinstubCmd)) {
        if (Test-Path $stub) {
            Remove-Item $stub -Force -ErrorAction SilentlyContinue
            Write-Ok "Binstub removed: $stub"
        }
    }
    if ($Purge) {
        if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
        Write-Ok "Runtime purged: $InstallDir"
    } else {
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue }
        Write-Ok 'Venv removed (state kept; -Purge to delete)'
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
