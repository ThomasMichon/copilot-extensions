$script:SupervisorAuditCorrelation = "$PID-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

function Invoke-SupervisorAudit {
    param(
        [string]$Event, [string]$Reason, [string]$Name = '',
        [string]$EnvFile = '', [string]$Outcome = 'begin', [int]$ExitCode = -1
    )
    $py = if (Test-Path $VenvPython) { $VenvPython } elseif (Test-Path $LinkPython) { $LinkPython } else { $null }
    if (-not $py) {
        Write-Warn "Supervisor lifecycle audit unavailable ($Event/$Reason); action is not suppressed"
        return
    }
    $enabled = 'unknown'
    $active = 'unknown'
    $parentPid = 0
    try {
        $parentPid = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop).ParentProcessId
    } catch { Write-Warn 'Supervisor audit parent process metadata unavailable' }
    if ($Name -and (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        if ($task) {
            $enabled = if ($task.State -eq 'Disabled') { 'disabled' } else { 'enabled' }
            $active = if ($task.State -eq 'Running') { 'active' } else { 'inactive' }
        } else { $enabled = 'absent'; $active = 'absent' }
    }
    if ($Name -and -not $EnvFile) {
        $EnvFile = Join-Path $InstallDir 'supervisor.env'
        if ($Name -ne $SupervisorTaskName -and $Name.StartsWith("$SupervisorTaskName-")) {
            $EnvFile = Join-Path $SupervisorProfileDir ($Name.Substring($SupervisorTaskName.Length + 1) + '.env')
        }
    }
    $auditArgs = @(
        (Join-Path $PluginDir 'src\agent_dispatch\supervisor_lifecycle.py'),
        '--root', $InstallDir, '--action', $Event, '--reason', $Reason,
        '--origin', 'installer.ps1', '--verb', $Action,
        '--correlation', $script:SupervisorAuditCorrelation,
        '--initiator-pid', "$PID", '--parent-pid', "$parentPid",
        '--outcome', $Outcome, '--native-enabled', $enabled, '--native-active', $active
    )
    if ($SrcVersion) { $auditArgs += @('--version', $SrcVersion) }
    if ($Name) { $auditArgs += @('--unit', $Name) }
    if ($EnvFile) { $auditArgs += @('--env-file', $EnvFile) }
    if ($ExitCode -ge 0) { $auditArgs += @('--exit-code', "$ExitCode") }
    $previousExitCode = $global:LASTEXITCODE
    try {
        & $py @auditArgs
        if ($LASTEXITCODE -ne 0) { Write-Warn "Supervisor lifecycle audit unavailable ($Event/$Reason); action is not suppressed" }
    } catch {
        Write-Warn "Supervisor lifecycle audit unavailable ($Event/$Reason); action is not suppressed"
    } finally { $global:LASTEXITCODE = $previousExitCode }
}

function Invoke-SupervisorTaskAction {
    param([string]$Operation, [string]$Name, [string]$Reason)
    Invoke-SupervisorAudit -Event $Operation -Reason $Reason -Name $Name
    try {
        switch ($Operation) {
            'stop' { Stop-ScheduledTask -TaskName $Name -ErrorAction Stop }
            'start' { Start-ScheduledTask -TaskName $Name -ErrorAction Stop }
            'enable' { Enable-ScheduledTask -TaskName $Name -ErrorAction Stop | Out-Null }
            'disable' { Disable-ScheduledTask -TaskName $Name -ErrorAction Stop | Out-Null }
        }
        Invoke-SupervisorAudit -Event $Operation -Reason $Reason -Name $Name -Outcome ok
    } catch {
        $script:SupervisorReconcileFailed = $true
        Invoke-SupervisorAudit -Event $Operation -Reason $Reason -Name $Name -Outcome fail
        Write-Warn "Supervisor $Operation failed ($Name): $($_.Exception.Message)"
    }
}
