[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Action,
    [string]$Distribution,
    [string]$SourceJson,
    [string]$SourceFile,
    [string]$MarketplaceKey,
    [string]$PluginId,
    [string]$PayloadRoot,
    [string]$CopilotHome,
    [string]$ProjectRoot,
    [string]$DurableHome,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ExpectedPayloadRoot,
    [string]$ExpectedPayloadVersion,
    [string]$ExpectedCellRoot,
    [string]$PayloadVersion,
    [string]$PayloadOrigin,
    [string]$PayloadOriginReceipt,
    [object]$ExpectedNamespaceGeneration,
    [object]$ExpectedInstallGeneration,
    [object]$ExpectedActivationGeneration,
    [string]$ExpectedCurrentVersion,
    [switch]$ExpectCurrentAbsent,
    [string]$SnapshotId,
    [string]$RuntimeVersion,
    [string]$ReservationRoot,
    [object]$ExpectedReservationGeneration,
    [string]$ExpectedReservationSha256,
    [string]$NamespaceState = 'active',
    [string]$InstallState = 'active',
    [string]$ActivationMode,
    [string]$ActivationState,
    [string]$LegacyDisposition,
    [string]$LegacyRoot,
    [string]$LegacyProbeJson,
    [string]$LegacyProbeFile,
    [string]$PolicyPath,
    [switch]$WithContextValidation
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom

function Fail([string]$Message) { throw $Message }

function ConvertTo-ExpectedGeneration($Value, [string]$ReceiptName) {
    if ($null -eq $Value) {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    $text = [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
    if ($text -notmatch '\A[0-9]+\z') {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    [long]$parsed = 0
    if (-not [long]::TryParse(
        $text,
        [Globalization.NumberStyles]::None,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )) {
        Fail "Expected $ReceiptName generation exceeds the portable signed 64-bit maximum."
    }
    return $text
}

function ConvertTo-InputPath([string]$Value) {
    return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Value))
}

$stderr = $null
$childDiagnostic = ''
$diagnosticFailure = ''
try {
    $request = @{}
    foreach ($name in $PSBoundParameters.Keys) {
        if ($name -cin @('Action', 'ExpectCurrentAbsent', 'WithContextValidation')) { continue }
        $request[$name] = $PSBoundParameters[$name]
    }
    $request['NamespaceState'] = $NamespaceState
    $request['InstallState'] = $InstallState
    $request['ExpectCurrentAbsent'] = [bool]$ExpectCurrentAbsent
    $request['WithContextValidation'] = [bool]$WithContextValidation

    foreach ($pair in @(
        @('ExpectedNamespaceGeneration', 'namespace.json'),
        @('ExpectedInstallGeneration', 'install.json'),
        @('ExpectedActivationGeneration', 'activation'),
        @('ExpectedReservationGeneration', 'reservation')
    )) {
        $required = (
            ($pair[0] -cin @('ExpectedNamespaceGeneration', 'ExpectedInstallGeneration') -and
                $Action -cin @('stamp', 'activation-cas', 'snapshot-stamp', 'slot-cutover', 'slot-release')) -or
            ($pair[0] -ceq 'ExpectedActivationGeneration' -and $Action -ceq 'activation-cas') -or
            ($pair[0] -ceq 'ExpectedReservationGeneration' -and $Action -ceq 'slot-release')
        )
        if ($required) {
            $request[$pair[0]] = ConvertTo-ExpectedGeneration (Get-Variable -Name $pair[0] -ValueOnly) $pair[1]
        }
    }

    if ($Action -ceq 'slot-release' -and -not $DurableHome) {
        Fail 'slot-release requires -DurableHome.'
    }
    $userHomePath = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    if ((-not $CopilotHome -or -not $DurableHome) -and -not $userHomePath) {
        Fail 'Cannot determine the user home.'
    }
    if (-not $CopilotHome) { $CopilotHome = Join-Path $userHomePath '.copilot' }
    if (-not $DurableHome) { $DurableHome = Join-Path $userHomePath '.copilot-extensions' }
    if (-not [IO.Path]::IsPathRooted($CopilotHome) -or
        -not [IO.Path]::IsPathRooted($DurableHome)) {
        Fail '-CopilotHome and -DurableHome must be absolute.'
    }
    $request['CopilotHome'] = ConvertTo-InputPath $CopilotHome
    $request['DurableHome'] = ConvertTo-InputPath $DurableHome
    $modeActions = @('status', 'deployment-status', 'probe-plugin-publication', 'probe-legacy')
    $sourceActions = @('source-id', 'resolve', 'stamp') + $modeActions
    $fileInputs = @('ProjectRoot')
    if ($Action -cin $sourceActions) { $fileInputs += 'SourceFile' }
    if ($Action -cin ($modeActions + @('activation-cas'))) { $fileInputs += 'LegacyProbeFile' }
    foreach ($name in $fileInputs) {
        if ($request[$name]) { $request[$name] = ConvertTo-InputPath $request[$name] }
    }
    # These inputs historically accept rooted drive-relative paths. Receipt
    # pointers, expectations and reservation identities retain stricter checks.
    $pointer = if ($Context) { $Context } else { $env:COPILOT_EXTENSIONS_CONTEXT }
    $rootedInputs = @()
    if ($Action -ceq 'stamp' -or ($Action -cin (@('resolve') + $modeActions) -and -not $pointer)) {
        $rootedInputs += 'PayloadRoot'
    }
    if ($Action -cin $modeActions) { $rootedInputs += @('LegacyRoot', 'PolicyPath') }
    if ($Action -ceq 'stamp') { $rootedInputs += 'PayloadOriginReceipt' }
    foreach ($name in $rootedInputs) {
        if ($request[$name]) {
            if (-not [IO.Path]::IsPathRooted([string]$request[$name])) {
                Fail "-$name must be absolute."
            }
            $request[$name] = ConvertTo-InputPath $request[$name]
        }
    }
    if ($request['ProjectRoot'] -and -not (Test-Path -LiteralPath $request['ProjectRoot'])) {
        Fail "Path does not exist: $($request['ProjectRoot'])"
    }
    $childEnvironment = @{}
    if ($env:COPILOT_PLUGIN_ROOT -and -not $pointer -and
        $Action -cin (@('resolve', 'stamp') + $modeActions)) {
        if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
        }
        $childEnvironment['COPILOT_PLUGIN_ROOT'] = ConvertTo-InputPath $env:COPILOT_PLUGIN_ROOT
    }
    if ($Action -cin $modeActions -and $pointer) {
        if (-not [IO.Path]::IsPathRooted($pointer)) {
            Fail 'The installation-context receipt pointer must be absolute.'
        }
        $request['Context'] = ConvertTo-InputPath $pointer
    }
    $sdk = Join-Path $PSScriptRoot 'installation_context.py'
    if (-not (Test-Path -LiteralPath $sdk -PathType Leaf)) {
        Fail 'The owning payload installation_context.py SDK is unavailable.'
    }
    try {
        $python = @(Get-Command -Name python -CommandType Application -ErrorAction Stop)[0]
    }
    catch [System.Management.Automation.CommandNotFoundException] {
        Fail (
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
    $packet = @{
        sdk = $sdk
        action = $Action
        parameters = $request
        supplied = @($PSBoundParameters.Keys)
        processDirectory = [Environment]::CurrentDirectory
        environment = $childEnvironment
        lifetime = @{ owner_pid = $PID; owner_birth = $callerBirth; nonce = $nonce }
    } | ConvertTo-Json -Depth 6 -Compress
    $encoded = [Convert]::ToBase64String($utf8NoBom.GetBytes($packet))
    # Request data travels over the private input pipe, not native argv.
    # Encoding preserves literal values without the OS command-line size limit.
    $bootstrap = @'
import base64,json,os,runpy,sys
if sys.version_info < (3,10):
    sys.stderr.write('installation-context: Python 3.10+ is required on PATH; run the harness prerequisite setup or install the standalone prerequisite.\n')
    sys.exit(1)
request=json.loads(base64.b64decode(sys.stdin.buffer.raw.readline()).decode('utf-8'))
worker=os.path.join(os.path.dirname(request['sdk']),'_installation_context_worker.py')
sys.exit(runpy.run_path(worker)['main'](request))
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
            if ($startupClock.ElapsedMilliseconds -ge 30000) {
                Fail 'Caller-lifetime request delivery timed out before application admission.'
            }
        }
        if ($windowsHost) {
            # No remote Job handle exists before complete request delivery.
            $offerPermitted = $true
            $inputWriter.WriteLine('offer:' + $nonce)
        }
        while (-not $ready.Wait(100)) {
            if ($startupClock.ElapsedMilliseconds -ge 30000) {
                Fail 'Caller-lifetime startup timed out before application admission.'
            }
        }
        if (-not $ready.Result) {
            while (-not $process.WaitForExit(100)) {}
            Fail ("Caller-lifetime startup failed: " + $stderr.GetAwaiter().GetResult())
        }
        $receipt = $ready.Result | ConvertFrom-Json
        if ($receipt.version -cne 1 -or $receipt.nonce -cne $nonce -or $receipt.owner_pid -cne $PID) {
            Fail 'Caller-lifetime startup returned an uncorrelated receipt.'
        }
        if ($windowsHost) {
            if ($receipt.scope -cne 'windows-job' -or
                $receipt.job.parent_pid -cne $PID -or $receipt.job.parent_birth -cne $callerBirth -or
                [string]$receipt.job.parent_handle -notmatch '\A[1-9][0-9]*\z') {
                Fail 'Caller-lifetime startup did not transfer its original Job owner.'
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
                Fail 'Caller-lifetime worker birth identity changed before acknowledgement.'
            }
        } elseif ($receipt.scope -cne 'posix-group' -or $receipt.worker_pid -cne $process.Id) {
            Fail 'Caller-lifetime startup did not retain the original process-group leader.'
        }
        $inputWriter.WriteLine($nonce)
        $stdout = $outputReader.ReadToEndAsync()
        while (-not $process.WaitForExit(100)) {}
        if ($null -ne $jobOwner) { $jobOwner.Dispose() }
        $inputWriter.Close()
        foreach ($capture in @($stdout, $stderr)) {
            while (-not $capture.Wait(100)) {}
        }
        $completion = [regex]::Match(
            $stdout.Result, "\r?`n__COPILOT_CALLER_EXIT_" + $nonce + "=([0-9]{1,3})\r?`n\z"
        )
        if (-not $completion.Success -or [int]$completion.Groups[1].Value -gt 255) {
            Fail ("Caller-owned application completion is unconfirmed: " + $stderr.Result)
        }
        $code = [int]$completion.Groups[1].Value
        if (($windowsHost -and $process.ExitCode -cne $code) -or
            (-not $windowsHost -and $process.ExitCode -cne 137)) {
            Fail 'Caller-owned process retirement is unconfirmed.'
        }
        $output = $stdout.Result.Substring(0, $completion.Index)
        if ($output.Length -gt 0) {
            Write-Output ($output.TrimEnd([char[]]"`r`n"))
        }
        [Console]::Error.Write($stderr.Result)
        exit $code
    }
    finally {
        try {
            if ($windowsHost -and $offerPermitted -and $null -eq $jobOwner) {
                # Only this explicit non-adoption declaration permits the
                # helper to reclaim its offer through the same pinned parent.
                try { $inputWriter.WriteLine('abort:' + $nonce) }
                catch {
                    [Console]::Error.WriteLine(
                        'installation-context: Unadopted Job offer cleanup is unconfirmed: ' +
                        $_.Exception.Message
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
                        [Console]::Error.WriteLine('installation-context: Caller-owned startup cleanup is unconfirmed.')
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
}
catch {
    $failure = "installation-context: $($_.Exception.Message)"
    if ($childDiagnostic) {
        $detail = $childDiagnostic.TrimEnd([char[]]"`r`n")
        if (-not $failure.Contains($detail)) { $failure += "`n" + $detail }
    }
    if ($diagnosticFailure) { $failure += "`n" + $diagnosticFailure }
    [Console]::Error.WriteLine($failure)
    exit 1
}
