<#
.SYNOPSIS
    Agent Bridge -- plugin installer for Windows.

.DESCRIPTION
    Manages the agent-bridge service lifecycle: install, uninstall, start, stop,
    status, update.

    Runtime lives at ~/.agent-bridge/ (venv, config, DB, auth).
    Binstub goes to ~/.local/bin/agent-bridge.cmd.

    Run from the plugin directory or via the Copilot CLI plugin mechanism:
      pwsh -File plugins\agent-bridge\scripts\install.ps1 install
      pwsh -File plugins\agent-bridge\scripts\install.ps1 status
      pwsh -File plugins\agent-bridge\scripts\install.ps1 update

    On first install, detects and migrates from a legacy project-service
    installer (services/agent-bridge/install.ps1) if present, preserving
    config, auth, and DB.

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER Purge
    On uninstall: also delete config, DB, and auth token.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'status', 'update')]
    [string]$Action = 'status',

    [switch]$Purge
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# -- Output helpers (PS5-safe) -----------------------------------------------

function Write-Ok   { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Step { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }

# -- Paths -------------------------------------------------------------------

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$InstallDir = Join-Path $env:USERPROFILE '.agent-bridge'
$VenvDir    = Join-Path $InstallDir 'venv'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$BinstubCmd = Join-Path $LocalBin 'agent-bridge.cmd'
$BinstubPs1 = Join-Path $LocalBin 'agent-bridge.ps1'
$Binstub    = $BinstubPs1   # primary entry point (shown in summaries)
$PidFile    = Join-Path $InstallDir 'agent-bridge.pid'
$TaskName   = 'Agent Bridge'
$Port       = 9280
$RelayPort  = 9857   # integrated credential relay (in-process with the bridge)

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}

# -- Helpers -----------------------------------------------------------------

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

# Resolve a vendored library path (libs\<LibName>) across multiple layouts.
# Returns the path string, or $null if not found.
function Resolve-VendoredLib {
    param([Parameter(Mandatory)][string]$LibName)
    # 1. Vendored inside agent-bridge (marketplace install layout)
    $candidate = Join-Path $PluginDir "libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    # 2. Relative path (git checkout layout)
    $candidate = Join-Path $PluginDir "..\..\libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    # 3. Git repo registry (~/.git-repos) -- use Python for safe YAML parsing
    $gitRepos = Join-Path $env:USERPROFILE '.git-repos'
    if (Test-Path $gitRepos) {
        try {
            $result = & python3 -c @"
import pathlib, os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
reg = yaml.safe_load(pathlib.Path.home().joinpath('.git-repos').read_text())
repo = (reg or {}).get('repos', {}).get('copilot-extensions', {})
if repo:
    p = repo.get('path', os.path.join(reg.get('srcroot', ''), 'copilot-extensions'))
    p = os.path.expanduser(p)
    lib = os.path.join(p, 'libs', '$LibName')
    if os.path.isfile(os.path.join(lib, 'pyproject.toml')):
        print(lib)
        raise SystemExit(0)
raise SystemExit(1)
"@ 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) {
                return $result.Trim()
            }
        } catch { }
    }

    # 4. Common checkout path (repo exists but registry absent/stale)
    $candidate = Join-Path $env:USERPROFILE "src\copilot-extensions\libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    return $null
}

# Resolve the ssh-manager / credential-relay vendored libs (thin wrappers).
function Resolve-SshManager { return (Resolve-VendoredLib -LibName 'ssh-manager') }
function Resolve-CredentialRelay { return (Resolve-VendoredLib -LibName 'credential-relay') }

# Check if ssh-manager is already importable in the venv.
function Test-SshManagerInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from ssh_manager import SSHProfileSource, get_default_manager' 2>$null
    return $LASTEXITCODE -eq 0
}

# Check if credential-relay is already importable in the venv.
function Test-CredentialRelayInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from credential_relay import RelayBuilder' 2>$null
    return $LASTEXITCODE -eq 0
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

