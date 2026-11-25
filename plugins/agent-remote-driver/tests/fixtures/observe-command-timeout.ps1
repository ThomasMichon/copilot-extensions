param(
    [Parameter(Mandatory=$true)][string]$Node,
    [Parameter(Mandatory=$true)][string]$Cli,
    [Parameter(Mandatory=$true)][string]$HomeDirectory,
    [Parameter(Mandatory=$true)][string]$ExpectedStatus,
    [switch]$CancelCaller,
    [switch]$AwaitHelperDeadline
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$command = $null
$helper = $null
$held = $null
$cancelled = $false
try {
    if ($CancelCaller -and -not $AwaitHelperDeadline) {
        $payload = Split-Path -Parent (Split-Path -Parent $Cli)
        $support = Join-Path $payload 'scripts\command-errors'
        $index = Get-Content -LiteralPath (Join-Path $support 'index.json') -Raw | ConvertFrom-Json
        . (Join-Path (Join-Path $support $index.bundle) 'private-paths.ps1')
        $directory = Join-Path $HomeDirectory '.copilot-extensions\logs\diagnostics\commands'
        Initialize-CEPrivateDirectory $directory
        $held = Open-CEPrivateFile (Join-Path $directory '.command-errors.lock') -TokenOwner
        $held.Lock(1073741824, 1)
    }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Node
    $start.Arguments = '"' + $Cli + '" status'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables['HOME'] = $HomeDirectory
    $start.EnvironmentVariables['USERPROFILE'] = $HomeDirectory
    $command = [Diagnostics.Process]::Start($start)
    $stdout = $command.StandardOutput.ReadToEndAsync()
    $stderr = $command.StandardError.ReadToEndAsync()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $ready = Join-Path $HomeDirectory 'helper-ready.json'
    do {
        if (Test-Path -LiteralPath $ready) {
            $identity = [IO.File]::ReadAllText($ready) | ConvertFrom-Json
            $helper = [Diagnostics.Process]::GetProcessById($identity.pid)
            $null = $helper.Handle
            if ($helper.StartTime.ToUniversalTime().Ticks -ne $identity.created) {
                throw 'The fixture helper identity changed before observation'
            }
            $helperName = $helper.ProcessName + '.exe'
            $helperStarted = $helper.StartTime.ToUniversalTime()
            [IO.File]::WriteAllText((Join-Path $HomeDirectory 'helper-observed'), 'ready')
            break
        }
        if ($command.HasExited -or $clock.ElapsedMilliseconds -ge 12000) {
            throw 'The actual helper did not reach its observation boundary'
        }
        Start-Sleep -Milliseconds 5
    } while ($true)
    if ($AwaitHelperDeadline) {
        while (-not (Test-Path -LiteralPath (Join-Path $HomeDirectory 'helper-stalled'))) {
            if ($command.HasExited -or $clock.ElapsedMilliseconds -ge 12000) {
                throw 'The actual helper did not enter its deliberate stall'
            }
            Start-Sleep -Milliseconds 5
        }
    }
    if ($CancelCaller) {
        if ($command.HasExited) { throw 'The original caller completed before cancellation' }
        $command.Kill()
        $cancelled = $true
    }
    if (-not $command.WaitForExit([Math]::Max(0, 12000 - [int]$clock.ElapsedMilliseconds))) {
        throw 'The fixture CLI exceeded its existing twelve-second bound'
    }
    # Inspect the retained native handle before waiting for inherited streams.
    $remaining = [Math]::Max(0, 6000 - [int]([DateTime]::UtcNow - $helperStarted).TotalMilliseconds)
    $retired = $helper.WaitForExit($remaining)
    $output = $stdout.GetAwaiter().GetResult()
    if ($CancelCaller) {
        $status = 'caller-cancelled'
    } else {
        $response = $stderr.GetAwaiter().GetResult() | ConvertFrom-Json
        if ($command.ExitCode -ne 1 -or $output -or $response.diagnostics.status -ne $ExpectedStatus) {
            throw ('The primary result or evidence status changed: exit={0}, stdoutLength={1}, status={2}' -f $command.ExitCode,$output.Length,$response.diagnostics.status)
        }
        $status = $response.diagnostics.status
    }
    @{
        helpersStillAlive = [int](-not $retired)
        helperNames = @($helperName)
        primaryExitCode = $command.ExitCode
        helperStatus = $status
        helperReadyBeforeCancellation = [bool]($AwaitHelperDeadline -and $cancelled)
        coverage = 'retained helper handle and creation time, before streams or outer Job cleanup'
    } | ConvertTo-Json -Compress
} finally {
    try {
        if ($command -and -not $command.HasExited) {
            $command.Kill()
            if (-not $command.WaitForExit(5000)) { throw 'Fixture CLI cleanup did not settle' }
        }
    } finally {
        if ($held) { $held.Dispose() }
        if ($helper) {
            if (-not $helper.WaitForExit(10000)) { throw 'The owned fixture helper did not settle' }
            $helper.Dispose()
        }
        if ($command) { $command.Dispose() }
    }
}
