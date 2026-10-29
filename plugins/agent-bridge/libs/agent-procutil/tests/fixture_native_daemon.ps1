param(
    [ValidateSet('submit', 'worker')][string]$Mode,
    [string]$Root,
    [string]$Helper,
    [string]$Custody
)
$ErrorActionPreference = 'Stop'
if ($Mode -eq 'submit') {
    . $Helper
    $worker = Start-NativeDaemon -Script $PSCommandPath `
        -Arguments @('worker', $Root, $Helper, $Custody) -WorkingDirectory $Root `
        -LogPath (Join-Path $Root 'worker.log')
    try {
        [IO.File]::WriteAllText((Join-Path $Root 'worker.json'), (@{
            pid = $worker.Id; birth = [CopilotExtensions.DaemonInstanceNative]::Birth($worker)
        } | ConvertTo-Json))
    } finally {
        $worker.Dispose()
    }
    exit 0
}

. $Custody
$lease = Enter-NativeOperationLock (Join-Path $Root 'bootstrap.lock')
try {
    $lease.AssertOwned()
    [IO.File]::WriteAllText((Join-Path $Root 'started'), 'no Python or uv')
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while (-not [IO.File]::Exists((Join-Path $Root 'continue'))) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'native worker fixture deadline expired' }
        Start-Sleep -Milliseconds 20
    }
    $lease.AssertOwned()
    . (Join-Path (Split-Path $Helper) 'native_owned.ps1')
    $code = Invoke-NativeOwnedProcess -FilePath (Join-Path $PSHOME 'powershell.exe') `
        -Arguments @('-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
            "[Console]::Out.WriteLine('post-caller native prerequisite'); exit 7") `
        -WorkingDirectory $Root -LogPath (Join-Path $Root 'owned.log') -TimeoutMilliseconds 10000
    $lease.AssertOwned()
    if ($code -ne 7) { throw "native prerequisite returned $code" }
    [IO.File]::WriteAllText((Join-Path $Root 'result'), "completed:$code")
} finally {
    $lease.Dispose()
}
exit 7
