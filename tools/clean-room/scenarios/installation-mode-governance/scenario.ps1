<#
  Tier-P Windows proof for the read-only installation-mode resolver.
  Windows PowerShell 5.1 compatible; fixture state lives under the report
  directory and the mounted source remains read-only.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

if (-not $env:CR_SCENARIO_NAME) {
    $env:CR_SCENARIO_NAME = 'installation-mode-governance'
}
$LibPath = $env:CR_LIB
if (-not $LibPath) {
    $LibPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'lib\clean-room-lib.ps1'
}
. $LibPath

$ROOT = $env:CR_PARTNER_PATH
$MARKETPLACE = 'example--9caa0da95f327099'
$MARKETPLACE_KEY = 'example'
$PLUGIN = 'agent-example'
$SOURCE = [ordered]@{
    source = 'github'
    repo = 'example-org/example-marketplace'
}
$WORK = Join-Path (Split-Path -Parent $env:CR_REPORT) 'installation-mode-governance-state'
$EVALUATOR = if ($ROOT) {
    Join-Path $ROOT 'libs\installation-context\installation-context.ps1'
}
else {
    ''
}
$PROBE_UNKNOWN = '{"declared":false,"result":"unknown","checkedAt":null}'
$PROBE_ABSENT = '{"declared":true,"result":"absent","checkedAt":"2026-01-01T00:00:00Z"}'
$PROBE_PRESENT = '{"declared":true,"result":"present","checkedAt":"2026-01-01T00:00:00Z"}'

function Write-JsonNoBom([string]$Path, $Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText(
        $Path,
        $json + "`n",
        (New-Object Text.UTF8Encoding($false))
    )
}

function Reset-Case([string]$Name) {
    $path = Join-Path $WORK $Name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    $profile = Join-Path $path 'profile'
    $durable = Join-Path $path 'durable'
    $legacy = Join-Path $path 'legacy'
    $payload = Join-Path $path 'payload'
    New-Item -ItemType Directory -Force -Path $profile, $legacy, $payload | Out-Null
    [pscustomobject]@{
        root = $path
        profile = $profile
        durable = $durable
        legacy = $legacy
        payload = $payload
        policy = (Join-Path $profile '.copilot-extensions\installation-mode.json')
        probe = (Join-Path $path 'legacy-probe.json')
        source = (Join-Path $path 'source.json')
        context = $null
    }
}

function Get-TreeFingerprint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return ''
    }
    $lines = @()
    foreach ($item in Get-ChildItem -LiteralPath $Path -Recurse -Force | Sort-Object FullName) {
        $relative = $item.FullName.Substring($Path.Length).TrimStart('\')
        if ($item.PSIsContainer) {
            $lines += "D|$relative"
        }
        else {
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash
            $lines += "F|$relative|$hash"
        }
    }
    $lines -join "`n"
}

function Invoke-Mode($Case, [string]$ProbeJson) {
    Write-JsonNoBom $Case.probe ($ProbeJson | ConvertFrom-Json)
    Write-JsonNoBom $Case.source $SOURCE
    $previousProfile = $env:USERPROFILE
    try {
        $env:USERPROFILE = $Case.profile
        & powershell.exe -NoProfile -Command exit
    }
    finally {
        $env:USERPROFILE = $previousProfile
    }
    $before = Get-TreeFingerprint $Case.root
    $previousProfile = $env:USERPROFILE
    $previousContext = $env:COPILOT_EXTENSIONS_CONTEXT
    $previousPayload = $env:COPILOT_PLUGIN_ROOT
    $output = @()
    $code = 1
    try {
        $env:USERPROFILE = $Case.profile
        $env:COPILOT_EXTENSIONS_CONTEXT = $null
        $env:COPILOT_PLUGIN_ROOT = $null
        $arguments = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $EVALUATOR,
            'status',
            '-PayloadRoot', $Case.payload,
            '-PluginId', $PLUGIN,
            '-SourceFile', $Case.source,
            '-MarketplaceKey', $MARKETPLACE_KEY,
            '-DurableHome', $Case.durable,
            '-LegacyRoot', $Case.legacy,
            '-LegacyProbeFile', $Case.probe
        )
        $output = @(& powershell.exe @arguments)
        $code = $LASTEXITCODE
    }
    finally {
        $env:USERPROFILE = $previousProfile
        $env:COPILOT_EXTENSIONS_CONTEXT = $previousContext
        $env:COPILOT_PLUGIN_ROOT = $previousPayload
    }
    $after = Get-TreeFingerprint $Case.root
    if ($code -ne 0) {
        return [pscustomobject]@{
            result = $null
            unchanged = ($before -eq $after)
            error = "Evaluator exited ${code}: $($output -join "`n")"
        }
    }
    try {
        $result = ($output -join "`n") | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            result = $null
            unchanged = ($before -eq $after)
            error = "Evaluator emitted invalid JSON: $($_.Exception.Message)"
        }
    }
    [pscustomobject]@{
        result = $result
        unchanged = ($before -eq $after)
        error = $null
    }
}

