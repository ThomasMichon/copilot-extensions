[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [Parameter(Mandatory = $true)][string]$PluginId,
    [Parameter(Mandatory = $true)][string]$LegacyRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$helper = Join-Path $PSScriptRoot 'installation-context.ps1'
$probe = @()
try {
    $probe = @(& $helper -Action probe-plugin-publication -PayloadRoot $PayloadRoot `
        -PluginId $PluginId -LegacyRoot $LegacyRoot)
    $code = $LASTEXITCODE
    $decision = ($probe -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($code -eq 0 -and
        $decision.deployment.allowPluginPublication -is [bool] -and
        $decision.deployment.allowPluginPublication -and
        $decision.legacy.root -is [string] -and $decision.legacy.root -and
        [string]$decision.deployment.path -ceq
            (Join-Path $decision.legacy.root 'runtime-deployment.json')) {
        $legacyProbe = @(& $helper -Action probe-legacy -PayloadRoot $PayloadRoot `
            -PluginId $PluginId -LegacyRoot $LegacyRoot)
        $legacyCode = $LASTEXITCODE
        $probe += $legacyProbe
        $legacy = ($legacyProbe -join "`n") | ConvertFrom-Json -ErrorAction Stop
        if ($legacyCode -eq 0 -and $legacy.allowMutation -is [bool] -and
            $legacy.allowMutation -and
            [string]$legacy.legacy.root -ceq [string]$decision.legacy.root) {
            exit 0
        }
    }
} catch {
    [Console]::Error.WriteLine("[$PluginId] Runtime publication probe failed: $($_.Exception.Message)")
}
[Console]::Error.WriteLine(
    "[$PluginId] Runtime publication refused: $($probe -join ' ') " +
    'Use explicit installation management to install or repair this runtime.'
)
exit 3
