$ErrorActionPreference = 'Stop'
$root = if ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT } elseif ($env:PLUGIN_ROOT) {
    $env:PLUGIN_ROOT
} elseif ($env:CLAUDE_PLUGIN_ROOT) { $env:CLAUDE_PLUGIN_ROOT } else {
    Split-Path -Parent $PSScriptRoot
}
$script = Join-Path $root 'scripts/write_session_guidance.py'
$python = $null
foreach ($candidate in @('python3', 'python')) {
    $found = Get-Command $candidate -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    if ($found) { $python = $found.Source; break }
}

function Resolve-ConsolePython([string]$source) {
    for ($depth = 0; $depth -lt 3; $depth++) {
        if ([IO.Path]::GetFileName($source) -ieq 'py.exe') { throw 'launcher unsupported' }
        $directory = Split-Path -Parent $source
        $config = Join-Path (Split-Path -Parent $directory) 'pyvenv.cfg'
        if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $source }
        if ((Get-Item -LiteralPath $config).Length -gt 4096) { throw 'venv metadata too large' }
        $homes = @([IO.File]::ReadAllLines($config) | Where-Object { $_ -match '^\s*home\s*=' })
        if ($homes.Count -ne 1) { throw 'venv home unavailable' }
        $homePath = ($homes[0] -split '=', 2)[1].Trim()
        if (-not [IO.Path]::IsPathRooted($homePath)) { throw 'venv home is relative' }
        $source = Join-Path $homePath 'python.exe'
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'base interpreter unavailable' }
    }
    throw 'base interpreter did not resolve'
}

$process = $null
try {
    if (-not $python -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
        throw 'writer unavailable'
    }
    # A venv redirector/py launcher would make root-only cancellation incomplete.
    # The isolated writer itself starts no descendants, so launch its base directly.
    $python = Resolve-ConsolePython $python
    $buffer = New-Object char[] 65537
    $length = [Console]::In.ReadBlock($buffer, 0, $buffer.Length)
    if ($length -gt 65536) { throw 'input too large' }
    $payload = [string]::new($buffer, 0, $length)
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $python
    $psi.Arguments = '-I -S "' + $script.Replace('"', '\"') + '"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    $process = [Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.AutoFlush = $true
    $inputDelivery = $process.StandardInput.WriteAsync($payload)
    $remaining = [int][Math]::Max(0, 5000 - $deadline.ElapsedMilliseconds)
    if (-not $inputDelivery.Wait($remaining)) { throw 'writer input deadline exceeded' }
    $process.StandardInput.Close()
    $remaining = [int][Math]::Max(0, 5000 - $deadline.ElapsedMilliseconds)
    if (-not $process.WaitForExit($remaining)) {
        $process.Kill()
        if (-not $process.WaitForExit(1000)) { throw 'writer cleanup exceeded deadline' }
        throw 'writer deadline exceeded'
    }
    if (-not [Threading.Tasks.Task]::WaitAll(
        [Threading.Tasks.Task[]]@($stdout, $stderr), 1000
    )) { throw 'writer streams did not close' }
    if ($process.ExitCode -ne 0 -or $stdout.Result -cne '{}') { throw 'writer failed' }
} catch {
    [Console]::Error.WriteLine('budget-guidance: session cue could not be written')
} finally {
    if ($process) {
        try {
            if (-not $process.HasExited) {
                $process.Kill()
                [void]$process.WaitForExit(1000)
            }
        } catch {
            [Console]::Error.WriteLine('budget-guidance: session cue process cleanup was not confirmed')
        } finally {
            $process.Dispose()
        }
    }
    [Console]::Out.Write('{}')
}
exit 0
