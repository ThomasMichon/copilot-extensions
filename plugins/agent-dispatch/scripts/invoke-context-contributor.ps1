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

$env:COPILOT_PLUGIN_ROOT = $resolvedRoot
$payload = ''
try {
    $payload = [Console]::In.ReadToEnd()
    $hook = $payload | ConvertFrom-Json -ErrorAction Stop
    $rawCwd = [string]$hook.cwd
    if (-not $rawCwd -or -not [IO.Path]::IsPathRooted($rawCwd)) {
        throw 'Hook payload cwd is unavailable'
    }
    $launchCwd = (Resolve-Path -LiteralPath $rawCwd -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $launchCwd -PathType Container)) {
        throw 'Hook payload cwd is not a directory'
    }
}
catch {
    [Console]::Out.Write('{}')
    exit 0
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
$resolver = Join-Path (Join-Path $resolvedRoot 'scripts') 'resolve_context_authority.py'
$authority = ''
if ($python -and (Test-Path -LiteralPath $resolver -PathType Leaf)) {
    try {
        $authority = (
            $payload | & $python.Source $resolver 2>$null | Out-String
        ).Trim()
    }
    catch { $authority = '' }
}
$engine = if ($authority) {
    Join-Path (Join-Path $authority 'scripts') 'aggregate_context.py'
} else {
    ''
}
if ($python -and $engine -and (Test-Path -LiteralPath $engine -PathType Leaf)) {
    $output = [IO.Path]::GetTempFileName()
    try {
        $payload | & $python.Source $engine --producer "$SourceId/$ContributorId" > $output
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
    $hostExecutable = [Environment]::ProcessPath
    if (-not $hostExecutable) {
        try {
            $hostExecutable = (Get-Process -Id $PID -ErrorAction Stop).Path
        }
        catch { }
    }
    if ($hostExecutable) {
        Push-Location -LiteralPath $launchCwd
        try {
            $payload | & $hostExecutable -NoProfile -File $script @ContributorArgs
        }
        finally {
            Pop-Location
        }
    }
    else {
        [Console]::Out.Write('{}')
    }
}
else {
    [Console]::Out.Write('{}')
}
exit 0
