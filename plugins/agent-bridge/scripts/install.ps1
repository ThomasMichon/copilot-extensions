<#
.SYNOPSIS
    Agent Bridge - standardized installer interface for Windows.

.DESCRIPTION
    Manages the agent-bridge service lifecycle: install, uninstall, start, stop,
    status, update.

    Runtime lives at ~/.agent-bridge/ (venv, config, DB, auth).
    Binstub goes to ~/.local/bin/agent-bridge.cmd.

    Run from the repo root:
      pwsh -File plugins\agent-bridge\scripts\install.ps1 install
      pwsh -File plugins\agent-bridge\scripts\install.ps1 status
      pwsh -File plugins\agent-bridge\scripts\install.ps1 update

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

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Paths -------------------------------------------------------------------

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..'))
$InstallDir = Join-Path $env:USERPROFILE '.agent-bridge'
$VenvDir    = Join-Path $InstallDir 'venv'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$Binstub    = Join-Path $LocalBin 'agent-bridge.cmd'
$PidFile    = Join-Path $InstallDir 'agent-bridge.pid'

# -- Helpers -----------------------------------------------------------------

function Write-Status($Prefix, $Message) {
    Write-Host "[$Prefix] $Message"
}

function Get-VenvPython {
    $p = Join-Path $VenvDir 'Scripts\python.exe'
    if (Test-Path $p) { return $p }
    $p = Join-Path $VenvDir 'bin/python'
    if (Test-Path $p) { return $p }
    return $null
}

function Get-VenvPip {
    $p = Join-Path $VenvDir 'Scripts\pip.exe'
    if (Test-Path $p) { return $p }
    $p = Join-Path $VenvDir 'bin/pip'
    if (Test-Path $p) { return $p }
    return $null
}

function Get-AgentBridgeBin {
    $p = Join-Path $VenvDir 'Scripts\agent-bridge.exe'
    if (Test-Path $p) { return $p }
    $p = Join-Path $VenvDir 'bin/agent-bridge'
    if (Test-Path $p) { return $p }
    return $null
}

# -- Actions -----------------------------------------------------------------

function Invoke-Install {
    Write-Status 'agent-bridge' 'Installing'

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null

    if (-not (Test-Path $VenvDir)) {
        Write-Status 'agent-bridge' "Creating venv at $VenvDir"
        & python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create venv' }
    }

    $pip = Get-VenvPip
    if (-not $pip) { throw 'pip not found in venv' }

    Write-Status 'agent-bridge' 'Installing package'
    & $pip install --quiet --upgrade pip
    & $pip install --quiet -e "$PluginDir"
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

    # Create binstub
    $agentBridge = Get-AgentBridgeBin
    if ($agentBridge) {
        Set-Content -Path $Binstub -Value "@`"$agentBridge`" %*" -Encoding ASCII
        Write-Status 'agent-bridge' "Binstub: $Binstub"
    }

    # Generate default config
    $venvPython = Get-VenvPython
    if ($venvPython) {
        & $venvPython -c "from agent_bridge.config import load_config, write_default_config; write_default_config(load_config())" 2>$null
    }

    Write-Status 'OK' 'agent-bridge installed'
    Write-Status 'agent-bridge' "Run 'agent-bridge start' to start the service"
}

function Invoke-Uninstall {
    Write-Status 'agent-bridge' 'Uninstalling'

    Invoke-Stop

    if (Test-Path $Binstub) {
        Remove-Item -Force $Binstub
    }

    if (Test-Path $VenvDir) {
        Remove-Item -Recurse -Force $VenvDir
        Write-Status 'agent-bridge' 'Removed venv'
    }

    if ($Purge -and (Test-Path $InstallDir)) {
        Write-Status 'agent-bridge' 'Purging config, DB, and auth'
        Remove-Item -Recurse -Force $InstallDir
    } else {
        Write-Status 'agent-bridge' "Preserved config/DB at $InstallDir (use -Purge to remove)"
    }

    Write-Status 'OK' 'agent-bridge uninstalled'
}

function Invoke-Start {
    $agentBridge = Get-AgentBridgeBin
    if (-not $agentBridge) {
        Write-Status 'FAIL' 'agent-bridge not installed. Run: install.ps1 install'
        exit 1
    }

    Write-Status 'agent-bridge' 'Starting'
    $proc = Start-Process -FilePath $agentBridge -ArgumentList 'start' `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $InstallDir 'agent-bridge.log') `
        -RedirectStandardError (Join-Path $InstallDir 'agent-bridge-err.log')

    Set-Content -Path $PidFile -Value $proc.Id
    Start-Sleep -Seconds 1

    if (-not $proc.HasExited) {
        Write-Status 'OK' "agent-bridge started (pid=$($proc.Id))"
    } else {
        Write-Status 'FAIL' "agent-bridge failed to start -- check agent-bridge.log"
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        exit 1
    }
}

function Invoke-Stop {
    if (Test-Path $PidFile) {
        $pid = Get-Content $PidFile
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Status 'agent-bridge' "Stopping (pid=$pid)"
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        Write-Status 'OK' 'agent-bridge stopped'
    } else {
        Write-Status 'agent-bridge' 'Not running'
    }
}

function Invoke-Status {
    $running = $false
    if (Test-Path $PidFile) {
        $pid = Get-Content $PidFile
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Status 'OK' "agent-bridge is running (pid=$pid)"
            $running = $true
        } else {
            Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        }
    }
    if (-not $running) {
        Write-Status 'agent-bridge' 'Not running'
    }

    $agentBridge = Get-AgentBridgeBin
    if ($agentBridge) {
        $version = & $agentBridge version 2>$null
        Write-Status 'agent-bridge' "Installed: $version"
    } else {
        Write-Status 'agent-bridge' 'Not installed'
    }
}

function Invoke-Update {
    Write-Status 'agent-bridge' 'Updating'

    $pip = Get-VenvPip
    if (-not $pip) {
        Write-Status 'FAIL' 'agent-bridge not installed. Run: install.ps1 install'
        exit 1
    }

    & $pip install --quiet --upgrade pip
    & $pip install --quiet -e "$PluginDir"
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

    Write-Status 'OK' 'agent-bridge updated'

    # Restart if running
    if (Test-Path $PidFile) {
        $pid = Get-Content $PidFile
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Status 'agent-bridge' 'Restarting service'
            Invoke-Stop
            Invoke-Start
        }
    }
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
