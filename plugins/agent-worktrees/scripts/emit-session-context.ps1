# Pure aggregate-mode context producer; direct sessionStart hooks remain separate.
$ErrorActionPreference = 'SilentlyContinue'

$root = if ($env:COPILOT_PLUGIN_ROOT) {
    $env:COPILOT_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path (Join-Path $root 'scripts') 'emit_session_context.py'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Out.Write('{}')
    exit 0
}
& $python.Source $script
if ($LASTEXITCODE -ne 0) {
    [Console]::Out.Write('{}')
}
exit 0
