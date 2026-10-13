#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Manifest,
    [string]$Receipt
)

if ($Receipt -and (
    [IO.Path]::GetFullPath($Receipt) -ne [IO.Path]::GetFullPath("$Manifest.receipt.json") -or
    ((Test-Path -LiteralPath $Receipt) -and
        ((Get-Item -LiteralPath $Receipt).Attributes -band [IO.FileAttributes]::ReparsePoint))
)) {
    Write-Error 'Invalid pane argument receipt path.'
    exit 3
}

$stage = 'manifest-open'
$handoffError = $null
try {
    $stream = [IO.FileStream]::new(
        $Manifest, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose
    )
    $reader = [IO.StreamReader]::new($stream)
    $stage = 'manifest-json'
    # ConvertFrom-Json on older PowerShell versions coerces date-shaped argv.
    $document = [System.Text.Json.JsonDocument]::Parse($reader.ReadToEnd())
    $handoff = $document.RootElement
    $stage = 'manifest-schema'
    $version = $handoff.GetProperty('version')
    $wrapperElement = $handoff.GetProperty('wrapper')
    $argv = $handoff.GetProperty('argv')
    if (
        $version.ValueKind -ne [System.Text.Json.JsonValueKind]::Number -or
        $version.GetInt32() -ne 1 -or
        $wrapperElement.ValueKind -ne [System.Text.Json.JsonValueKind]::String -or
        $argv.ValueKind -ne [System.Text.Json.JsonValueKind]::Array -or
        $argv.GetArrayLength() -eq 0
    ) {
        throw 'Pane argument manifest requires version 1, a wrapper path and a non-empty string argv.'
    }
    $wrapper = $wrapperElement.GetString()
    $argumentList = [Collections.Generic.List[string]]::new()
    foreach ($argument in $argv.EnumerateArray()) {
        if ($argument.ValueKind -ne [System.Text.Json.JsonValueKind]::String) {
            throw 'Pane argv contains a non-string value.'
        }
        $argumentList.Add($argument.GetString())
    }
    $paneArgs = @($argumentList.ToArray())
    $stage = 'wrapper-path'
    if ([string]::IsNullOrWhiteSpace($wrapper) -or -not (Test-Path -LiteralPath $wrapper -PathType Leaf)) {
        throw 'Pane wrapper does not exist.'
    }
} catch {
    $handoffError = $_
} finally {
    if ($document) { $document.Dispose() }
    if ($reader) { $reader.Dispose() }
    if ($stream) { $stream.Dispose() }
}

if ($Receipt) {
    $receiptTemp = "$Receipt.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        $receiptJson = ConvertTo-Json -InputObject @{
            version = 1
            manifest = [IO.Path]::GetFileName($Manifest)
            status = if ($handoffError) { 'rejected' } else { 'consumed' }
            stage = $stage
            pid = $PID
        } -Compress
        [IO.File]::WriteAllText($receiptTemp, $receiptJson)
        [IO.File]::Move($receiptTemp, $Receipt)
    } catch {
        Write-Error 'Could not publish pane argument receipt.'
        exit 3
    } finally {
        if (Test-Path -LiteralPath $receiptTemp) { [IO.File]::Delete($receiptTemp) }
    }
}
if ($handoffError) {
    Write-Error "Could not consume pane argument handoff ($stage; $($handoffError.Exception.GetType().Name))."
    exit 3
}

try {
    & $wrapper @paneArgs
} catch {
    Write-Error "Pane wrapper launch failed: $($_.Exception.Message)"
    exit 3
}
exit $LASTEXITCODE
