<#
.SYNOPSIS
    Self-elevating, one-time repair for the agent-bridge Windows auto-start
    scheduled task.

.DESCRIPTION
    Routine `install.ps1 update` deliberately never rewrites the scheduled task
    (the decoupled, write-once-bootstrap model -- dotfiles#227): rewriting an
    existing elevated/S4U (boot) task needs elevation, and a failed rewrite used
    to churn or destroy a working auto-start. So when the existing task is broken
    and elevation-locked -- classically an S4U/boot task that never launches
    (`LastTaskResult = 267011` / SCHED_S_TASK_HAS_NOT_RUN) -- it is repaired here,
    explicitly, in one step.

    This script:
      1. Requests elevation via UAC automatically (relaunches itself elevated).
      2. Re-enters the retained installer's provision action after elevation.
      3. Rechecks native scheduling policy, task ownership and startup holds
         under the install lock before repairing the interactive AtLogOn task.

    It deliberately does NOT start the daemon: starting it from this elevated
    context would leave an ELEVATED daemon running (wrong -- the daemon must run
    as the normal user). The daemon self-heals on demand meanwhile (any
    `agent-bridge` command boots it), and the repaired task starts it at the next
    logon. The installer's `provision` action invokes this repair when it detects
    a never-ran S4U task. Run this once; routine updates then leave the healthy
    task untouched.

.NOTES
    The task action and serialized reconciliation are owned by install.ps1.
#>
[CmdletBinding()]
param(
    # Internal: set on the elevated relaunch so the elevated instance does the
    # work instead of prompting again.
    [switch]$Elevated,

    # Force a re-register even when the task is already a healthy non-elevated
    # interactive AtLogOn task (normally that is a no-op that skips UAC).
    [switch]$Force,

    [string]$InstallDir,

    [string]$TaskName
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not $InstallDir) { $InstallDir = if ($env:AGENT_BRIDGE_CONFIG_DIR) {
    $env:AGENT_BRIDGE_CONFIG_DIR
} else {
    Join-Path $env:USERPROFILE '.agent-bridge' # marketplace-isolation: allow legacy-compatibility
} }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$legacyInstallDir = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.agent-bridge'))
$isLegacyInstall = [StringComparer]::OrdinalIgnoreCase.Equals($InstallDir, $legacyInstallDir)
$resolver = Join-Path $InstallDir 'bin\resolve-runtime.ps1'
if (Test-Path -LiteralPath $resolver) {
    $previousRoot = $env:AGENT_RT_ROOT
    try {
        $env:AGENT_RT_ROOT = $InstallDir
        $AgentRtPy = $null
        . $resolver
        if (-not $AgentRtPy) { throw 'Bridge selected runtime is unavailable for task binding' }
        $boundTaskName = & $AgentRtPy -I -B -c `
            'import sys; from pathlib import Path; from agent_bridge.install_paths import scheduled_task_name; print(scheduled_task_name(Path(sys.argv[1])))' `
            $InstallDir
        if ($LASTEXITCODE -ne 0 -or $boundTaskName -isnot [string] -or -not $boundTaskName) {
            throw 'Bridge installation task binding could not be resolved'
        }
        if ($TaskName -and -not [StringComparer]::OrdinalIgnoreCase.Equals($TaskName, $boundTaskName)) {
            throw 'Bridge repair task selector disagrees with the selected installation'
        }
        $TaskName = $boundTaskName
    } finally { $env:AGENT_RT_ROOT = $previousRoot }
} elseif ($isLegacyInstall) {
    if ($TaskName -and $TaskName -ne 'Agent Bridge') {
        throw 'Bridge repair task selector disagrees with the legacy installation'
    }
    $TaskName = 'Agent Bridge'
} elseif (-not $TaskName -or $TaskName -eq 'Agent Bridge') {
    throw 'Scoped Bridge repair requires its installation task name; no legacy task was queried'
}

function Test-IsAdmin {
    try {
        return ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Resolve-PwshPath {
    $p = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
    if (Test-Path -LiteralPath $p) { return $p }
    $cmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return 'pwsh.exe'
}

# ---------------------------------------------------------------------------
# Stage 0: assess -- decide whether any work (and any elevation) is needed.
# ---------------------------------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

function Test-HealthyInteractive($task) {
    if (-not $task) { return $false }
    if ($task.Principal.LogonType -ne 'Interactive') { return $false }
    foreach ($trg in @($task.Triggers)) {
        if ($trg.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger') { return $true }
    }
    return $false
}

# Already the desired state? Then there is nothing to repair -- and, crucially,
# NO elevation/UAC is needed. "Merely using agent-bridge should never prompt for
# UAC" -- so an already-correct (or absent) task must not trigger a prompt.
if ((Test-HealthyInteractive $existing) -and -not $Force) {
    Write-Host "[OK] '$TaskName' is already a healthy non-elevated interactive AtLogOn task -- nothing to repair (no elevation needed)." -ForegroundColor Green
    exit 0
}

# Elevation is required ONLY to remove/rewrite a task whose principal the current
# non-elevated user cannot touch -- i.e. an S4U or Password (boot/headless) task.
# Registering the interactive AtLogOn task, or replacing an existing *interactive*
# one, needs no elevation; nor does creating one when absent.
$needsElevation = $existing -and ($existing.Principal.LogonType -in @('S4U', 'Password'))

# ---------------------------------------------------------------------------
# Stage 1: relaunch under UAC ONLY when elevation is actually needed.
# ---------------------------------------------------------------------------
if ($needsElevation -and -not (Test-IsAdmin)) {
    if ([Environment]::GetCommandLineArgs() -icontains '-NonInteractive') {
        Write-Error 'Scheduled-task repair requires an elevated interactive host; unattended repair left the existing task unchanged.'
        exit 1
    }
    Write-Host "agent-bridge: replacing the existing elevated/S4U '$TaskName' task with the default non-elevated one needs elevation." -ForegroundColor Cyan
    Write-Host "A Windows UAC prompt will appear -- approve it to continue." -ForegroundColor DarkGray

    $exe = (Get-Process -Id $PID).Path
    if (-not $exe) { $exe = Resolve-PwshPath }
    $relaunchArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $PSCommandPath + '"'), '-Elevated',
        '-InstallDir', ('"' + $InstallDir + '"'),
        '-TaskName', ('"' + $TaskName + '"')
    )
    if ($Force) { $relaunchArgs += '-Force' }
    try {
        $proc = Start-Process -FilePath $exe -Verb RunAs -PassThru -Wait -ArgumentList $relaunchArgs
    } catch {
        Write-Warning "Elevation was declined or failed: $($_.Exception.Message.Trim())"
        Write-Warning "No change was made. The daemon still self-heals on demand (any 'agent-bridge' command boots it); the existing task is left as-is."
        exit 1
    }
    if ($proc.ExitCode -eq 0) {
        Write-Host "[OK] Scheduled task '$TaskName' repaired (interactive AtLogOn). It starts at your next logon; the daemon self-heals on demand meanwhile." -ForegroundColor Green
    } else {
        Write-Warning "Repair exited with code $($proc.ExitCode) -- see the elevated window's output above."
    }
    exit $proc.ExitCode
}

# ---------------------------------------------------------------------------
# Stage 2: do the repair (elevated if we needed to be; otherwise non-elevated).
# Never starts the daemon.
# ---------------------------------------------------------------------------
$installer = Join-Path $PSScriptRoot 'install.ps1'
if (-not (Test-Path -LiteralPath $installer)) {
    throw 'Retained Bridge installer is unavailable; task left unchanged'
}
# The elevated provision path does not launch this repair again. It acquires
# its own lock and re-reads current policy after the elevation gap.
& $installer provision -InstallDir $InstallDir -ForceTaskRepair
exit $LASTEXITCODE
