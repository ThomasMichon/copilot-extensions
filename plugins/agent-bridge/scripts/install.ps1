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
$Binstub    = Join-Path $LocalBin 'agent-bridge.cmd'
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

# Resolve ssh-manager library path across multiple layouts.
# Returns the path string, or $null if not found.
function Resolve-SshManager {
    # 1. Vendored inside agent-bridge (marketplace install layout)
    $candidate = Join-Path $PluginDir 'libs\ssh-manager'
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    # 2. Relative path (git checkout layout)
    $candidate = Join-Path $PluginDir '..\..\libs\ssh-manager'
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
    lib = os.path.join(p, 'libs', 'ssh-manager')
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
    $candidate = Join-Path $env:USERPROFILE 'src\copilot-extensions\libs\ssh-manager'
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    return $null
}

# Check if ssh-manager is already importable in the venv.
function Test-SshManagerInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from ssh_manager import SSHProfileSource, get_default_manager' 2>$null
    return $LASTEXITCODE -eq 0
}

function Get-AgentBridgeBin {
    $p = Join-Path $VenvDir 'Scripts\agent-bridge.exe'
    if (Test-Path $p) { return $p }
    $p = Join-Path $VenvDir 'bin/agent-bridge'
    if (Test-Path $p) { return $p }
    return $null
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
    # Fallback: find by executable path
    $exe = Join-Path $VenvDir 'Scripts\agent-bridge.exe'
    if (Test-Path $exe) {
        $proc = Get-Process | Where-Object { $_.Path -eq $exe } | Select-Object -First 1
        if ($proc) { return $proc }
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

function Write-DeployManifest {
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $repoRoot = Split-Path $PluginDir
    $gitInfo = Get-GitInfo -Path $repoRoot

    # Read version from pyproject.toml
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }

    $manifest = [ordered]@{
        schema_version = 2
        service        = 'agent-bridge'
        installer      = 'plugin'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = $env:COMPUTERNAME.ToLower()
        runtime_source = [ordered]@{
            repo    = 'copilot-extensions'
            plugin  = 'agent-bridge'
            version = $ver
            commit  = $gitInfo.commit
            branch  = $gitInfo.branch
            dirty   = $gitInfo.dirty
            path    = ($PluginDir -replace '\\', '/')
        }
    }

    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8
    Write-Ok "Deploy manifest written"
}

function Register-ScheduledTask_ {
    $agentBridge = Get-AgentBridgeBin
    if (-not $agentBridge) {
        Write-Warn "agent-bridge binary not found -- skipping scheduled task"
        return
    }

    # Create launcher script
    $launcherPath = Join-Path $InstallDir 'start-agent-bridge.ps1'
    @"
# Start agent-bridge service -- called by scheduled task at logon.
`$agentBridge = '$($agentBridge -replace "'", "''")'
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

`$proc = Start-Process -FilePath `$agentBridge -ArgumentList 'start' ``
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

    $action = New-ScheduledTaskAction `
        -Execute $pwshPath `
        -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`""

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

    # Create venv via uv
    if (-not (Test-Path $VenvPython)) {
        Write-Step 'Creating venv via uv...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & uv venv $VenvDir --python 3.10 --allow-existing 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "Failed to create venv at $VenvDir"
                exit 1
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

    # Install package via uv (ssh-manager library first, then agent-bridge)
    Write-Step 'Installing agent-bridge package...'
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

    # Create binstub
    $agentBridge = Get-AgentBridgeBin
    if ($agentBridge) {
        $stubContent = "@echo off`r`nset `"PYTHONUTF8=1`"`r`n`"$agentBridge`" %*"
        [System.IO.File]::WriteAllText($Binstub, $stubContent)
        Write-Ok "Binstub: $Binstub"
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

    if (Test-Path $Binstub) {
        Remove-Item -Force $Binstub
        Write-Ok 'Binstub removed'
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
    $agentBridge = Get-AgentBridgeBin
    if (-not $agentBridge) {
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
`$p = Start-Process -FilePath '$($agentBridge -replace "'", "''")' -ArgumentList 'start' -NoNewWindow -PassThru -RedirectStandardOutput '$($logFile -replace "'", "''")' -RedirectStandardError '$($errFile -replace "'", "''")'
Set-Content -Path '$($PidFile -replace "'", "''")' -Value `$p.Id
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($inner))

    Start-Process -FilePath (Get-PwshPath) `
        -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-EncodedCommand', $encoded) `
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

    $agentBridge = Get-AgentBridgeBin
    if ($agentBridge) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $version = & $agentBridge version 2>$null
        $ErrorActionPreference = $prevEAP
        Write-Ok "Installed: $version"
    } else {
        Write-Step 'Not installed'
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
    if (-not $agentBridge) {
        exit 1
    }
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

    # Repair venv if python binary is missing
    if (-not (Test-Path $VenvPython)) {
        if (Test-Path $VenvDir) {
            Write-Step 'Repairing venv (python binary missing)...'
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & uv venv $VenvDir --python 3.10 --allow-existing 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
            }
            $ErrorActionPreference = $prevEAP
            if (-not (Test-Path $VenvPython)) {
                Write-Fail 'Venv repair failed'
                exit 1
            }
            Write-Ok 'Venv repaired'
        } else {
            Write-Fail 'agent-bridge not installed. Run: install.ps1 install'
            exit 1
        }
    }

    # Stop running instance to avoid file locks
    $wasRunning = $null -ne (Get-RunningProcess)
    if ($wasRunning) {
        Invoke-Stop
    }

    # Reinstall package via uv (ssh-manager + agent-bridge)
    Write-Step 'Updating agent-bridge package...'
    $SshManagerDir = Resolve-SshManager
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($SshManagerDir) {
        $sshOut = & uv pip install --python $VenvPython --reinstall-package ssh-manager `
            "$SshManagerDir" --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "ssh-manager update failed (exit $LASTEXITCODE)"
            if ($sshOut) { Write-Host ($sshOut | Out-String) }
            throw 'ssh-manager update failed'
        }
    } elseif (Test-SshManagerInstalled) {
        Write-Step 'ssh-manager already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    $bridgeOut = & uv pip install --python $VenvPython --reinstall-package agent-bridge `
        "$PluginDir" --quiet 2>&1
    $updateResult = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($updateResult -ne 0) {
        Write-Fail "Package update failed (exit $updateResult)"
        if ($bridgeOut) { Write-Host ($bridgeOut | Out-String) }
        throw 'Package update failed'
    }
    Write-Ok 'Package updated'

    # Update sibling plugins (e.g. agent-codespaces for codespace: namespace)
    Install-SiblingPlugins -Reinstall

    # Update binstub
    $agentBridge = Get-AgentBridgeBin
    if ($agentBridge) {
        $stubContent = "@echo off`r`nset `"PYTHONUTF8=1`"`r`n`"$agentBridge`" %*"
        [System.IO.File]::WriteAllText($Binstub, $stubContent)
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