function Assert-Case(
    $Actual,
    [string]$Status,
    [string]$Desired,
    $ActualMode,
    [string]$Label
) {
    if ($Actual.error) {
        fail "${Label}: $($Actual.error)"
        if ($Actual.unchanged) {
            pass "${Label}: failed evaluator made no filesystem writes"
        }
        else {
            fail "${Label}: failed evaluator changed fixture state"
        }
        return
    }
    $actualMatches = if ($null -eq $ActualMode) {
        $null -eq $Actual.result.actualMode
    }
    else {
        $Actual.result.actualMode -eq $ActualMode
    }
    if (
        $Actual.result.status -eq $Status -and
        $Actual.result.desiredMode -eq $Desired -and
        $actualMatches
    ) {
        pass "${Label}: status=$Status desired=$Desired actual=$ActualMode"
    }
    else {
        fail "${Label}: got status=$($Actual.result.status) desired=$($Actual.result.desiredMode) actual=$($Actual.result.actualMode)"
    }
    if ($Actual.unchanged) {
        pass "${Label}: evaluator made no filesystem writes"
    }
    else {
        fail "${Label}: evaluator changed fixture state"
    }
}

function Write-Context($Case) {
    $cell = Join-Path (Join-Path $Case.durable 'marketplaces') $MARKETPLACE
    $pluginRoot = Join-Path (Join-Path $cell 'plugins') $PLUGIN
    $namespace = Join-Path $cell 'namespace.json'
    $install = Join-Path $pluginRoot 'install.json'
    Write-JsonNoBom $namespace ([ordered]@{
        schema = 'copilot-extensions.marketplace-namespace'
        version = 1
        marketplaceId = $MARKETPLACE
        source = [ordered]@{
            kind = 'github'
            canonical = 'github:example-org/example-marketplace'
            ref = ''
            fingerprint = 'sha256:9caa0da95f327099983348a3d2353fa93e57ef6428b69f354cc3bdc76ada0894'
        }
        locators = @()
        generation = 1
        state = 'active'
        createdAt = '2026-01-01T00:00:00Z'
        updatedAt = '2026-01-01T00:00:00Z'
    })
    Write-JsonNoBom $install ([ordered]@{
        schema = 'copilot-extensions.plugin-installation'
        version = 1
        marketplaceId = $MARKETPLACE
        pluginId = $PLUGIN
        pluginRoot = [IO.Path]::GetFullPath($pluginRoot)
        namespaceReceipt = [IO.Path]::GetFullPath($namespace)
        payload = [ordered]@{
            root = [IO.Path]::GetFullPath($Case.payload)
            version = '1.0.0'
            origin = 'explicit'
        }
        roots = [ordered]@{
            versions = 'versions'
            snapshots = 'snapshots'
            state = 'state'
            run = 'run'
            logs = 'logs'
            cache = 'cache'
            launchers = 'launchers'
        }
        generation = 2
        state = 'active'
        createdAt = '2026-01-01T00:00:00Z'
        updatedAt = '2026-01-01T00:00:00Z'
    })
    $Case | Add-Member -NotePropertyName context -NotePropertyValue $install -Force
    [pscustomobject]@{
        pluginRoot = $pluginRoot
        install = $install
    }
}

function Write-Activation($Case, $Context) {
    $activation = Join-Path $Context.pluginRoot 'installation-activation.json'
    Write-JsonNoBom $activation ([ordered]@{
        schema = 'copilot-extensions.installation-activation'
        version = 1
        marketplaceId = $MARKETPLACE
        pluginId = $PLUGIN
        mode = 'namespaced'
        state = 'active'
        environment = [ordered]@{
            platform = 'windows'
            homeRealPath = [IO.Path]::GetFullPath($Case.profile)
            wslDistro = $null
        }
        context = [IO.Path]::GetFullPath($Context.install)
        namespaceGeneration = 1
        installGeneration = 2
        generation = 3
        legacy = [ordered]@{
            disposition = 'absent'
            probe = [ordered]@{
                declared = $true
                result = 'absent'
                checkedAt = '2026-01-01T00:00:00Z'
            }
        }
        createdAt = '2026-01-01T00:00:00Z'
        updatedAt = '2026-01-01T00:00:00Z'
    })
    $activation
}

cr_init

phase 0 'source and evaluator present'
envdump
if (-not $ROOT -or -not (Test-Path -LiteralPath $ROOT -PathType Container)) {
    jam 'repo-config' 'CR_PARTNER_PATH does not name the mounted source tree' 'pass -PartnerPath to the Windows clean-room runner'
    cr_finalize
}
if (-not (Test-Path -LiteralPath $EVALUATOR -PathType Leaf)) {
    jam 'drop-structural' 'canonical PowerShell evaluator is absent from the mounted source' 'mount the copilot-extensions worktree'
    cr_finalize
}
pass "mounted evaluator present: $EVALUATOR"

