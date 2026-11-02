# Import retains existing compiled guards; migration requires a fresh process.
if ('CopilotExtensions.NativeOwnedProcess' -as [type]) {
    throw 'A legacy compiled owned-process adapter is already loaded. Use a fresh PowerShell process after the normal payload update; existing process custody was not migrated.'
}
$script:NativeOwnedInstances = Join-Path $PSScriptRoot 'daemon_instances.ps1'

function Invoke-NativeOwnedProcess {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$LogPath,
        [int]$TimeoutMilliseconds = 180000
    )
    if ($env:OS -ne 'Windows_NT') { throw 'Native PowerShell process custody requires Windows.' }
    if ($TimeoutMilliseconds -le 0 -or $TimeoutMilliseconds -gt 7200000) {
        throw 'Owned child requires a deadline of at most two hours.'
    }
    foreach ($path in @($FilePath, $WorkingDirectory, $LogPath)) {
        if (-not [IO.Path]::IsPathRooted($path) -or [IO.Path]::GetFullPath($path) -ne $path) {
            throw 'Native process paths must be fully qualified and normalized.'
        }
    }
    $environment = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $environment[[string]$entry.Key] = [string]$entry.Value
    }
    if ($environment.ContainsKey('COPILOT_EXTENSIONS_OWNED_JOB_SCOPES')) {
        $scopes = [string]$environment['COPILOT_EXTENSIONS_OWNED_JOB_SCOPES']
        $names = $scopes.Split(';')
        if ($scopes.Length -gt 4096 -or $names.Count -ge 64 -or
            @($names | Select-Object -Unique).Count -ne $names.Count -or
            @($names | Where-Object { $_ -cnotmatch '\ALocal\\ce-owned-job-[0-9a-f]{32}\z' }).Count) {
            $failure = [ArgumentException]::new('Owned Job scope metadata is invalid or has no child capacity.')
            $failure.Data['CleanupConfirmed'] = $true
            throw $failure
        }
    }
    . $script:NativeOwnedInstances
    try {
        $result = Invoke-DaemonInstanceRequest @{command='run-owned'; parameters=@{
            executable=$FilePath; arguments=@($Arguments); cwd=$WorkingDirectory; log=$LogPath
            timeout_ms=$TimeoutMilliseconds; environment=$environment
        }}
        if (($result.returncode -isnot [int] -and $result.returncode -isnot [long]) -or
            $result.returncode -lt [int]::MinValue -or $result.returncode -gt [int]::MaxValue) {
            throw 'Native owned-process completion is unconfirmed.'
        }
        return [int]$result.returncode
    } catch {
        $cause = $_.Exception
        while ($cause.InnerException -and -not $cause.Data.Contains('CleanupConfirmed')) {
            $cause = $cause.InnerException
        }
        $failure = [InvalidOperationException]::new(
            'Native owned process failed: ' + $cause.Message, $_.Exception
        )
        $failure.Data['CleanupConfirmed'] = (
            $cause.Data.Contains('CleanupConfirmed') -and
            $cause.Data['CleanupConfirmed'] -is [bool] -and $cause.Data['CleanupConfirmed'] -ceq $true
        )
        throw $failure
    }
}
