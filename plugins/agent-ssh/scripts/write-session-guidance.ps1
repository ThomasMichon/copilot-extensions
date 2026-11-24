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
$helper = Join-Path (Join-Path $root 'scripts') 'hook-uv-python.ps1'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf) -or
    -not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Out.Write('{}')
    exit 0
}
. $helper
try {
    Invoke-HookUvPython -ArgumentList @('-B', $script)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Out.Write('{}')
    }
} catch {
    [Console]::Out.Write('{}')
}
exit 0
