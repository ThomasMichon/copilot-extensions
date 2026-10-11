$script:SupervisorAuditCorrelation = "$PID-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

function Invoke-SupervisorAudit {
    param(
        [string]$Event, [string]$Reason, [string]$Name = '',
        [string]$EnvFile = '', [string]$Outcome = 'begin', [int]$ExitCode = -1
    )
    try {
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
        '--origin', 'installer.ps1', '--verb', $script:Action,
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
    } catch {
        Write-Warn "Supervisor lifecycle audit probe unavailable ($Event/$Reason); action is not suppressed"
    }
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

        if ($Operation -eq 'start') {
            if (-not $script:SupervisorReconciledNames) { $script:SupervisorReconciledNames = @{} }
            $startedTask = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
            if ($startedTask -and $startedTask.State -eq 'Running') {
                $script:SupervisorReconciledNames[$Name] = $true
            } else {
                throw 'native task start returned but activation is unconfirmed'
            }
        }

        Invoke-SupervisorAudit -Event $Operation -Reason $Reason -Name $Name -Outcome ok
        $script:SupervisorActionSucceeded = $true
    } catch {
        $script:SupervisorReconcileFailed = $true
        Invoke-SupervisorAudit -Event $Operation -Reason $Reason -Name $Name -Outcome fail
        Write-Warn "Supervisor $Operation failed ($Name): $($_.Exception.Message)"
        $script:SupervisorActionSucceeded = $false
    }
}

function Convert-SupervisorRetirementResult {
    param([string]$Output)
    $lines = @($Output -split "`n" | Where-Object { $_.Trim() })
    $jsonLines = @($lines | Where-Object { $_.Trim().StartsWith('{') })
    foreach ($line in $lines) {
        if (-not $line.Trim().StartsWith('{')) {
            Write-Warn "Supervisor retirement diagnostic: $line"
        }
    }
    if ($jsonLines.Count -ne 1) { throw 'Expected one retirement JSON result' }
    return ($jsonLines[0] | ConvertFrom-Json -ErrorAction Stop)
}
