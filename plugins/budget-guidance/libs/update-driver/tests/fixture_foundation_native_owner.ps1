param([string]$Root, [string]$Helper)
$ErrorActionPreference = 'Stop'
[Console]::Out.WriteLine('loading native operation custody')
[Console]::Out.Flush()
. $Helper
$owned = Enter-NativeOperationLock (Join-Path $Root 'execution.lock')
try {
    $owned.AssertOwned()
    $document = [IO.File]::ReadAllText((Join-Path $Root 'fixture-operation.json'))
    $temporary = Join-Path $Root 'fixture-operation.tmp'
    [IO.File]::WriteAllText($temporary, $document)
    [IO.File]::Move($temporary, (Join-Path $Root 'foundation-operation.json'))
    [IO.File]::WriteAllText((Join-Path $Root 'native-ready'), 'actual execution custody held')
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    while ($elapsed.ElapsedMilliseconds -lt 30000) {
        $owned.AssertOwned()
        Start-Sleep -Milliseconds 20
    }
    throw 'native owner fixture observation deadline expired'
} finally {
    $owned.Dispose()
}
