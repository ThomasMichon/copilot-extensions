$ErrorActionPreference = 'Stop'
$clock = [Diagnostics.Stopwatch]::StartNew()
$root = if ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT } elseif ($env:PLUGIN_ROOT) {
    $env:PLUGIN_ROOT
} elseif ($env:CLAUDE_PLUGIN_ROOT) { $env:CLAUDE_PLUGIN_ROOT } else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path $root 'scripts/write_session_guidance.py'
$diagnostic = 'budget-guidance: session cue could not be written'

function Invoke-CueCapture {
    param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment, [string]$InputText = '')
    # uv inspection and the direct, child-free writer share the original clock.
    # Do not put an intermediary `uv run` under this direct-process owner.
    $remaining = [int][Math]::Max(0, 6000 - $clock.ElapsedMilliseconds)
    if ($remaining -le 1000) { throw 'session cue deadline exceeded' }
    $errors = ''
    $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
        -InputText $InputText -ErrorOutput ([ref]$errors) `
        -TimeoutMilliseconds $remaining -CleanupMilliseconds 1000
    return [pscustomobject]@{
        ExitCode = $global:LASTEXITCODE; Stdout = [string]$output; Stderr = $errors
    }
}

try {
    $provider = Join-Path $root 'scripts/bootstrap-uv.ps1'
    $capture = Join-Path $root 'scripts/versioned_runtime_native.ps1'
    $request = Join-Path $root 'scripts/bootstrap-python-version.txt'
    foreach ($path in @($provider, $capture, $request, $script)) {
        if (-not [IO.File]::Exists($path)) {
            $diagnostic = 'budget-guidance: managed cue prerequisites are unavailable; run the owning scripts/install.ps1 install'
            throw 'writer prerequisites unavailable'
        }
    }
    . $provider
    . $capture
    $selected = Resolve-UvBootstrapPython -RequestFile $request -Operation Inspect -IsolateSelection `
        -InvokeCaptured {
            param($FilePath, $ArgList, $Environment)
            Invoke-CueCapture -FilePath $FilePath -ArgList $ArgList -Environment $Environment
        }
    if ($selected.Status -cne 'selected') {
        $diagnostic = 'budget-guidance: managed cue prerequisites are unavailable; run the owning scripts/install.ps1 install'
        throw 'managed writer unavailable'
    }
    $buffer = New-Object char[] 65537
    $length = [Console]::In.ReadBlock($buffer, 0, $buffer.Length)
    if ($length -gt 65536) { throw 'input too large' }
    $payload = [string]::new($buffer, 0, $length)
    $result = Invoke-CueCapture -FilePath $selected.Python -ArgList @('-I', '-S', $script) `
        -Environment (Get-UvBootstrapEnvironment) -InputText $payload
    if ($result.ExitCode -ne 0 -or $result.Stdout -cne '{}') { throw 'writer failed' }
} catch {
    [Console]::Error.WriteLine($diagnostic)
} finally {
    [Console]::Out.Write('{}')
}
exit 0