# Install sibling plugin packages (e.g. agent-codespaces) into the bridge venv.
# This provides the `codespace:` namespace resolver and credential relay that
# agent-bridge imports at startup. The package is installed for IMPORT ONLY --
# the canonical agent-codespaces CLI binstub is owned by ~/.agent-codespaces via
# its own installer. A missing sibling is non-fatal but WARNED loudly, because
# it disables codespace support.
function Install-SiblingPlugins {
    param(
        [switch]$Reinstall
    )
    $pluginsRoot = Split-Path $PluginDir
    $siblings = @('agent-codespaces', 'agent-containers')
    foreach ($name in $siblings) {
        $sibDir = Join-Path $pluginsRoot $name
        if (-not (Test-Path (Join-Path $sibDir 'pyproject.toml'))) {
            # Also check marketplace vendor layout
            $sibDir = Join-Path $PluginDir "plugins\$name"
            if (-not (Test-Path (Join-Path $sibDir 'pyproject.toml'))) {
                Write-Warn "Sibling plugin '$name' not found -- its namespace resolver / relay will be UNAVAILABLE."
                Write-Warn "  Install it from the marketplace: copilot plugin install $name@copilot-extensions"
                continue
            }
        }
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        if ($Reinstall) {
            $pkgName = $name -replace '-', '_'
            $out = & uv pip install --python $VenvPython --reinstall-package $pkgName `
                "$sibDir" --quiet 2>&1
        } else {
            $out = & uv pip install --python $VenvPython "$sibDir" --quiet 2>&1
        }
        $result = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($result -eq 0) {
            Write-Ok "Sibling plugin (relay import): $name"
        } else {
            Write-Warn "Sibling plugin $name install failed -- its namespace resolver / relay will be UNAVAILABLE."
            if ($out) { Write-Host ($out | Out-String) }
        }
    }
}

# Sibling plugin binstubs (e.g. agent-codespaces) are owned by their own
# installer (~/.agent-codespaces), not by agent-bridge. Bridge uninstall must
# leave them in place. Kept as a no-op for clarity / future siblings.
function Remove-SiblingBinstubs {
    Write-Step "Leaving sibling CLI binstubs in place (owned by their own installers)"
}

function Get-RunningProcess {
    # Try PID file first
    if (Test-Path $PidFile) {
        $pid_ = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($pid_) {
            $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
            if ($proc) { return $proc }
            Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        }
    }
    # Fallback: find by executable path. The service now runs as the venv's
    # python.exe (`-m agent_bridge`); match that. Legacy installs that still ran
    # the agent-bridge.exe trampoline are also matched for clean migration.
    foreach ($exe in @($VenvPython, (Join-Path $VenvDir 'Scripts\agent-bridge.exe'))) {
        if ($exe -and (Test-Path $exe)) {
            $proc = Get-Process | Where-Object { $_.Path -eq $exe } | Select-Object -First 1
            if ($proc) { return $proc }
        }
    }
    # Last resort: find by port binding (catches orphaned processes
    # whose PID file was lost or exe path changed during update)
    $conn = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' } |
        Select-Object -First 1
    if ($conn) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) { return $proc }
    }
    return $null
}

function Test-HealthCheck {
    $retries = 5
    for ($i = 1; $i -le $retries; $i++) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/health" `
                -TimeoutSec 2 -ErrorAction Stop
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
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

function Write-DeployManifest {
    Write-DeployManifestFor -Service 'agent-bridge' -Plugin 'agent-bridge' `
        -InstallPath $InstallDir -PluginPath $PluginDir -VenvPath $VenvDir
}

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

function Register-ScheduledTask_ {
    if (-not (Test-Path $VenvPython)) {
        Write-Warn "agent-bridge venv not found -- skipping scheduled task"
        return
    }

    # Create launcher script
    $launcherPath = Join-Path $InstallDir 'start-agent-bridge.ps1'
    @"
# Start agent-bridge service -- called by scheduled task at logon.
# Launch via the venv's signed python (-m), never the unsigned console-script
# trampoline .exe -- Smart App Control blocks unsigned, zero-reputation exes.
`$launchPy = '$($VenvPython -replace "'", "''")'
`$pidFile = '$($PidFile -replace "'", "''")'
`$logFile = Join-Path (Split-Path `$pidFile) 'agent-bridge.log'
`$errFile = Join-Path (Split-Path `$pidFile) 'agent-bridge-err.log'

if (Test-Path `$pidFile) {
    `$existingPid = Get-Content `$pidFile -ErrorAction SilentlyContinue
    if (`$existingPid) {
        `$proc = Get-Process -Id `$existingPid -ErrorAction SilentlyContinue
        if (`$proc -and -not `$proc.HasExited) { exit 0 }
    }
}

`$proc = Start-Process -FilePath `$launchPy -ArgumentList '-m','agent_bridge','start' ``
    -NoNewWindow -PassThru ``
    -RedirectStandardOutput `$logFile ``
    -RedirectStandardError `$errFile
Set-Content -Path `$pidFile -Value `$proc.Id
"@ | Set-Content -Path $launcherPath -Encoding UTF8

    $pwshPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
    if (-not (Test-Path $pwshPath)) {
        $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        $pwshPath = if ($pwshCmd) { $pwshCmd.Source } else { 'pwsh.exe' }
    }

    # Use conhost --headless to prevent Windows Terminal from capturing the
    # task's pwsh as a visible window/tab when Terminal is the default terminal
    # app. -WindowStyle Hidden alone is ignored by Windows Terminal, so a bare
    # `pwsh -WindowStyle Hidden` task surfaces a real console window -- and
    # because the launcher spawns the long-lived python.exe (-m agent_bridge)
    # with -NoNewWindow, that window persists for the life of the service.
    $action = New-ScheduledTaskAction `
        -Execute 'conhost.exe' `
        -Argument "--headless `"$pwshPath`" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`""

    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT15S'

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Set-ScheduledTask -TaskName $TaskName `
            -Action $action -Trigger $trigger -Settings $settings | Out-Null
        Write-Ok "Scheduled task updated (at logon, 15s delay)"
    } else {
        Register-ScheduledTask -TaskName $TaskName `
            -Action $action -Trigger $trigger -Settings $settings `
            -Description 'Agent-Bridge -- inter-agent communication service on port 9280.' `
            | Out-Null
        Write-Ok "Scheduled task registered (at logon, 15s delay)"
    }
}

function Invoke-MigrationCheck {
    <# Detect and handle migration from a legacy project-service installer. #>
    $oldManifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $oldManifest)) { return }

    try {
        $manifest = Get-Content $oldManifest -Raw | ConvertFrom-Json
        if ($manifest.installer_path -and $manifest.installer_path -like '*services/agent-bridge*') {
            Write-Step "Migrating from legacy project-service installer"
            Write-Step "  Preserving config, auth, and DB"

            # Stop old instance if running
            $proc = Get-RunningProcess
            if ($proc) {
                Write-Step "  Stopping running instance (pid=$($proc.Id))"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
            }

            # Remove old scheduled task if it exists (it may have been registered
            # by the legacy project-service installer)
            $oldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($oldTask) {
                Write-Step "  Re-registering scheduled task (plugin-owned)"
            }

            Write-Ok "Migration from legacy project-service installer detected"
        }
    } catch { }
}

# -- Actions -----------------------------------------------------------------

function Write-Binstubs {
    <# Deploy the agent-bridge CLI binstubs into ~/.local/bin.

       Primary: agent-bridge.ps1. PowerShell resolves a .ps1 (ExternalScript)
       ahead of a .cmd (Application) in the same directory, and `@args`
       forwards the argument array to python verbatim -- quotes, &&, |, ;, and
       ! in `send` / `--remote-cmd` payloads arrive intact. A .cmd forwarding
       %* re-tokenizes the command line and mangles (and can inject) those
       metacharacters; setlocal/enabledelayedexpansion does not fix it.

       Fallback: agent-bridge.cmd, for non-PowerShell callers (cmd.exe or a
       bare CreateProcess/PATHEXT spawn) that cannot resolve a .ps1. It never
       shadows the .ps1 for PowerShell callers when both sit in the same dir.

       Both launch the venv's PSF-signed python via `-m`, never the unsigned
       console-script trampoline .exe that Smart App Control blocks (3077). #>
    param([Parameter(Mandatory)][string]$PythonExe)

    $ps1 = "`$env:PYTHONUTF8 = '1'`r`n& `"$PythonExe`" -m agent_bridge @args`r`nexit `$LASTEXITCODE"
    [System.IO.File]::WriteAllText($BinstubPs1, $ps1, (New-Object System.Text.UTF8Encoding($false)))

    $cmd = "@echo off`r`nset `"PYTHONUTF8=1`"`r`n`"$PythonExe`" -m agent_bridge %*"
    [System.IO.File]::WriteAllText($BinstubCmd, $cmd)

    Write-Ok "Binstub: $BinstubPs1 (+ .cmd fallback)"
}

function Invoke-Install {
    Write-Host ''
    Write-Host '=== agent-bridge install ===' -ForegroundColor Cyan
    Write-Host ''

    # Prerequisite: uv
    try { uv --version 2>&1 | Out-Null } catch {
        Write-Fail 'uv not found on PATH (required for venv + package management)'
        Write-Fail 'Install: https://docs.astral.sh/uv/getting-started/installation/'
        exit 1
    }

    # Check for migration from old installer
    Invoke-MigrationCheck

    # Create directories
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }

    # Create venv (signed base python where available, so it is SAC-trusted)
    if (-not (Test-Path $VenvPython)) {
        Write-Step 'Creating venv...'
        if (-not (New-SignedVenv)) {
            Write-Fail "Failed to create venv at $VenvDir"
            exit 1
        }
        Write-Ok 'Venv created'
    } else {
        # Rebuild in place if the existing venv python is unsigned (SAC).
        if (-not (New-SignedVenv)) {
            Write-Fail "Venv unavailable at $VenvDir"
            exit 1
        }
        Write-Skip 'Venv ready'
    }

    # Install package via uv (ssh-manager library first, then agent-bridge)
    Write-Step 'Installing agent-bridge package...'
    # Pre-strip any locked console-script trampoline so uv can overwrite it
    # (Windows denies overwriting an in-use .exe -- os error 5).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    $SshManagerDir = Resolve-SshManager
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($SshManagerDir) {
        $sshOut = & uv pip install --python $VenvPython "$SshManagerDir" --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "ssh-manager install failed (exit $LASTEXITCODE)"
            if ($sshOut) { Write-Host ($sshOut | Out-String) }
            throw 'ssh-manager install failed'
        }
    } elseif (Test-SshManagerInstalled) {
        Write-Step 'ssh-manager already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # credential-relay (the relay framework agent-bridge runs in its daemon).
    $CredRelayDir = Resolve-CredentialRelay
    if ($CredRelayDir) {
        $crOut = & uv pip install --python $VenvPython "$CredRelayDir" --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "credential-relay install failed (exit $LASTEXITCODE)"
            if ($crOut) { Write-Host ($crOut | Out-String) }
            throw 'credential-relay install failed'
        }
    } elseif (Test-CredentialRelayInstalled) {
        Write-Step 'credential-relay already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    $bridgeOut = & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1
    $installResult = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($installResult -ne 0) {
        Write-Fail "Package install failed (exit $installResult)"
        if ($bridgeOut) { Write-Host ($bridgeOut | Out-String) }
        throw 'Package install failed'
    }
    Write-Ok 'Package installed'

    # Install sibling plugins (e.g. agent-codespaces for codespace: namespace)
    Install-SiblingPlugins

    # Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused);
    # also clears sibling agent-*.exe pulled into this venv by Install-SiblingPlugins.
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # Create binstub -- launch via the venv's signed python (`-m`), never the
    # unsigned console-script trampoline .exe (Smart App Control blocks it).
    if (Test-Path $VenvPython) {
        Write-Binstubs -PythonExe $VenvPython
    }

    # Generate default config
    if (Test-Path $VenvPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c "from agent_bridge.config import load_config, write_default_config; write_default_config(load_config())" 2>$null
        $ErrorActionPreference = $prevEAP
    }

    # Register scheduled task
    Register-ScheduledTask_

    # Write deploy manifest
    Write-DeployManifest

    # Ensure ~/.local/bin is on user PATH
    $userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if ($userPath -and $userPath -notlike "*$LocalBin*") {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$userPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "Added $LocalBin to user PATH"
    }

    Write-Host ''
    Write-Ok 'agent-bridge installed'
    Write-Host "  Install dir: $InstallDir"
    Write-Host "  Binstub:     $Binstub"
    Write-Host "  Config:      agent-bridge config show"
    Write-Host "  API:         http://127.0.0.1:$Port"

    # Start service and verify health
    Write-Host ''
    Write-Step 'Starting service after install...'
    Invoke-Start
}

function Invoke-Uninstall {
    Write-Host ''
    Write-Host '=== agent-bridge uninstall ===' -ForegroundColor Cyan
    Write-Host ''

    Invoke-Stop

    # Remove scheduled task
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Scheduled task removed'
    }

    foreach ($stub in @($BinstubPs1, $BinstubCmd)) {
        if (Test-Path $stub) {
            Remove-Item -Force $stub
            Write-Ok "Binstub removed: $stub"
        }
    }

    Remove-SiblingBinstubs

    if (Test-Path $VenvDir) {
        Remove-Item -Recurse -Force $VenvDir
        Write-Ok 'Venv removed'
    }

    if ($Purge -and (Test-Path $InstallDir)) {
        Write-Warn 'Purging config, DB, and auth'
        Remove-Item -Recurse -Force $InstallDir
    } else {
        Write-Skip "Preserved config/DB at $InstallDir (use -Purge to remove)"
    }

    Write-Ok 'agent-bridge uninstalled'
}

function Get-PwshPath {
    $pwshPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
    if (-not (Test-Path $pwshPath)) {
        $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        $pwshPath = if ($pwshCmd) { $pwshCmd.Source } else { 'powershell.exe' }
    }
    return $pwshPath
}

function Invoke-Start {
    if (-not (Test-Path $VenvPython)) {
        Write-Fail 'agent-bridge not installed. Run: install.ps1 install'
        exit 1
    }

    # Check if already running
    $proc = Get-RunningProcess
    if ($proc) {
        Write-Warn "agent-bridge is already running (pid=$($proc.Id))"
        return
    }

    Write-Step 'Starting agent-bridge...'

    $logFile = Join-Path $InstallDir 'agent-bridge.log'
    $errFile = Join-Path $InstallDir 'agent-bridge-err.log'

    # Start the service through a DETACHED, hidden pwsh launched via
    # ShellExecute (no -NoNewWindow / no redirection on THIS call, so handles
    # are NOT inherited from the installer). That inner pwsh does the redirected
    # Start-Process and records the pid. Without this indirection the long-lived
    # uvicorn server inherits the installer's std handles; when install.ps1 is
    # run with its output redirected or piped, the server holds that handle open
    # and the installer appears to hang after "Update complete".
    $inner = @"
`$p = Start-Process -FilePath '$($VenvPython -replace "'", "''")' -ArgumentList '-m','agent_bridge','start' -NoNewWindow -PassThru -RedirectStandardOutput '$($logFile -replace "'", "''")' -RedirectStandardError '$($errFile -replace "'", "''")'
Set-Content -Path '$($PidFile -replace "'", "''")' -Value `$p.Id
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($inner))

    # Launch the detached, hidden pwsh through conhost --headless so Windows
    # Terminal (when configured as the default terminal app) cannot capture it
    # as a visible window/tab -- -WindowStyle Hidden alone is ignored by the
    # DefTerm handoff. ShellExecute (no -NoNewWindow / no redirection on THIS
    # call) is preserved so the long-lived python.exe (-m agent_bridge) does not
    # inherit the installer's std handles; it inherits conhost's headless pseudoconsole.
    $pwshForHeadless = Get-PwshPath
    Start-Process -FilePath 'conhost.exe' `
        -ArgumentList @('--headless', "`"$pwshForHeadless`"", '-NoProfile', '-WindowStyle', 'Hidden', '-EncodedCommand', $encoded) `
        -WindowStyle Hidden | Out-Null

    # The detached launcher writes the pid file once the service is spawned.
    $rp = $null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        $rp = Get-RunningProcess
        if ($rp) { break }
    }

    if ($rp) {
        if (Test-HealthCheck) {
            Write-Ok "agent-bridge started (pid=$($rp.Id), port=$Port)"
        } else {
            Write-Warn "agent-bridge started (pid=$($rp.Id)) but health check failed -- check agent-bridge.log"
        }
    } else {
        Write-Fail 'agent-bridge failed to start -- check agent-bridge.log'
        exit 1
    }
}

function Invoke-Stop {
    $proc = Get-RunningProcess
    if (-not $proc) {
        Write-Skip 'agent-bridge not running'
        return
    }

    Write-Step "Stopping agent-bridge (pid=$($proc.Id))..."
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue

    # Wait up to 10s for process to exit and release the port
    $waited = 0
    while ($waited -lt 10) {
        Start-Sleep -Seconds 1
        $waited++
        $check = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $check -or $check.HasExited) { break }
    }

    $check = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if ($check -and -not $check.HasExited) {
        Write-Fail "Process did not stop cleanly"
        return
    }

    # Verify port is actually free (catches orphaned child processes)
    $portInUse = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' }
    if ($portInUse) {
        Write-Warn "Port $Port still in use after stop -- killing occupant (pid=$($portInUse.OwningProcess))"
        Stop-Process -Id $portInUse.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    # Also ensure the integrated credential relay is down. It runs in-process
    # with the bridge (so the kill above usually frees it), but free the port
    # explicitly to catch an orphaned relay.
    $relayInUse = Get-NetTCPConnection -LocalPort $RelayPort -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' }
    if ($relayInUse) {
        Write-Warn "Credential relay port $RelayPort still in use -- killing occupant (pid=$($relayInUse.OwningProcess))"
        Stop-Process -Id $relayInUse.OwningProcess -Force -ErrorAction SilentlyContinue
    }

    Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
    Write-Ok 'agent-bridge stopped'
}

function Invoke-Status {
    $running = $false
    $proc = Get-RunningProcess
    if ($proc) {
        Write-Ok "agent-bridge is running (pid=$($proc.Id))"
        $running = $true

        if (Test-HealthCheck) {
            Write-Ok "Health check passed (port $Port)"
        } else {
            Write-Warn "Process running but health check failed"
        }
    } else {
        Write-Step 'agent-bridge is not running'
    }

    if (Test-Path $VenvPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $version = & $VenvPython -m agent_bridge version 2>$null
        $ErrorActionPreference = $prevEAP
        Write-Ok "Installed: $version"
    } else {
        Write-Step 'Not installed'
    }

    # Show runtime source footprint (local checkout vs marketplace)
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            if ($m.source) {
                $extra = ''
                if ($m.source.kind -eq 'local' -and $m.source.commit) {
                    $extra = " @ $($m.source.commit)$(if ($m.source.dirty) { '+dirty' })"
                }
                Write-Ok "Source: $($m.source.kind) ($($m.source.version))$extra"
            }
        } catch { }
    }

    # Show config summary
    if (Test-Path (Join-Path $InstallDir 'config.yaml')) {
        Write-Ok "Config: $(Join-Path $InstallDir 'config.yaml')"
    }

    # Show scheduled task
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Ok "Scheduled task: $($task.State)"
    } else {
        Write-Step 'No scheduled task registered'
    }

    # Exit non-zero when not installed (used by module update orchestrator)
    if (-not (Test-Path $VenvPython)) {
        exit 1
    }
}

