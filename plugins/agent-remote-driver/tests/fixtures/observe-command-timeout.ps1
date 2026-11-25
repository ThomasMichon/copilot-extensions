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
$helpers = @{}
$descendants = @{}
$held = $null
$cancelled = $false
try {
    # Initialize the read-only observer before the command's own deadline.
    $null = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -Property ProcessId
    if ($CancelCaller) {
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
    do {
        $parents = @($command.Id) + @($helpers.Values | ForEach-Object { $_.ProcessId })
        $filter = ($parents | Select-Object -Unique | ForEach-Object { "ParentProcessId=$_" }) -join ' OR '
        foreach ($row in @(Get-CimInstance Win32_Process -Filter $filter -Property ProcessId,ParentProcessId,CreationDate,Name)) {
            $identity = '{0}|{1}' -f $row.ProcessId,$row.CreationDate.ToUniversalTime().Ticks
            if ($row.ParentProcessId -eq $command.Id) { $helpers[$identity] = $row }
            else { $descendants[$identity] = $row }
        }
        $stalled = -not $AwaitHelperDeadline -or (Test-Path -LiteralPath (Join-Path $HomeDirectory 'helper-stalled'))
        if ($CancelCaller -and -not $cancelled -and $stalled -and @($helpers.Values | Where-Object { $_.Name -ieq 'powershell.exe' }).Count) {
            if ($command.HasExited) { throw 'The original caller completed before cancellation could be observed' }
            Stop-Process -Id $command.Id -ErrorAction Stop
            if (-not $command.WaitForExit(5000)) { throw 'The owned original caller did not exit' }
            $cancelled = $true
        }
        if ($command.HasExited) { break }
        if ($clock.ElapsedMilliseconds -ge 12000) {
            throw 'The fixture CLI exceeded its existing twelve-second bound'
        }
        Start-Sleep -Milliseconds 20
    } while ($true)
    if ($helpers.Count -eq 0) {
        throw 'No native helper was observed; single-process coverage is inconclusive'
    }
    $alive = 0
    if ($AwaitHelperDeadline) {
        do {
            $active = @($helpers.Values | Where-Object {
                $observed = $_
                @(Get-CimInstance Win32_Process -Filter ("ProcessId=" + $observed.ProcessId) -Property ProcessId,CreationDate |
                    Where-Object CreationDate -eq $observed.CreationDate).Count
            })
            if (-not $active.Count) { break }
            $oldest = ($active | Sort-Object CreationDate | Select-Object -First 1).CreationDate
            if (([DateTime]::UtcNow - $oldest.ToUniversalTime()).TotalMilliseconds -gt 6000) {
                throw 'The stalled helper survived its caller and its independent five-second deadline'
            }
            Start-Sleep -Milliseconds 20
        } while ($true)
    }
    foreach ($row in @($helpers.Values) + @($descendants.Values)) {
        foreach ($current in @(Get-CimInstance Win32_Process -Filter ("ProcessId=" + $row.ProcessId) -Property ProcessId,CreationDate)) {
            if ($current.CreationDate -eq $row.CreationDate) { $alive++ }
        }
    }
    $output = $stdout.GetAwaiter().GetResult()
    if ($CancelCaller) {
        if (-not $cancelled) { throw 'Caller-loss coverage is inconclusive' }
        $status = 'caller-cancelled'
    } else {
        $response = $stderr.GetAwaiter().GetResult() | ConvertFrom-Json
        if ($command.ExitCode -ne 1 -or $output -or $response.diagnostics.status -ne $ExpectedStatus) {
            throw 'The original command failure or expected evidence result changed'
        }
        $status = $response.diagnostics.status
    }
    @{
        observedHelpers = $helpers.Count
        observedDescendants = $descendants.Count
        helpersStillAlive = $alive
        helperNames = @($helpers.Values | ForEach-Object { $_.Name } | Sort-Object)
        descendantNames = @($descendants.Values | ForEach-Object { $_.Name } | Sort-Object)
        primaryExitCode = $command.ExitCode
        helperStatus = $status
        helperReadyBeforeCancellation = [bool]($AwaitHelperDeadline -and $stalled -and $cancelled)
        coverage = 'sampled native tree plus canonical managed-only source inspection'
    } | ConvertTo-Json -Compress
} finally {
    try {
        if ($command -and -not $command.HasExited) {
            Stop-Process -Id $command.Id -ErrorAction Stop
            if (-not $command.WaitForExit(5000)) { throw 'Fixture CLI cleanup did not settle' }
        }
    } finally {
        if ($held) { $held.Dispose() }
        foreach ($row in @($helpers.Values) + @($descendants.Values)) {
            $current = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $row.ProcessId) -Property ProcessId,CreationDate
            if ($current -and $current.CreationDate -eq $row.CreationDate) {
                try { $process = [Diagnostics.Process]::GetProcessById($row.ProcessId) }
                catch [ArgumentException] { continue }
                if (-not $process.WaitForExit(10000)) { throw 'The owned fixture helper did not settle' }
            }
        }
    }
}
