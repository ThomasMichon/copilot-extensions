function Invoke-BootstrapOwnedCapture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgList = @(),
        [hashtable]$Environment,
        [string]$InputText = '',
        [ValidateRange(1, 7200000)][int]$TimeoutMilliseconds = 30000,
        [ValidateRange(1, 2147483647)][int]$CleanupMilliseconds = 3000
    )
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    if ([System.Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Bootstrap native capture requires Windows.'
    }
    if ($CleanupMilliseconds -ge $TimeoutMilliseconds) {
        throw 'Bootstrap capture must reserve cleanup inside its timeout.'
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $fenceKey = 'CopilotExtensions.BootstrapCapture.Unconfirmed'
    if ($null -ne [AppDomain]::CurrentDomain.GetData($fenceKey)) {
        throw 'Bootstrap capture retains unconfirmed cleanup; replay is refused.'
    }
    $workDeadline = $TimeoutMilliseconds - $CleanupMilliseconds
    $utf8 = New-Object Text.UTF8Encoding($false)
    $hostPath = Join-Path $PSScriptRoot 'bootstrap-capture.exe'
    $asset = Get-Item -LiteralPath $hostPath -Force -ErrorAction Stop
    if ($asset.PSIsContainer -or ($asset.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The packaged bootstrap capture host must be an ordinary file.'
    }
    $assemblyBytes = [IO.File]::ReadAllBytes($asset.FullName)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $retirementKey = 'CopilotExtensions.BootstrapCapture.JobRetirement.' +
            [Convert]::ToBase64String($hasher.ComputeHash($assemblyBytes))
    } finally { $hasher.Dispose() }
    $retirement = [AppDomain]::CurrentDomain.GetData($retirementKey)
    if ($null -eq $retirement) {
        $retirement = [Reflection.Assembly]::Load($assemblyBytes).GetType(
            'CopilotExtensions.BootstrapCapture.JobRetirement', $true
        ).GetMethod('TerminateAndWait')
        if ($null -eq $retirement) { throw 'The packaged bootstrap retirement method is unavailable.' }
        [AppDomain]::CurrentDomain.SetData($retirementKey, $retirement)
    }
    $nonce = [Guid]::NewGuid().ToString('N')
    $caller = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $birth = 'windows:' + $caller.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
            [Globalization.CultureInfo]::InvariantCulture
        )
    } finally { $caller.Dispose() }
    $childEnvironment = @{}
    foreach ($entry in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $childEnvironment[[string]$entry.Key] = [string]$entry.Value
    }
    if ($Environment) {
        foreach ($name in $Environment.Keys) {
            if ($null -eq $Environment[$name]) { $childEnvironment.Remove($name) }
            else { $childEnvironment[$name] = [string]$Environment[$name] }
        }
    }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $hostPath
    $start.WorkingDirectory = [System.Environment]::CurrentDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.EnvironmentVariables['COMPLUS_NoGuiFromShim'] = '1'
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $utf8
    $start.StandardErrorEncoding = $utf8
    $process = $null
    $job = $null
    $errors = $null
    $offerPermitted = $false
    $failure = $null
    $result = $null
    $retain = $false
    try {
        if ($clock.ElapsedMilliseconds -ge $workDeadline) { throw [TimeoutException]::new('WATCHDOG: bootstrap capture startup deadline.') }
        $process = [Diagnostics.Process]::Start($start)
        $process.StandardInput.NewLine = "`n"
        $process.StandardInput.AutoFlush = $true
        $ready = $process.StandardOutput.ReadLineAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $remaining = $workDeadline - $clock.ElapsedMilliseconds
        if ($remaining -le 0) { throw [TimeoutException]::new('WATCHDOG: bootstrap capture startup deadline.') }
        $packet = @{
            version = 1; nonce = $nonce; owner_pid = $PID; owner_birth = $birth
            executable = $FilePath; arguments = @($ArgList); cwd = $start.WorkingDirectory
            environment = $childEnvironment; input = $InputText; timeout_ms = [int]$remaining
        } | ConvertTo-Json -Compress -Depth 6
        if ($utf8.GetByteCount($packet) -gt 1048576) { throw 'Bootstrap capture request exceeds its byte bound.' }
        $encoded = [Convert]::ToBase64String($utf8.GetBytes($packet))
        $write = $process.StandardInput.WriteLineAsync($encoded)
        while (-not $write.IsCompleted) {
            if ($clock.ElapsedMilliseconds -ge $workDeadline) { throw [TimeoutException]::new('WATCHDOG: bootstrap request delivery deadline.') }
            [Threading.Thread]::Sleep(5)
        }
        [void]$write.GetAwaiter().GetResult()
        $offerPermitted = $true
        $permission = $process.StandardInput.WriteLineAsync('offer:' + $nonce)
        while (-not $permission.IsCompleted) {
            if ($clock.ElapsedMilliseconds -ge $workDeadline) { throw [TimeoutException]::new('WATCHDOG: bootstrap offer deadline.') }
            [Threading.Thread]::Sleep(5)
        }
        [void]$permission.GetAwaiter().GetResult()
        while (-not $ready.IsCompleted) {
            if ($clock.ElapsedMilliseconds -ge $workDeadline) { throw [TimeoutException]::new('WATCHDOG: bootstrap custody deadline.') }
            [Threading.Thread]::Sleep(5)
        }
        $line = $ready.GetAwaiter().GetResult()
        if (-not $line -or $line.Length -gt 16384) { throw 'Bootstrap custody receipt is unavailable or oversized.' }
        $receipt = $line | ConvertFrom-Json
        if ($receipt.version -cne 1 -or $receipt.kind -cne 'ready' -or $receipt.nonce -cne $nonce -or
            $receipt.job.parent_pid -cne $PID -or $receipt.job.parent_birth -cne $birth -or
            $receipt.job.worker_pid -cne $process.Id -or
            [string]$receipt.job.parent_handle -notmatch '\A[1-9][0-9]*\z' -or
            [string]$receipt.job.name -cnotmatch '\ALocal\\ce-owned-job-[0-9a-f]{32}\z') {
            throw 'Bootstrap custody receipt is uncorrelated.'
        }
        $workerBirth = 'windows:' + $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
            [Globalization.CultureInfo]::InvariantCulture
        )
        if ($receipt.job.worker_birth -cne $workerBirth -or $process.HasExited) {
            throw 'Bootstrap worker birth identity changed before adoption.'
        }
        $job = [Microsoft.Win32.SafeHandles.SafeFileHandle]::new(
            [IntPtr]([long]$receipt.job.parent_handle), $true
        )
        $completion = $process.StandardOutput.ReadLineAsync()
        $ack = $process.StandardInput.WriteLineAsync($nonce)
        while (-not $ack.IsCompleted -or -not $completion.IsCompleted) {
            if ($clock.ElapsedMilliseconds -ge $workDeadline) { throw [TimeoutException]::new('WATCHDOG: bootstrap selector deadline.') }
            [Threading.Thread]::Sleep(5)
        }
        [void]$ack.GetAwaiter().GetResult()
        if ($clock.ElapsedMilliseconds -ge $workDeadline) { throw [TimeoutException]::new('WATCHDOG: bootstrap selector deadline.') }
        $line = $completion.GetAwaiter().GetResult()
        if (-not $line -or $line.Length -gt 16777216) { throw 'Bootstrap completion is unavailable or oversized.' }
        $result = $line | ConvertFrom-Json
        if ($result.version -cne 1 -or $result.kind -cne 'result' -or $result.nonce -cne $nonce -or
            ($result.exit_code -isnot [int] -and $result.exit_code -isnot [long]) -or
            $result.exit_code -lt [int]::MinValue -or $result.exit_code -gt [int]::MaxValue -or
            $result.stdout -isnot [string] -or $result.stderr -isnot [string] -or
            $result.error -isnot [string] -or
            ($result.native_error -isnot [int] -and $result.native_error -isnot [long])) {
            throw 'Bootstrap selector completion is uncorrelated.'
        }
    } catch { $failure = $_ }
    finally {
        try {
            if ($null -ne $job) {
                try {
                    $remaining = [Math]::Max(0, $TimeoutMilliseconds - $clock.ElapsedMilliseconds)
                    [void]$retirement.Invoke($null, @($job.DangerousGetHandle(), [int]$remaining))
                    $job.Dispose()
                } catch {
                    $retain = $true
                    $failure = [InvalidOperationException]::new('Bootstrap Job retirement is unconfirmed.', $_.Exception)
                }
            }
            elseif ($process -and $offerPermitted) {
                $retain = $true
                try {
                    $abort = $process.StandardInput.WriteLineAsync('abort:' + $nonce)
                    $remaining = [Math]::Max(0, $TimeoutMilliseconds - $clock.ElapsedMilliseconds)
                    if ($remaining -gt 0) { [void]$abort.Wait([int]$remaining) }
                } catch { }
            }
        } finally {
            if ($process) {
                try { $process.StandardInput.Close() } catch { }
                $remaining = [Math]::Max(0, $TimeoutMilliseconds - $clock.ElapsedMilliseconds)
                $retired = $process.WaitForExit([int]$remaining)
                if (-not $retired) {
                    if (-not $offerPermitted) {
                        try { $process.Kill() } catch { }
                    }
                    $retain = $true
                    $failure = [InvalidOperationException]::new('Bootstrap host retirement is unconfirmed.')
                }
                try {
                    if ($errors -and $errors.IsCompleted) {
                        $diagnostic = $errors.GetAwaiter().GetResult()
                        if ($diagnostic -and -not $failure) {
                            $failure = [InvalidOperationException]::new($diagnostic.Trim())
                        }
                    }
                } catch {
                    $failure = [InvalidOperationException]::new('Bootstrap host diagnostics are unconfirmed.', $_.Exception)
                } finally {
                    if ($retain) {
                        [AppDomain]::CurrentDomain.SetData(
                            $fenceKey, @{Job = $job; Process = $process; Failure = $failure}
                        )
                    } else { $process.Dispose() }
                }
            }
        }
    }
    if ($failure) { throw $failure }
    if ($result.error) {
        if ($result.error -ceq 'deadline') { throw [TimeoutException]::new('WATCHDOG: bootstrap selector deadline.') }
        if ($result.native_error) {
            throw [ComponentModel.Win32Exception]::new(
                [int]$result.native_error, "Bootstrap selector failed (WinError $($result.native_error))."
            )
        }
        throw 'Bootstrap selector capture failed.'
    }
    return [pscustomobject]@{
        ExitCode = [int]$result.exit_code; Stdout = [string]$result.stdout; Stderr = [string]$result.stderr
    }
}
