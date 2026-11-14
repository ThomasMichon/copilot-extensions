$ErrorActionPreference = 'Stop'
try {
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $OutputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    $reader = [IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8, $false)
    try { $payload = $reader.ReadToEnd() } finally { $reader.Dispose() }
    $admission = Join-Path $PSScriptRoot 'managed-session.ps1'
    if (-not [IO.File]::Exists($admission)) { exit 0 }
    . $admission
    if (-not (Test-ManagedWorktreesSession -Raw $payload)) { exit 0 }
    $runtimeRoot = Get-ManagedWorktreesRuntimeRoot
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $pointer = $env:COPILOT_EXTENSIONS_CONTEXT
    }
    $previousRoot = $env:AGENT_RT_ROOT
    try {
        $env:AGENT_RT_ROOT = $runtimeRoot
        . (Join-Path $PSScriptRoot 'resolve-runtime.ps1') -TraceMode read-only
        $python = $AwPy
    } finally {
        if ($null -eq $previousRoot) { Remove-Item Env:AGENT_RT_ROOT -ErrorAction SilentlyContinue }
        else { $env:AGENT_RT_ROOT = $previousRoot }
    }
    if (-not $python) { throw 'Selected runtime unavailable' }
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        # Bootstrap only the owning SDK, without slot site code, before admitting the client.
        $owner = if ($env:AGENT_WORKTREES_PAYLOAD_ROOT) { $env:AGENT_WORKTREES_PAYLOAD_ROOT }
                 elseif ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT }
                 else { Split-Path -Parent $PSScriptRoot }
        $durableHome = $pointer
        1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
        $validator = Join-Path $PSScriptRoot 'installation-context\installation_context.py'
        $validation = @(& $python -I -S -B -X utf8 $validator validate `
            --context $pointer --durable-home $durableHome `
            --expected-plugin-id agent-worktrees --expected-payload-root $owner)
        if ($LASTEXITCODE -ne 0) { throw 'Invalid installation context' }
        $context = ConvertFrom-Json -InputObject ($validation -join "`n")
        if ($context.pluginRoot -isnot [string] -or
            [IO.Path]::GetFullPath($context.pluginRoot) -ine $runtimeRoot) {
            throw 'Invalid installation-context runtime root'
        }
    }
    $payload | & $python -I -B -X utf8 (Join-Path $PSScriptRoot 'hook_client.py') sessionEnd
    if ($LASTEXITCODE -ne 0) { throw 'SessionEnd client failed' }
} catch {
    [Console]::Error.WriteLine('[agent-worktrees] SessionEnd client unavailable; closure is unconfirmed.')
}
exit 0
