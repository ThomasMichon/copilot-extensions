# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
$ErrorActionPreference = 'SilentlyContinue'

$root = if ($env:COPILOT_PLUGIN_ROOT) {
    $env:COPILOT_PLUGIN_ROOT
} elseif ($env:PLUGIN_ROOT) {
    $env:PLUGIN_ROOT
} elseif ($env:CLAUDE_PLUGIN_ROOT) {
    $env:CLAUDE_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path (Join-Path $root 'scripts') 'write_session_guidance.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Out.Write('{}')
    exit 0
}
$env:PYTHONPATH = ''
try {
    . (Join-Path (Join-Path $root 'scripts') 'resolve-bootstrap-python.ps1')
    $python = Resolve-PayloadBootstrapPython -ContextFree -ErrorAction Stop
    & $python -I -S -B $script
    if ($LASTEXITCODE -ne 0) {
        [Console]::Out.Write('{}')
    }
} catch {
    [Console]::Out.Write('{}')
}
exit 0