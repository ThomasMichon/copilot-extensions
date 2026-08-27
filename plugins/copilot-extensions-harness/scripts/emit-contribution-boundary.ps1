$ErrorActionPreference = 'SilentlyContinue'
$pluginRoot = if ($env:COPILOT_PLUGIN_ROOT) {
    $env:COPILOT_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$guide = Join-Path (Join-Path $pluginRoot 'references') 'contribution-ground-rules.md'
if (-not (Test-Path -LiteralPath $guide)) {
    Write-Output '{}'
    exit 0
}
$context = "Copilot-extensions accepts only general-purpose, organization-neutral capabilities; personal needs belong in the adopter's private control repo and organization-specific work in its internal marketplace. Read: $guide"
Write-Output (@{ additionalContext = $context } | ConvertTo-Json -Compress)
