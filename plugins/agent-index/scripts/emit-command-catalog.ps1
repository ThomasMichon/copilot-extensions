# Emit the exact payload-owned agent-index invocation into session context.
$ErrorActionPreference = 'SilentlyContinue'

$selfRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$contextRoot = if ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT } else { $selfRoot }
try { $contextRoot = (Resolve-Path $contextRoot).Path } catch { Write-Output '{}'; exit 0 }
if (-not [StringComparer]::OrdinalIgnoreCase.Equals($contextRoot, $selfRoot)) {
    Write-Output '{}'
    exit 0
}

$commandPath = Join-Path $selfRoot 'bin\agent-index.ps1'
$availability = if (Test-Path -LiteralPath $commandPath) { 'ready' } else { 'unavailable' }
$catalog = [ordered]@{
    schema = 'copilot-extensions.session-command-catalog'
    version = 1
    plugin = 'agent-index'
    payload = [ordered]@{ provenance = 'payload-local' }
    commands = @(
        [ordered]@{
            id = 'agent-index'
            argv = @($commandPath)
            shell = 'direct'
            purpose = 'Search and operate the semantic index'
            availability = $availability
        }
    )
}
$catalogJson = $catalog | ConvertTo-Json -Compress -Depth 6
$fence = '```'
$context = @(
    '## agent-index session command catalog'
    ''
    'Invoke the exact `argv` below. Do not search `PATH` or substitute a same-named command from another payload.'
    ''
    "${fence}json"
    $catalogJson
    $fence
) -join "`n"
Write-Output (@{ additionalContext = $context } | ConvertTo-Json -Compress -Depth 3)
exit 0
