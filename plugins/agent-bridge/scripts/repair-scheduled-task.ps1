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
      2. Removes the stale/broken primary 'Agent Bridge' task.
      3. Registers the clean default **interactive AtLogOn** task, reusing the
         existing task's action verbatim when present (it points at the stable
         `start-agent-bridge.ps1` supervisor, which resolves the live runtime from
         the `current-version` marker) so nothing about the runtime contract
         drifts. Only the logon mode changes (S4U/boot -> interactive).

    It deliberately does NOT start the daemon: starting it from this elevated
    context would leave an ELEVATED daemon running (wrong -- the daemon must run
    as the normal user). The daemon self-heals on demand meanwhile (any
    `agent-bridge` command boots it), and the repaired task starts it at the next
    logon. Run this once; routine updates then leave the healthy task untouched.

.NOTES
    The task's action/contract is owned by install.ps1's Register-ScheduledTask_;
    this script reuses the live action rather than re-declaring it, so the two
    never diverge.
#>
[CmdletBinding()]
param(
    # Internal: set on the elevated relaunch so the elevated instance does the
    # work instead of prompting again.
    [switch]$Elevated,

    # Force a re-register even when the task is already a healthy non-elevated
    # interactive AtLogOn task (normally that is a no-op that skips UAC).
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$TaskName = 'Agent Bridge'
$InstallDir = if ($env:AGENT_BRIDGE_CONFIG_DIR) {
    $env:AGENT_BRIDGE_CONFIG_DIR
} else {
    Join-Path $env:USERPROFILE '.agent-bridge'
}
$Supervisor = Join-Path $InstallDir 'start-agent-bridge.ps1'

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
    Write-Host "agent-bridge: replacing the existing elevated/S4U '$TaskName' task with the default non-elevated one needs elevation." -ForegroundColor Cyan
    Write-Host "A Windows UAC prompt will appear -- approve it to continue." -ForegroundColor DarkGray

    $exe = (Get-Process -Id $PID).Path
    if (-not $exe) { $exe = Resolve-PwshPath }
    $relaunchArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath, '-Elevated'
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
Write-Host "Repairing the '$TaskName' scheduled task (target: non-elevated interactive AtLogOn)..." -ForegroundColor Cyan

# Capture the existing task's action so the rebuilt task keeps the exact same,
# version-stable launch contract (only the logon mode changes).
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$oldAction = if ($existing) { @($existing.Actions)[0] } else { $null }
if ($existing) {
    Write-Host ("  existing task: LogonType={0}" -f $existing.Principal.LogonType)
}

# Remove the stale registration (COM view first, then the on-disk store -- the
# two can desync, and schtasks.exe /Delete clears a file Unregister missed).
$removed = $false
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    $removed = $true
} catch {}
try {
    & schtasks.exe /Delete /TN $TaskName /F *> $null
    if ($LASTEXITCODE -eq 0) { $removed = $true }
} catch {}
if ($removed) {
    Write-Host "  removed the stale '$TaskName' task"
} else {
    Write-Host "  no existing '$TaskName' task to remove -- provisioning fresh"
}

# Rebuild the action: reuse the captured one verbatim, else reconstruct it to
# point at the stable supervisor (present on any provisioned box).
if ($oldAction -and $oldAction.Execute) {
    $action = New-ScheduledTaskAction `
        -Execute $oldAction.Execute `
        -Argument $oldAction.Arguments `
        -WorkingDirectory $oldAction.WorkingDirectory
} else {
    if (-not (Test-Path -LiteralPath $Supervisor)) {
        Write-Error "Supervisor script not found at '$Supervisor'. Install agent-bridge first (`install.ps1 provision`), then re-run this repair."
        exit 2
    }
    $pwshPath = Resolve-PwshPath
    $action = New-ScheduledTaskAction `
        -Execute 'conhost.exe' `
        -Argument "--headless `"$pwshPath`" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$Supervisor`"" `
        -WorkingDirectory $InstallDir
}

# The clean default: interactive AtLogOn, current user, Limited (no elevation --
# the daemon does not need it), 15s delay. This is the mode a headless S4U task
# fails at on a box with no reliable logon-token acquisition (the 267011 case).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT15S'
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description 'Agent-Bridge -- inter-agent communication service (auto-start at logon).' `
        -Force | Out-Null
} catch {
    Write-Error "Failed to register the interactive '$TaskName' task: $($_.Exception.Message.Trim())"
    exit 3
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ("[OK] '{0}' registered (LogonType={1}, AtLogOn +15s)." -f $TaskName, $task.Principal.LogonType) -ForegroundColor Green
    Write-Host "     It starts the daemon at your next logon. Not started now (that would run it elevated); the daemon self-heals on demand meanwhile." -ForegroundColor DarkGray
    exit 0
}
Write-Error "Registration reported success but '$TaskName' is not present."
exit 3
