# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext -- a raw hooks.json entry, not a declared
# session-context.json contributor. See scripts/write_session_guidance.py.
$ErrorActionPreference = 'SilentlyContinue'

$root = if ($env:COPILOT_PLUGIN_ROOT) {
    $env:COPILOT_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path (Join-Path $root 'scripts') 'write_session_guidance.py'
$python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
if ($python -and $python.Source -match '\\WindowsApps\\') { $python = $null }
if (-not $python) {
    $python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
}
if (-not $python -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Out.Write('{}')
    exit 0
}
$env:PYTHONPATH = ''
& $python.Source $script
if ($LASTEXITCODE -ne 0) {
    [Console]::Out.Write('{}')
}
exit 0
