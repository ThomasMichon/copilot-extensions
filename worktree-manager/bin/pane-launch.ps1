#Requires -Version 7.0
param([Parameter(Mandatory)][string]$Manifest)

try {
    $stream = [IO.FileStream]::new(
        $Manifest, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose
    )
    $reader = [IO.StreamReader]::new($stream)
    # ConvertFrom-Json on older PowerShell versions coerces date-shaped argv.
    $document = [System.Text.Json.JsonDocument]::Parse($reader.ReadToEnd())
    $handoff = $document.RootElement
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
    if ([string]::IsNullOrWhiteSpace($wrapper) -or -not (Test-Path -LiteralPath $wrapper -PathType Leaf)) {
        throw 'Pane wrapper does not exist.'
    }
} catch {
    Write-Error "Could not consume pane argument handoff: $($_.Exception.Message)"
    exit 3
} finally {
    if ($document) { $document.Dispose() }
    if ($reader) { $reader.Dispose() }
    if ($stream) { $stream.Dispose() }
}

try {
    & $wrapper @paneArgs
} catch {
    Write-Error "Pane wrapper launch failed: $($_.Exception.Message)"
    exit 3
}
exit $LASTEXITCODE
