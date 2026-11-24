# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
$ErrorActionPreference = 'Stop'
$clock = [Diagnostics.Stopwatch]::StartNew()

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
    $bootstrap = Join-Path $root 'scripts\_bootstrap_python'
    $python = & (Join-Path $bootstrap 'select.ps1') -Operation Inspect `
        -TimeoutMilliseconds 30000
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        . (Join-Path $bootstrap 'bootstrap-capture.ps1')
    } else {
        . (Join-Path $bootstrap 'versioned_runtime_native.ps1')
    }
    $buffer = New-Object char[] 65537
    $reader = [IO.StreamReader]::new(
        [Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false, $true)
    )
    try { $length = $reader.ReadBlock($buffer, 0, $buffer.Length) } finally { $reader.Dispose() }
    $payload = [string]::new($buffer, 0, $length)
    if ([Text.Encoding]::UTF8.GetByteCount($payload) -gt 65536) { throw 'Input is oversized.' }
    $remaining = 40000 - $clock.ElapsedMilliseconds
    if ($remaining -le 1000) { throw 'Session guidance hook deadline expired.' }
    $cleanup = [int][Math]::Min(3000, [Math]::Floor($remaining / 4))
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $result = Invoke-BootstrapOwnedCapture -FilePath $python `
            -ArgList @('-I', '-B', '-X', 'utf8', $script) -InputText $payload `
            -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
        if ($result.ExitCode -ne 0 -or $result.Stdout -cne '{}') { throw 'Writer failed.' }
    } else {
        $diagnostic = ''
        $output = Invoke-Hidden -FilePath $python `
            -ArgList @('-I', '-B', '-X', 'utf8', $script) -InputText $payload `
            -ErrorOutput ([ref]$diagnostic) -TimeoutMilliseconds ([int]$remaining) `
            -CleanupMilliseconds $cleanup -CaptureEncoding ([Text.UTF8Encoding]::new($false))
        if ($LASTEXITCODE -ne 0 -or $output -cne '{}') { throw 'Writer failed.' }
    }
} catch { } finally {
    [Console]::Out.Write('{}')
}
exit 0