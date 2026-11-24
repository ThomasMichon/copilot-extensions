# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
$ErrorActionPreference = 'Stop'

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
try {
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'Writer is unavailable.' }
    $python = . (Join-Path $root 'scripts\hook-bootstrap-python.ps1') `
        -ScriptsRoot (Join-Path $root 'scripts')
    $buffer = New-Object char[] 65537
    $reader = [IO.StreamReader]::new(
        [Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false, $true)
    )
    try { $length = $reader.ReadBlock($buffer, 0, $buffer.Length) } finally { $reader.Dispose() }
    $payload = [string]::new($buffer, 0, $length)
    if ([Text.Encoding]::UTF8.GetByteCount($payload) -gt 65536) { throw 'Input is oversized.' }
    $result = Invoke-LoggerHookCapture -FilePath $python `
        -ArgList @('-I', '-B', '-X', 'utf8', $script) -InputText $payload
    if ($result.ExitCode -ne 0 -or $result.Stdout -cne '{}') { throw 'Writer failed.' }
} catch { } finally {
    [Console]::Out.Write('{}')
}
exit 0