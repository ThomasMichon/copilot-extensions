$ErrorActionPreference = 'Stop'
try {
    $pluginRoot = if ($env:COPILOT_PLUGIN_ROOT) {
        $env:COPILOT_PLUGIN_ROOT
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    $guide = Join-Path (Join-Path $pluginRoot 'references') 'contribution-ground-rules.md'
    $manifest = Join-Path $pluginRoot 'plugin.json'
    if (-not (Test-Path -LiteralPath $guide) -or -not (Test-Path -LiteralPath $manifest)) {
        throw 'plugin payload incomplete'
    }
    $version = (Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json).version
    $context = "[owner: copilot-extensions-harness@$version] copilot-extensions accepts only general-purpose, organization-neutral capabilities; personal needs belong in the adopter's private control repo and organization-specific work in its internal marketplace. Read: $guide"
    Write-Output (@{ additionalContext = $context } | ConvertTo-Json -Compress)
} catch {
    Write-Output '{}'
}
exit 0
