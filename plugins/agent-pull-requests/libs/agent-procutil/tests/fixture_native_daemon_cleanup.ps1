param(
    [ValidateSet('cleanup', 'mismatch', 'unconfirmed-cleanup')][string]$Mode,
    [string]$Helper,
    [string]$Root
)
$ErrorActionPreference = 'Stop'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED = '1'
. $Helper
$script:worker = $null
$script:readOriginal = (Get-Item function:Read-NativeDaemonFrame).ScriptBlock
function Read-NativeDaemonFrame {
    param([IO.Pipes.PipeStream]$Pipe, [int]$Length, [int]$Timeout = 15000)
    $frame = & $script:readOriginal -Pipe $Pipe -Length $Length -Timeout $Timeout
    $actual = [CopilotExtensions.NativeDaemonAdmission]::PipeProcess($Pipe, $true)
    if ($Length -ne 4 -or $actual -ne [BitConverter]::ToInt32($frame, 0)) {
        throw 'fixture peer identity mismatch'
    }
    $script:worker = [Diagnostics.Process]::GetProcessById($actual)
    [void]$script:worker.Handle
    if ($Mode -eq 'mismatch') { return ,([BitConverter]::GetBytes([int]$PID)) }
    if ($Mode -eq 'unconfirmed-cleanup') {
        # Lose only the launcher's native pin; the independent observer still owns its own handle.
        (Get-Variable -Name launcher -Scope 1).Value.Dispose()
    }
    throw 'fixture preack failure'
}
$entry = Join-Path $Root 'entry.ps1'
[IO.File]::WriteAllText($entry,
    "[IO.File]::WriteAllText((Join-Path `$PSScriptRoot 'started'),'unexpected entry point')")
try {
    $failure = $null
    try {
        $unexpected = Start-NativeDaemon -Script $entry -Arguments @('unused') `
            -WorkingDirectory $Root -LogPath (Join-Path $Root 'worker.log')
        $unexpected.Dispose()
    } catch {
        $failure = $_.Exception
        while ($failure.InnerException -and $failure -isnot [CopilotExtensions.NativeDaemonSpawnException]) {
            $failure = $failure.InnerException
        }
    }
    if ($null -eq $failure -or $failure -isnot [CopilotExtensions.NativeDaemonSpawnException]) {
        throw 'expected a typed native admission failure'
    }
    if ($null -eq $script:worker -or -not $script:worker.WaitForExit(10000)) {
        throw 'fixture worker did not retire'
    }
    @{
        settled = $failure.CleanupConfirmed
        workerCode = $script:worker.ExitCode
        error = $failure.InnerException.Message
    } | ConvertTo-Json -Compress
} finally {
    if ($script:worker) {
        try {
            if (-not $script:worker.HasExited) {
                $script:worker.Kill()
                [void]$script:worker.WaitForExit(5000)
            }
        } finally { $script:worker.Dispose() }
    }
}
