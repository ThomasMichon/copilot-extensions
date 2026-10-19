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
    param([object[]]$Output)
    # Preserve the native stream boundary: stderr is diagnostic, not JSON.
    $stdout = @(
        foreach ($record in $Output) {
            if ($record -is [System.Management.Automation.ErrorRecord]) {
                Write-Warn "Supervisor retirement diagnostic: $record"
            } else {
                [string]$record
            }
        }
    ) -join "`n"
    if (-not $stdout.Trim()) { throw 'Expected one retirement JSON result on stdout' }
    if (-not $stdout.TrimStart().StartsWith('{')) { throw 'Expected retirement JSON object on stdout' }
    $result = ConvertFrom-Json -InputObject $stdout -ErrorAction Stop
    if ($result -isnot [pscustomobject] -or
        $result.ok -isnot [bool] -or
        $result.selected -isnot [array] -or
        $result.retired -isnot [array] -or
        $result.errors -isnot [array]) {
        throw 'Invalid typed retirement JSON result'
    }
    foreach ($processId in @($result.selected) + @($result.retired)) {
        if (($processId -isnot [int] -and $processId -isnot [long]) -or $processId -le 0) {
            throw 'Invalid retirement process identity'
        }
    }
    foreach ($processId in $result.retired) {
        if ($processId -notin $result.selected) { throw 'Retired process was not selected' }
    }
    foreach ($message in $result.errors) {
        if ($message -isnot [string]) { throw 'Invalid retirement error diagnostic' }
    }
    return $result
}
