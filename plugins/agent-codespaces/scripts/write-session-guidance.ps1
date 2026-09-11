# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
$ErrorActionPreference = 'SilentlyContinue'

$root = if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    Split-Path -Parent $PSScriptRoot
} elseif ($env:COPILOT_PLUGIN_ROOT) {
    $env:COPILOT_PLUGIN_ROOT
} elseif ($env:PLUGIN_ROOT) {
    $env:PLUGIN_ROOT
} elseif ($env:CLAUDE_PLUGIN_ROOT) {
    $env:CLAUDE_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path (Join-Path $root 'scripts') 'write_session_guidance.py'
$python = $null
foreach ($candidate in @('python3', 'python', 'py')) {
    $found = Get-Command $candidate -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } |
        Select-Object -First 1
    if ($found) {
        $python = $found
        break
    }
}
if (-not $python -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        [Console]::Error.WriteLine('CodeSpaces guidance refused: Python or writer is unavailable')
        exit 126
    }
    [Console]::Out.Write('{}')
    exit 0
}
$env:PYTHONPATH = ''
try {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        & $python.Source -I -X utf8 $script
        exit $LASTEXITCODE
    }
    & $python.Source $script
    if ($LASTEXITCODE -ne 0) {
        [Console]::Out.Write('{}')
    }
} catch {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        [Console]::Error.WriteLine("CodeSpaces guidance refused: $_")
        exit 126
    }
    [Console]::Out.Write('{}')
}
exit 0
