param([string]$Root, [string]$Plugin, [string]$Library, [string]$Mode)
$ErrorActionPreference = 'Stop'
. (Join-Path $Library 'single-instance-lease\src\single_instance_lease\operation.ps1')
. (Join-Path $Library 'update-driver\src\update_driver\foundation.ps1')
$utf8 = New-Object Text.UTF8Encoding($false)
function Save([string]$Name, [string]$Value) {
    [IO.File]::WriteAllText((Join-Path $Root $Name), $Value, $utf8)
}
if ($Mode -eq 'protocol') {
    $results = @()
    foreach ($path in (Get-ChildItem -LiteralPath (Join-Path $Root 'corpus') -File | Sort-Object Name)) {
        [IO.File]::Copy($path.FullName, (Join-Path $Root 'foundation-operation.json'), $true)
        $result = @{ name = $path.Name; accepted = $false; document = $null }
        try {
            $result.document = Get-NativeFoundationProgress -Root $Root
            $result.accepted = $true
        } catch {
            $result.error = $_.Exception.Message
        }
        $results += $result
    }
    Save 'report.json' (ConvertTo-Json -InputObject $results -Depth 5 -Compress)
    exit 0
}
$owned = Enter-NativeOperationLock ([IO.Path]::Combine($Root, 'execution.lock'))
try {
    if ($Mode -eq 'refuse') {
        $refused = $false
        try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
        catch { $refused = $true; Save 'error.txt' $_.Exception.Message }
        if (-not $refused) { throw 'expected admission refusal' }
        exit 0
    }
    $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin
    Save 'admitted.json' $operation
    if ($Mode -eq 'hold') {
        Save 'native-ready' 'actual native progress persisted under custody'
        $elapsed = [Diagnostics.Stopwatch]::StartNew()
        while ($elapsed.ElapsedMilliseconds -lt 120000) {
            $owned.AssertOwned()
            Start-Sleep -Milliseconds 20
        }
        throw 'native progress fixture deadline expired'
    }
    $duplicate = $false
    try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
    catch { $duplicate = $true }
    if (-not $duplicate) { throw 'duplicate admission replaced active progress' }
    if ($null -ne (Get-NativeFoundationProgress -Root (Join-Path $Root 'absent'))) {
        throw 'absent status returned progress'
    }
    if (Test-Path -LiteralPath (Join-Path $Root 'absent')) { throw 'absent status created state' }
    if ($null -ne (Get-NativeFoundationProgress -Root (Join-Path $Root 'absent-parent\state'))) {
        throw 'nested-absent status returned progress'
    }
    if (Test-Path -LiteralPath (Join-Path $Root 'absent-parent')) { throw 'nested-absent status created state' }
    Save 'file-parent' 'ordinary file is not an absent directory'
    $refused = $false
    try { $null = Get-NativeFoundationProgress -Root (Join-Path $Root 'file-parent\state') }
    catch { $refused = $true }
    if (-not $refused) { throw 'file ancestor was treated as absent state' }
    Save 'observed.json' (Get-NativeFoundationProgress -Root $Root)
    $foreign = Enter-NativeOperationLock ([IO.Path]::Combine($Root, 'other.lock'))
    try {
        $refused = $false
        try { $null = Complete-NativeFoundationProgress -Custody $foreign -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0 }
        catch { $refused = $true }
        if (-not $refused) { throw 'foreign custody completed progress' }
    } finally { $foreign.Dispose() }
    $inputPath = Join-Path $Plugin 'source.py'
    $original = [IO.File]::ReadAllBytes($inputPath)
    try {
        [IO.File]::WriteAllText($inputPath, 'changed after admission', $utf8)
        $refused = $false
        try { $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0 }
        catch { $refused = $true }
        if (-not $refused) { throw 'source substitution completed successfully' }
    } finally { [IO.File]::WriteAllBytes($inputPath, $original) }
    $reader = [IO.File]::Open((Join-Path $Root 'foundation-operation.json'), 'Open', 'Read', 'Read')
    try {
        $refused = $false
        try { $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0 }
        catch { $refused = $true }
        if (-not $refused) { throw 'unshareable reader did not refuse bounded publication' }
        if ((Get-NativeFoundationProgress -Root $Root) -ne $operation) { throw 'failed publication discarded progress' }
    } finally { $reader.Dispose() }
    Save 'completed.json' (Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0)
    $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin
    Save 'failed.json' (Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 7 -Reason builder_failed)
    $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin
    Save 'interrupted.json' (Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -Reason builder_settlement_unknown)
    $fenced = $false
    try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
    catch { $fenced = $true }
    if (-not $fenced) { throw 'uncertain progress was replayed' }
} finally {
    $owned.Dispose()
}
$refused = $false
try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
catch { $refused = $true }
if (-not $refused) { throw 'disposed custody admitted progress' }