function Test-RuntimeHealthy {
    <# True if the venv python can import the agent-bridge runtime + its key
       deps. Used to (a) decide whether the current venv is worth snapshotting
       and (b) verify a fresh install before declaring the update good (#52).
       Checks uvicorn + credential_relay too -- the exact modules that went
       missing in the observed broken-venv outage. #>
    param([string]$Python)
    if (-not (Test-Path $Python)) { return $false }
    & $Python -c 'import agent_bridge, uvicorn, credential_relay' 2>$null
    return $LASTEXITCODE -eq 0
}

function Backup-Venv {
    <# Snapshot $VenvDir to $VenvDir.bak so a failed update can roll back. Clears
       any stale backup first. Returns $true on success. #>
    $bak = "$VenvDir.bak"
    if (Test-Path $bak) { Remove-Item -Recurse -Force $bak -ErrorAction SilentlyContinue }
    try {
        Copy-Item -Recurse -Force $VenvDir $bak -ErrorAction Stop
        return $true
    } catch {
        Write-Warn "Could not snapshot venv for rollback: $_"
        return $false
    }
}

function Restore-Venv {
    <# Replace a broken $VenvDir with the snapshot at $VenvDir.bak. Returns $true
       on success. #>
    $bak = "$VenvDir.bak"
    if (-not (Test-Path $bak)) { return $false }
    try {
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop }
        Move-Item -Force $bak $VenvDir -ErrorAction Stop
        return $true
    } catch {
        Write-Warn "Restore-Venv failed: $_"
        return $false
    }
}

