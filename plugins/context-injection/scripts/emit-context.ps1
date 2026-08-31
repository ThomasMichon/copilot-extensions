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
        '[context-injection] Python is unavailable; context aggregation disabled'
    )
    [Console]::Out.Write('{}')
    exit 0
}

$temp = [System.IO.Path]::GetTempFileName()
try {
    & $python.Source (Join-Path (Join-Path $root 'scripts') 'aggregate_context.py') `
        > $temp
    if ($LASTEXITCODE -eq 0) {
        [Console]::Out.Write((Get-Content -Raw -LiteralPath $temp))
    }
    else {
        [Console]::Error.WriteLine(
            '[context-injection] aggregator failed; direct context retained'
        )
        [Console]::Out.Write('{}')
    }
}
finally {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
}
exit 0
