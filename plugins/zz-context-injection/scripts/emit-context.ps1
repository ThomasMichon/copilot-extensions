$root = $env:COPILOT_PLUGIN_ROOT
if (-not $root) { $root = $env:PLUGIN_ROOT }
if (-not $root) { $root = $env:CLAUDE_PLUGIN_ROOT }
if (-not $root) {
    [Console]::Out.Write('{}')
    exit 0
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    [Console]::Error.WriteLine(
        '[zz-context-injection] Python is unavailable; context aggregation disabled'
    )
    [Console]::Out.Write('{}')
    exit 0
}

& $python.Source (Join-Path (Join-Path $root 'scripts') 'aggregate_context.py')
exit $LASTEXITCODE
