[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Worker,
    [Parameter(Mandatory = $true)][hashtable]$Request,
    [ValidateRange(0, 2147483647)][int]$TimeoutSeconds = 0,
    [ValidateSet('caller-owned', 'installation-context')][string]$DiagnosticPrefix = 'caller-owned'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$clock = [Diagnostics.Stopwatch]::StartNew()
$script:deadlineExpired = $false

function Assert-CallerDeadline {
    if ($TimeoutSeconds -gt 0 -and $clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
        $script:deadlineExpired = $true
        throw 'WATCHDOG: caller-owned application deadline exceeded; retirement must complete before returning.'
    }
}

$stderr = $null
$childDiagnostic = ''
$diagnosticFailure = ''
try {
    try {
        $python = @(Get-Command -Name python -CommandType Application -ErrorAction Stop)[0]
    } catch [System.Management.Automation.CommandNotFoundException] {
        throw (
            'Python 3.10+ must be available as python on PATH. ' +
            'Run the harness Python prerequisite setup, or install the standalone prerequisite, then retry.'
        )
    }
    $windowsHost = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $caller = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $callerBirth = if ($windowsHost) {
            'windows:' + $caller.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                [Globalization.CultureInfo]::InvariantCulture
            )
        } else { $null }
    } finally { $caller.Dispose() }
    $nonce = [Guid]::NewGuid().ToString('N')
    $Request['lifetime'] = @{ owner_pid = $PID; owner_birth = $callerBirth; nonce = $nonce }
    $packet = @{ worker = $Worker; request = $Request } | ConvertTo-Json -Depth 10 -Compress
    $encoded = [Convert]::ToBase64String($utf8NoBom.GetBytes($packet))
    # Keep request data on the original shell's private lifetime pipe.
    $bootstrap = @'
import base64,json,runpy,sys
if sys.version_info < (3,10):
    sys.stderr.write('caller-owned: Python 3.10+ is required on PATH; run the harness prerequisite setup or install the standalone prerequisite.\n')
    sys.exit(1)
