<#
.SYNOPSIS
    Agent Logger -- session-sync installer (Windows).

.DESCRIPTION
    Creates a venv at ~/.agent-logger, installs the agent-logger package, and
    registers a Scheduled Task that runs `session-sync run --prune` every 4
    hours. Windows-first by design: the runtime is the venv's python invoked
    as `python -m agent_logger.sync.engine` (the console-script .exe is not
    relied upon, matching the other plugins' Smart App Control posture).

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

$InstallDir = Join-Path $env:USERPROFILE '.agent-logger'
$VenvDir    = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$TaskName   = 'Agent Logger Session Sync'

function Install-Package {
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    if (-not (Test-Path $LocalBin))   { New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null }
    if (-not (Test-Path $VenvPython)) {
        python -m venv $VenvDir
        Write-Changed "created venv at $VenvDir"
    }
    & $VenvPython -m pip install --quiet --upgrade pip
    & $VenvPython -m pip install --quiet $PluginDir
    Write-Ok "installed agent-logger package"

    # Binstubs: .cmd shims that invoke `python -m` (avoids the SAC-blocked
    # console-script trampolines).
    @{
        'session-sync' = 'agent_logger.sync.engine'
        'agent-logger' = 'agent_logger'
    }.GetEnumerator() | ForEach-Object {
        $cmd = Join-Path $LocalBin "$($_.Key).cmd"
        "@echo off`r`n`"$VenvPython`" -m $($_.Value) %*" | Set-Content -Encoding ASCII $cmd
    }
    Write-Ok "wrote binstubs to $LocalBin"
}

function Register-SyncTask {
    $action = New-ScheduledTaskAction -Execute $VenvPython `
        -Argument '-m agent_logger.sync.engine run --prune'
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours 4)
    $trigger.Repetition.StopAtDurationEnd = $false
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
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
    }
    'status' {
        if (Test-Path $VenvPython) {
            Write-Ok ("installed: " + (& $VenvPython -m agent_logger version))
            & $VenvPython -m agent_logger.sync.engine status
        } else {
            Write-Warn2 "not installed (run: install.ps1 install)"
        }
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Write-Ok "scheduled task present"
        } else {
            Write-Warn2 "scheduled task not registered"
        }
    }
}