function Remove-VenvBackup {
    $bak = "$VenvDir.bak"
    if (Test-Path $bak) { Remove-Item -Recurse -Force $bak -ErrorAction SilentlyContinue }
}

function Invoke-Update {
    Write-Host ''
    Write-Host '=== agent-bridge update ===' -ForegroundColor Cyan
    Write-Host ''

    # Prerequisite: uv
    try { uv --version 2>&1 | Out-Null } catch {
        Write-Fail 'uv not found on PATH (required for package management)'
        Write-Fail 'Install: https://docs.astral.sh/uv/getting-started/installation/'
        exit 1
    }

    # Stop running instance first -- a rebuild/repair of the venv (below) must
    # not race a live bridge holding python.exe open.
    $wasRunning = $null -ne (Get-RunningProcess)

    # Snapshot the current healthy venv so a failed install can roll back to the
    # previous-good runtime instead of leaving the service DOWN with a broken/
    # empty venv (#52). Only snapshot a venv that actually works -- no point
    # backing up an already-broken one.
    $haveBackup = $false
    if (Test-RuntimeHealthy $VenvPython) {
        $haveBackup = Backup-Venv
    }

    try {
        if ($wasRunning) {
            Invoke-Stop
        }

        # Repair venv if python binary is missing (or rebuild if unsigned for SAC)
        if ((-not (Test-Path $VenvPython)) -or ($env:OS -eq 'Windows_NT')) {
            if ((Test-Path $VenvDir) -or (Get-SignedBasePython)) {
                if (-not (Test-Path $VenvPython)) { Write-Step 'Repairing venv (python binary missing)...' }
                if (-not (New-SignedVenv)) {
                    throw 'Venv repair failed'
                }
                if (-not (Test-Path $VenvPython)) {
                    throw 'Venv repair failed'
                }
                Write-Ok 'Venv repaired'
            } else {
                throw 'agent-bridge not installed. Run: install.ps1 install'
            }
        }

        # Reinstall package via uv (ssh-manager + credential-relay + agent-bridge)
        Write-Step 'Updating agent-bridge package...'
        # Pre-strip any locked console-script trampoline so uv can overwrite it
        # (Windows denies overwriting an in-use .exe -- os error 5).
        Remove-ConsoleTrampolines -VenvDir $VenvDir
        $SshManagerDir = Resolve-SshManager
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        if ($SshManagerDir) {
            $sshOut = & uv pip install --python $VenvPython --reinstall-package agent-ssh-manager `
                "$SshManagerDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($sshOut) { Write-Host ($sshOut | Out-String) }
                throw "ssh-manager update failed (exit $LASTEXITCODE)"
            }
        } elseif (Test-SshManagerInstalled) {
            Write-Step 'ssh-manager already installed in venv (marketplace layout)'
        } else {
            throw 'Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
        }
        # credential-relay: force-reinstall so a local code change propagates even
        # without a version bump (uv otherwise skips a same-version path dep).
        $CredRelayDir = Resolve-CredentialRelay
        if ($CredRelayDir) {
            $crOut = & uv pip install --python $VenvPython --reinstall-package agent-credential-relay `
                "$CredRelayDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($crOut) { Write-Host ($crOut | Out-String) }
                throw "credential-relay update failed (exit $LASTEXITCODE)"
            }
        } elseif (Test-CredentialRelayInstalled) {
            Write-Step 'credential-relay already installed in venv (marketplace layout)'
        } else {
            throw 'Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
        }
        $bridgeOut = & uv pip install --python $VenvPython --reinstall-package agent-bridge `
            "$PluginDir" --quiet 2>&1
        $updateResult = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($updateResult -ne 0) {
            if ($bridgeOut) { Write-Host ($bridgeOut | Out-String) }
            throw "Package update failed (exit $updateResult)"
        }

        # Verify the freshly-installed runtime imports before declaring success.
        # Catches a half-installed venv (e.g. a wheel/dependency gap like #51)
        # while we can still roll back -- rather than starting a broken service.
        if (-not (Test-RuntimeHealthy $VenvPython)) {
            throw 'Post-install verification failed (agent_bridge / uvicorn / credential_relay not importable)'
        }
        Write-Ok 'Package updated'
    }
    catch {
        Write-Fail "Update failed: $_"
        if ($haveBackup) {
            Write-Step 'Rolling back to the previous venv...'
            if (Restore-Venv) {
                Write-Ok 'Previous venv restored'
                if ($wasRunning) {
                    Write-Step 'Restarting the previous service...'
                    Invoke-Start
                }
            } else {
                Write-Fail 'Rollback failed -- run "install.ps1 install" to rebuild the runtime'
            }
        } else {
            Write-Warn 'No healthy venv snapshot to roll back to -- run "install.ps1 install" to rebuild the runtime'
        }
        exit 1
    }

    # Success: discard the rollback snapshot.
    Remove-VenvBackup

    # Update sibling plugins (e.g. agent-codespaces for codespace: namespace)
    Install-SiblingPlugins -Reinstall

    # Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused);
    # also clears sibling agent-*.exe pulled into this venv by Install-SiblingPlugins.
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # Update binstub -- launch via the venv's signed python (`-m`), never the
    # unsigned console-script trampoline .exe (Smart App Control blocks it).
    if (Test-Path $VenvPython) {
        Write-Binstubs -PythonExe $VenvPython
    }

    # Update scheduled task
    Register-ScheduledTask_

    # Update deploy manifest
    Write-DeployManifest

    # (Re)start service -- always ensure running after update
    Write-Step 'Starting service...'
    Invoke-Start

    Write-Ok 'Update complete'
}

# -- Dispatch ----------------------------------------------------------------

switch ($Action) {
    'install'   { Invoke-Install }
    'uninstall' { Invoke-Uninstall }
    'start'     { Invoke-Start }
    'stop'      { Invoke-Stop }
    'status'    { Invoke-Status }
    'update'    { Invoke-Update }
}
