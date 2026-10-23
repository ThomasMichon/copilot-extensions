param([string]$OperationHelper, [string]$ParticipantHelper, [string]$Root, [string]$Mode)
$ErrorActionPreference = 'Stop'
. $OperationHelper
. $ParticipantHelper
$lease = $null
$witness = $null
try {
    $lockPath = Join-Path $Root 'execution.lock'
    if ($Mode -eq 'wrong-lock') { $lockPath = Join-Path $Root 'other.lock' }
    $lease = Enter-NativeOperationLock $lockPath
    if ($Mode -eq 'disposed') { $lease.Dispose() }
    $witnessRoot = $Root
    if ($Mode -eq 'trailing-root') { $witnessRoot += '\' }
    try {
        $witness = New-NativeParticipantWitness -Custody $lease -Root $witnessRoot -RequestId ('a' * 32)
    } catch {
        if ($Mode -in @('wrong-lock', 'disposed')) {
            [Console]::Out.WriteLine('refused')
            exit 0
        }
        throw
    }
    [Console]::Out.WriteLine($witness.Environment())
    [Console]::Out.Flush()
    while ($null -ne ($command = [Console]::In.ReadLine())) {
        if ($command -eq 'release') {
            $lease.Dispose()
            [Console]::Out.WriteLine('released')
        } elseif ($command -eq 'replace-owner') {
            $lease.Dispose()
            $lease = Enter-NativeOperationLock (Join-Path $Root 'execution.lock')
            [Console]::Out.WriteLine('replaced')
        } elseif ($command -eq 'close') {
            $witness.Dispose()
            [Console]::Out.WriteLine('closed')
        } elseif ($command -eq 'stop') {
            break
        } else {
            throw 'Unknown fixture command.'
        }
        [Console]::Out.Flush()
    }
} finally {
    if ($witness) { $witness.Dispose() }
    if ($lease) { $lease.Dispose() }
}
