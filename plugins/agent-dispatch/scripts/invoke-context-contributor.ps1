param(
    [Parameter(Position = 0)][string]$SourceId,
    [Parameter(Position = 1)][string]$ContributorId,
    [Parameter(Position = 2)][string]$RelativeScript,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ContributorArgs
)

$root = $env:COPILOT_PLUGIN_ROOT
if (-not $root) { $root = $env:PLUGIN_ROOT }
if (-not $root) { $root = $env:CLAUDE_PLUGIN_ROOT }
if (-not $root -or -not $SourceId -or -not $ContributorId -or -not $RelativeScript) {
    [Console]::Out.Write('{}')
    exit 0
}

try {
    $resolvedRoot = [IO.Path]::GetFullPath($root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $script = [IO.Path]::GetFullPath((Join-Path $resolvedRoot $RelativeScript))
    $prefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (
        -not $script.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($script) -ne '.ps1'
    ) {
        throw 'Contributor script escapes its payload root'
    }
}
catch {
    [Console]::Out.Write('{}')
    exit 0
}

$authority = Join-Path (Split-Path -Parent $resolvedRoot) 'context-injection'
$engine = Join-Path (Join-Path $authority 'scripts') 'aggregate_context.py'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if ($python -and (Test-Path -LiteralPath $engine -PathType Leaf)) {
    $output = [IO.Path]::GetTempFileName()
    try {
        & $python.Source $engine --producer "$SourceId/$ContributorId" > $output
        if ($LASTEXITCODE -eq 0) {
            [Console]::Out.Write((Get-Content -Raw -LiteralPath $output))
        }
        else {
            [Console]::Error.WriteLine(
                "[$SourceId] context authority failed after selection; context suppressed"
            )
            [Console]::Out.Write('{}')
        }
    }
    finally {
        Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

if (Test-Path -LiteralPath $script -PathType Leaf) {
    & $script @ContributorArgs
}
else {
    [Console]::Out.Write('{}')
}
exit 0
