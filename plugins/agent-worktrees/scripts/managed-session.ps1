function Get-ManagedWorktreesPython {
    $python = $env:AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON
    if ($python -and $python -notlike '*\WindowsApps\*' -and
        [IO.Path]::IsPathRooted($python) -and [IO.File]::Exists($python)) { return $python }
    $runtimeRoot = Join-Path $env:USERPROFILE '.agent-worktrees'
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $validator = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
        if (-not [IO.File]::Exists($validator)) { return $null }
        $owner = if ($env:AGENT_WORKTREES_PAYLOAD_ROOT) { $env:AGENT_WORKTREES_PAYLOAD_ROOT }
                 elseif ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT }
                 else { Split-Path -Parent $PSScriptRoot }
        $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
        1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
        $validation = @(& $validator -Action validate -Context $env:COPILOT_EXTENSIONS_CONTEXT `
            -DurableHome $durableHome -ExpectedPluginId 'agent-worktrees' -ExpectedPayloadRoot $owner)
        if ($LASTEXITCODE -ne 0) { return $null }
        $context = ConvertFrom-Json -InputObject ($validation -join "`n") -ErrorAction Stop
        if ($context.pluginRoot -isnot [string] -or
            -not [IO.Path]::IsPathRooted($context.pluginRoot)) { return $null }
        $runtimeRoot = $context.pluginRoot
    }
    $python = $null
    $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
    if ([IO.File]::Exists($resolver)) {
        $previousRoot = $env:AGENT_RT_ROOT
        try {
            $env:AGENT_RT_ROOT = $runtimeRoot
            . $resolver -TraceMode read-only
            $python = $AwPy
        } finally {
            if ($null -eq $previousRoot) { Remove-Item Env:AGENT_RT_ROOT -ErrorAction SilentlyContinue }
            else { $env:AGENT_RT_ROOT = $previousRoot }
        }
    }
    if (-not $python -and -not $env:COPILOT_EXTENSIONS_CONTEXT) {
        $python = Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        Select-Object -First 1 -ExpandProperty Source
    }
    return $python
}

function Test-ManagedWorktreesSession {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    $helper = Join-Path $PSScriptRoot 'managed_session.py'
    if (-not [IO.File]::Exists($helper)) { return $false }
    $process = $null
    try {
        if (-not $Raw.TrimStart().StartsWith('{')) { return $false }
        $event = ConvertFrom-Json -InputObject $Raw -ErrorAction Stop
        $query = @{}
        foreach ($name in @('sessionId', 'cwd', 'workingDirectory')) {
            if ($event.PSObject.Properties.Name -ccontains $name) {
                $query[$name] = $event.$name
            }
        }
        $frame = ConvertTo-Json -InputObject $query -Compress
        $python = Get-ManagedWorktreesPython
        if (-not $python) { return $false }
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $python
        $info.Arguments = '-I -B -X utf8 "' + $helper + '" --base64-input'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $utf8 = [Text.UTF8Encoding]::new($false)
        $info.StandardOutputEncoding = $utf8
        $info.StandardErrorEncoding = $utf8
        $process = [Diagnostics.Process]::Start($info)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $bytes = $utf8.GetBytes([Convert]::ToBase64String($utf8.GetBytes($frame)) + "`n")
        $writing = $process.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
        if (-not $writing.Wait(2000) -or
            -not $process.StandardInput.BaseStream.FlushAsync().Wait(2000)) { return $false }
        if (-not $process.WaitForExit(2000) -or
            -not [Threading.Tasks.Task]::WaitAll(@($stdout, $stderr), 1000) -or
            $process.ExitCode -ne 0 -or $stdout.Result.Length -gt 65536) { return $false }
        $result = ConvertFrom-Json -InputObject $stdout.Result -ErrorAction Stop
        return $result.admitted -is [bool] -and $result.admitted
    } catch {
        return $false
    } finally {
        if ($process) {
            try { $process.StandardInput.Close() } catch {}
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
            $process.Dispose()
        }
    }
}
