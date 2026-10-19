$script:SupervisorAuditCorrelation = "$PID-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

function Get-SupervisorDispatchArguments {
    param([string]$CommandLine)
    if (-not ('DispatchSupervisorCommandLine' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class DispatchSupervisorCommandLine {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CommandLineToArgvW(string commandLine, out int count);
    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr value);
    public static string[] Split(string commandLine) {
        int count;
        IntPtr values = CommandLineToArgvW(commandLine, out count);
        if (values == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            string[] result = new string[count];
            for (int i = 0; i < count; i++)
                result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(values, i * IntPtr.Size));
            return result;
        } finally { LocalFree(values); }
    }
}
'@
    }
    $tokens = [DispatchSupervisorCommandLine]::Split($CommandLine)
    if (-not $tokens.Count) { return @() }
    $index = 1
    $module = [IO.Path]::GetFileNameWithoutExtension($tokens[0])
    $found = $module -in @('agent_dispatch', 'agent-dispatch')
    if (-not $found) {
        while ($index -lt $tokens.Count) {
            $value = $tokens[$index]
            if ($value -eq '-m') {
                if ($index + 1 -ge $tokens.Count -or $tokens[$index + 1] -notin @('agent_dispatch', 'agent-dispatch')) { return @() }
                $index += 2
                $found = $true
                break
            }
            if (-not $value.StartsWith('-') -or $value.StartsWith('-c')) { return @() }
            if ($value -in @('-X', '-W', '--check-hash-based-pycs')) { $index += 2 } else { $index++ }
        }
    }
    if (-not $found) { return @() }
    while ($index -lt $tokens.Count -and $tokens[$index].StartsWith('-')) {
        $option = ($tokens[$index] -split '=', 2)[0]
        if ($option -in @('--local', '--shared') -and $tokens[$index] -eq $option) {
            $index++
        } elseif ($option -in @('--url', '--token', '--control-token')) {
            if ($tokens[$index].Contains('=')) { $index++ }
            elseif ($index + 1 -lt $tokens.Count -and -not $tokens[$index + 1].StartsWith('--')) {
                $index += 2
            } else { return @() }
        } else { return @() }
    }
    if ($index -lt $tokens.Count) { return $tokens[$index..($tokens.Count - 1)] }
    return @()
}

function Stop-SupervisorSnapshotProcess {
    param($Snapshot)
    $process = $null
    try {
        if (-not $Snapshot.CreationDate) { throw 'snapshot has no process creation identity' }
        $expected = ([datetime]$Snapshot.CreationDate).ToUniversalTime().ToFileTimeUtc()
        if (-not ('DispatchSupervisorProcessHandle' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class DispatchSupervisorProcessHandle {
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool GetProcessTimes(
        IntPtr handle, out long creation, out long exit, out long kernel, out long user);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool TerminateProcess(IntPtr handle, uint exitCode);
    public static long CreationFileTime(IntPtr handle) {
        long creation, exit, kernel, user;
        if (!GetProcessTimes(handle, out creation, out exit, out kernel, out user))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return creation;
    }
    public static void Terminate(IntPtr handle) {
        if (!TerminateProcess(handle, 1))
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }
}
'@
        }
        $process = Get-Process -Id ([int]$Snapshot.ProcessId) -ErrorAction Stop
        $handle = $process.Handle
        $actual = [DispatchSupervisorProcessHandle]::CreationFileTime($handle)
        # CIM creation timestamps retain microseconds; FILETIME retains 100 ns.
        if (($actual - ($actual % 10)) -ne ($expected - ($expected % 10))) {
            throw 'process creation identity changed after inventory'
        }
        [DispatchSupervisorProcessHandle]::Terminate($handle)
        return $true
    } catch {
        Write-Warn "Supervisor process $($Snapshot.ProcessId) not retired: $($_.Exception.Message)"
        return $false
    } finally {
        if ($process) { $process.Dispose() }
    }
}

function Invoke-SupervisorAudit {
    param(
        [string]$Event, [string]$Reason, [string]$Name = '',
        [string]$EnvFile = '', [string]$Outcome = 'begin', [int]$ExitCode = -1
    )
    try {
    $py = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } elseif (Test-Path -LiteralPath $LinkPython) { $LinkPython } else { $null }
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