packet=json.loads(base64.b64decode(sys.stdin.buffer.raw.readline()).decode('utf-8'))
sys.exit(runpy.run_path(packet['worker'])['main'](packet['request']))
'@
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $python.Path
    $startInfo.Arguments = '-I -S -B -X utf8 -c "' + $bootstrap + '"'
    $startInfo.WorkingDirectory = [Environment]::CurrentDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.StandardOutputEncoding = $utf8NoBom
    $startInfo.StandardErrorEncoding = $utf8NoBom
    Assert-CallerDeadline
    $process = [Diagnostics.Process]::Start($startInfo)
    $jobOwner = $null
    $workerOwner = $null
    $inputWriter = $null
    $inputPipe = $null
    $offerPermitted = $false
    $outputReader = $null
    $errorReader = $null
    try {
        $inputWriter = $process.StandardInput
        $inputPipe = $inputWriter.BaseStream
        $inputWriter.NewLine = "`n"
        $inputWriter.AutoFlush = $true
        $outputReader = $process.StandardOutput
        $errorReader = $process.StandardError
        $stderr = $errorReader.ReadToEndAsync()
        $ready = $outputReader.ReadLineAsync()
        $startupClock = [Diagnostics.Stopwatch]::StartNew()
        $requestWrite = $inputWriter.WriteLineAsync($encoded)
        while (-not $requestWrite.Wait(100)) {
            Assert-CallerDeadline
            if ($startupClock.ElapsedMilliseconds -ge 30000) {
                throw 'Caller-lifetime request delivery timed out before application admission.'
            }
        }
        Assert-CallerDeadline
        if ($windowsHost) {
            # Only complete request delivery permits creating a remote Job offer.
            $offerPermitted = $true
            $permissionWrite = $inputWriter.WriteLineAsync('offer:' + $nonce)
            while (-not $permissionWrite.Wait(100)) { Assert-CallerDeadline }
        }
        while (-not $ready.Wait(100)) {
            Assert-CallerDeadline
            if ($startupClock.ElapsedMilliseconds -ge 30000) {
                throw 'Caller-lifetime startup timed out before application admission.'
            }
        }
        Assert-CallerDeadline
        if (-not $ready.Result) {
            # Unwind before diagnostic capture: the worker may need our EOF.
            throw 'Caller-lifetime startup failed before a custody receipt.'
        }
        $receipt = $ready.Result | ConvertFrom-Json
        if ($receipt.version -cne 1 -or $receipt.nonce -cne $nonce -or $receipt.owner_pid -cne $PID) {
            throw 'Caller-lifetime startup returned an uncorrelated receipt.'
        }
        if ($windowsHost) {
            if ($receipt.scope -cne 'windows-job' -or
                $receipt.job.parent_pid -cne $PID -or $receipt.job.parent_birth -cne $callerBirth -or
                [string]$receipt.job.parent_handle -notmatch '\A[1-9][0-9]*\z') {
                throw 'Caller-lifetime startup did not transfer its original Job owner.'
            }
            $jobOwner = [Microsoft.Win32.SafeHandles.SafeFileHandle]::new(
                [IntPtr]([long]$receipt.job.parent_handle), $true
            )
            $workerOwner = [Diagnostics.Process]::GetProcessById([int]$receipt.job.worker_pid)
            [void]$workerOwner.Handle
            $workerBirth = 'windows:' + $workerOwner.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                [Globalization.CultureInfo]::InvariantCulture
            )
            if ($workerBirth -cne $receipt.job.worker_birth -or $workerOwner.HasExited) {
                throw 'Caller-lifetime worker birth identity changed before acknowledgement.'
            }
        } elseif ($receipt.scope -cne 'posix-group' -or $receipt.worker_pid -cne $process.Id) {
            throw 'Caller-lifetime startup did not retain the original process-group leader.'
        }
        $acknowledge = $inputWriter.WriteLineAsync($nonce)
        while (-not $acknowledge.Wait(100)) { Assert-CallerDeadline }
        $stdout = $outputReader.ReadToEndAsync()
        while (-not $process.WaitForExit(100)) { Assert-CallerDeadline }
        Assert-CallerDeadline
        if ($null -ne $jobOwner) { $jobOwner.Dispose() }
        $inputWriter.Close()
        foreach ($capture in @($stdout, $stderr)) {
            while (-not $capture.Wait(100)) { Assert-CallerDeadline }
        }
        $completion = [regex]::Match(
            $stdout.Result, "\r?`n__COPILOT_CALLER_EXIT_" + $nonce + "=([0-9]{1,3})\r?`n\z"
        )
        if (-not $completion.Success -or [int]$completion.Groups[1].Value -gt 255) {
            throw ("Caller-owned application completion is unconfirmed: " + $stderr.Result)
        }
        $code = [int]$completion.Groups[1].Value
        if (($windowsHost -and $process.ExitCode -cne $code) -or
            (-not $windowsHost -and $process.ExitCode -cne 137)) {
            throw 'Caller-owned process retirement is unconfirmed.'
        }
        $output = $stdout.Result.Substring(0, $completion.Index)
        if ($output.Length -gt 0) { Write-Output ($output.TrimEnd([char[]]"`r`n")) }
        [Console]::Error.Write($stderr.Result)
        exit $code
    } finally {
        try {
            if ($windowsHost -and $offerPermitted -and $null -eq $jobOwner) {
                # Only explicit non-adoption permits reclaiming an offered handle.
                try {
                    $abort = $inputWriter.WriteLineAsync('abort:' + $nonce)
                    if (-not $abort.Wait(1000)) {
                        [Console]::Error.WriteLine($DiagnosticPrefix + ': Unadopted Job offer abort delivery is unconfirmed.')
                    }
                } catch {
                    [Console]::Error.WriteLine(
                        $DiagnosticPrefix + ': Unadopted Job offer cleanup is unconfirmed: ' + $_.Exception.Message
                    )
                }
            }
            if ($null -ne $jobOwner) { $jobOwner.Dispose() }
        } finally {
            try {
                if ($null -ne $inputPipe) { $inputPipe.Dispose() }
            } finally {
                try {
                    if ($null -ne $workerOwner) { $workerOwner.Dispose() }
                    $cleanupClock = [Diagnostics.Stopwatch]::StartNew()
                    if (-not $process.WaitForExit(3000)) {
                        [Console]::Error.WriteLine($DiagnosticPrefix + ': Caller-owned startup cleanup is unconfirmed.')
                    } elseif ($null -ne $stderr) {
                        $remaining = [Math]::Max(0, 3000 - $cleanupClock.ElapsedMilliseconds)
                        if (-not $stderr.IsCompleted -and $remaining -gt 0) {
                            try { [void]$stderr.Wait([int]$remaining) }
                            catch { $diagnosticFailure = $_.Exception.Message }
                        }
                        if ($stderr.Status -eq [Threading.Tasks.TaskStatus]::RanToCompletion) {
                            $childDiagnostic = $stderr.Result
                        } elseif ($stderr.IsFaulted -and -not $diagnosticFailure) {
                            $diagnosticFailure = $stderr.Exception.GetBaseException().Message
                        } elseif (-not $diagnosticFailure) {
                            $diagnosticFailure = 'Child diagnostic capture did not complete within cleanup.'
                        }
                    }
                } finally {
                    try {
                        if ($null -ne $outputReader) { $outputReader.Dispose() }
                    } finally {
                        try {
                            if ($null -ne $errorReader) { $errorReader.Dispose() }
                        } finally { $process.Dispose() }
                    }
                }
            }
        }
    }
} catch {
    $failure = "${DiagnosticPrefix}: $($_.Exception.Message)"
    if ($childDiagnostic) {
        $detail = $childDiagnostic.TrimEnd([char[]]"`r`n")
        if (-not $failure.Contains($detail)) { $failure += "`n" + $detail }
    }
    if ($diagnosticFailure) { $failure += "`n" + $diagnosticFailure }
    [Console]::Error.WriteLine($failure)
    if ($script:deadlineExpired) { exit 124 }
    exit 1
}