phase 1 'absent policy selects legacy without writes'
$case = Reset-Case 'absent'
$evaluation = Invoke-Mode $case $PROBE_UNKNOWN
Assert-Case $evaluation 'ready' 'legacy' 'legacy' 'absent policy'

phase 2 'proven plugin policy requests activation while legacy remains actual'
$case = Reset-Case 'precedence'
$context = Write-Context $case
Write-JsonNoBom $case.policy ([ordered]@{
    schema = 'copilot-extensions.installation-mode'
    version = 1
    installationMode = [ordered]@{
        enabled = $false
        marketplaces = [ordered]@{
            $MARKETPLACE = [ordered]@{
                enabled = $false
                plugins = [ordered]@{
                    $PLUGIN = [ordered]@{ enabled = $true }
                }
            }
        }
    }
})
$evaluation = Invoke-Mode $case $PROBE_ABSENT
Assert-Case $evaluation 'ready' 'namespaced' 'legacy' 'plugin override'
if (
    $null -ne $evaluation.result -and
    $evaluation.result.reason -eq 'activation-required' -and
    $evaluation.result.policy.authoritative -and
    $evaluation.result.policy.scope -eq 'plugin' -and
    $evaluation.result.runtimeRoot -eq [IO.Path]::GetFullPath($case.legacy)
) {
    pass 'authoritative plugin policy requests activation without selecting the cell root'
}
else {
    fail 'plugin precedence, activation reason, or legacy runtime pin was lost'
}

phase 3 'legacy footprint requires migration'
$case = Reset-Case 'migration'
$null = Write-Context $case
Write-JsonNoBom $case.policy ([ordered]@{
    schema = 'copilot-extensions.installation-mode'
    version = 1
    installationMode = [ordered]@{ enabled = $true }
})
$evaluation = Invoke-Mode $case $PROBE_PRESENT
Assert-Case $evaluation 'migration-required' 'namespaced' 'legacy' 'legacy migration'

phase 4 'active namespaced mode stays pinned when policy is removed'
$case = Reset-Case 'sticky'
$context = Write-Context $case
$activation = Write-Activation $case $context
$evaluation = Invoke-Mode $case $PROBE_ABSENT
Assert-Case $evaluation 'deactivation-required' 'legacy' 'namespaced' 'sticky activation'
if (
    $null -ne $evaluation.result -and
    $evaluation.result.activation -eq [IO.Path]::GetFullPath($activation) -and
    $evaluation.result.runtimeRoot -eq [IO.Path]::GetFullPath($context.pluginRoot)
) {
    pass 'activation path and namespaced runtime root remain pinned'
}
else {
    fail 'activation diagnostics or namespaced runtime pin was lost'
}

phase 5 'orphaned transfer fails closed'
$case = Reset-Case 'orphaned'
$missingActivation = Join-Path $case.durable "marketplaces\$MARKETPLACE\plugins\$PLUGIN\installation-activation.json"
Write-JsonNoBom (Join-Path $case.legacy '.installation-ownership.json') ([ordered]@{
    schema = 'copilot-extensions.legacy-installation-ownership'
    version = 1
    marketplaceId = $MARKETPLACE
    pluginId = $PLUGIN
    activation = [ordered]@{
        path = [IO.Path]::GetFullPath($missingActivation)
        generation = 1
    }
    environment = [ordered]@{
        platform = 'windows'
        homeRealPath = [IO.Path]::GetFullPath($case.profile)
        wslDistro = $null
    }
    transferredAt = '2026-01-01T00:00:00Z'
})
$evaluation = Invoke-Mode $case $PROBE_UNKNOWN
Assert-Case $evaluation 'orphaned-transfer' 'legacy' 'legacy' 'orphaned transfer'

phase 6 'stale maintenance marker blocks without auto-clear'
$case = Reset-Case 'maintenance'
$marker = Join-Path $case.profile '.copilot-extensions\maintenance'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $marker) | Out-Null
New-Item -ItemType File -Force -Path $marker | Out-Null
$evaluation = Invoke-Mode $case $PROBE_UNKNOWN
Assert-Case $evaluation 'maintenance-blocked' 'legacy' 'legacy' 'stale maintenance'
if (
    $null -ne $evaluation.result -and
    $evaluation.result.maintenance.state -eq 'stale' -and
    (Test-Path -LiteralPath $marker)
) {
    pass 'missing sidecar is stale and marker was not auto-cleared'
}
else {
    fail 'stale maintenance diagnostics or marker preservation failed'
}

cr_finalize
