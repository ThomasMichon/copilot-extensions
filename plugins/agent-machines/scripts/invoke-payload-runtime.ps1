$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$forwardArgs = @($args)

$payloadRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_MACHINES_PAYLOAD_ROOT',
    'Process'
)
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-machines] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$legacyRoot = Join-Path $env:USERPROFILE '.agent-machines' # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-machines] installation-context runtime resolver is unavailable.'
    )
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$marketplaceId = ''
$resolutionStatus = 'ready'
$resolutionReason = 'policy-default-false'
$actualMode = 'legacy'
$desiredMode = 'legacy'
$policy = Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'
$policyPresent = (
    (Test-Path -LiteralPath $policy) -or
    $null -ne (Get-Item -LiteralPath $policy -Force -ErrorAction SilentlyContinue)
)
$provenanceBoundary = (
    ($payloadRoot -replace '\\', '/') -match
        '/\.copilot/installed-plugins/[^/]+/[^/]+/?$'
)
if (-not $provenanceBoundary) {
    $probeRoot = $payloadRoot
    while ($probeRoot) {
        if (
            Test-Path -LiteralPath (
                Join-Path $probeRoot '.github\plugin\marketplace.json'
            ) -PathType Leaf
        ) {
            $provenanceBoundary = $true
            break
        }
        $parent = Split-Path -Parent $probeRoot
        if (-not $parent -or $parent -eq $probeRoot) { break }
        $probeRoot = $parent
    }
}
$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-machines] PowerShell host executable is unavailable.')
    exit 126
}

    $statusArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'status',
        '-PayloadRoot', $payloadRoot,
        '-PluginId', 'agent-machines',
        '-LegacyRoot', $legacyRoot
    )
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $statusArgs += @('-Context', $env:COPILOT_EXTENSIONS_CONTEXT)
        $contextDurableHome = $env:COPILOT_EXTENSIONS_CONTEXT
        1..5 | ForEach-Object {
            $contextDurableHome = Split-Path -Parent $contextDurableHome
        }
        $statusArgs += @('-DurableHome', $contextDurableHome)
    }
    $resolutionJson = @(& $hostExe @statusArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-machines] installation context could not be resolved.'
        )
        exit 126
    }
    try {
        $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-machines] installation context returned malformed status.'
        )
        exit 126
    }
    $resolutionStatus = [string]$resolution.status
    $resolutionReason = [string]$resolution.reason
    $actualMode = [string]$resolution.actualMode
    $desiredMode = [string]$resolution.desiredMode
    $simplePolicyLegacy = $false
    if (
        -not $env:COPILOT_EXTENSIONS_CONTEXT -and
        -not $policyPresent -and
        -not $provenanceBoundary -and
        $resolutionStatus -ceq 'provenance-blocked'
    ) {
        $simplePolicyLegacy = $true
    }
    elseif (
        -not $env:COPILOT_EXTENSIONS_CONTEXT -and
        $resolutionStatus -ceq 'provenance-blocked' -and
        [string]$resolution.policy.state -ceq 'valid' -and
        $resolution.policy.enabled -is [bool] -and
        -not $resolution.policy.enabled
    ) {
        try {
            $policyDocument = Get-Content -LiteralPath $policy -Raw |
                ConvertFrom-Json
            $installationMode = @(
                $policyDocument.PSObject.Properties |
                    Where-Object { $_.Name -ceq 'installationMode' }
            )
            $marketplaces = @()
            if ($installationMode.Count -eq 1) {
                $marketplaces = @(
                    $installationMode[0].Value.PSObject.Properties |
                        Where-Object { $_.Name -ceq 'marketplaces' }
                )
            }
            $simplePolicyLegacy = (
                $marketplaces.Count -eq 0 -or
                $marketplaces[0].Value.PSObject.Properties.Count -eq 0
            )
        } catch {
            $simplePolicyLegacy = $false
        }
    }
    if (
        (
            $resolutionStatus -ceq 'ready' -and
            $actualMode -ceq 'legacy' -and
            $desiredMode -ceq 'legacy'
        ) -or
        $simplePolicyLegacy
    ) {
        if ($env:COPILOT_EXTENSIONS_CONTEXT) {
            [Console]::Error.WriteLine(
                '[agent-machines] requested installation context is not active.'
            )
            exit 126
        }
        $runtimeRoot = $legacyRoot
    }
    elseif (
        (
            $resolutionStatus -ceq 'ready' -and
            $resolutionReason -ceq 'namespaced-active'
        ) -or
        $resolutionStatus -ceq 'deactivation-required'
    ) {
        if ($actualMode -cne 'namespaced') {
            [Console]::Error.WriteLine(
                "[agent-machines] installation context blocks invocation: " +
                "status=$resolutionStatus reason=$resolutionReason."
            )
            exit 126
        }
        $runtimeRoot = [string]$resolution.runtimeRoot
        $context = [string]$resolution.context
        $marketplaceId = [string]$resolution.marketplaceId
        if (-not $runtimeRoot -or -not $context -or -not $marketplaceId) {
            [Console]::Error.WriteLine(
                '[agent-machines] active installation context is incomplete.'
            )
            exit 126
        }
    }
    else {
        [Console]::Error.WriteLine(
            "[agent-machines] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }

function Resolve-AgentMachinesRuntime {
    $AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver
    return $AgentRtPy
}

function Invoke-AgentMachinesRuntime([string]$Python) {
    if ($context) { $env:COPILOT_EXTENSIONS_CONTEXT = $context }
    & $Python -m agent_machines @forwardArgs
    exit $LASTEXITCODE
}

$python = Resolve-AgentMachinesRuntime
if ($python) { Invoke-AgentMachinesRuntime $python }
if ($env:AGENT_MACHINES_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-machines] runtime not provisioned ' +
        '(AGENT_MACHINES_NO_SELFPROVISION set).'
    )
    exit 1
}

$installer = Join-Path $scriptDir 'init.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "[agent-machines] payload installer not found: $installer"
    )
    exit 127
}
[Console]::Error.WriteLine(
    '[agent-machines] runtime not provisioned -- provisioning from the owning payload.'
)
[Console]::Error.WriteLine(
    '::agent-provisioning:: plugin=agent-machines eta_seconds=120 reason=first-use'
)
if ($actualMode -ceq 'namespaced') {
    if (
        $resolutionStatus -cne 'ready' -or
        $resolutionReason -cne 'namespaced-active'
    ) {
        [Console]::Error.WriteLine(
            '[agent-machines] deactivation-pending installation cannot ' +
            'provision a new runtime.'
        )
        exit 126
    }
    & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer `
        -Action cell-provision `
        -Context $context `
        -ExpectedMarketplaceId $marketplaceId 2>&1 |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    $provisionStatus = $LASTEXITCODE
    if ($provisionStatus -ne 0) { exit $provisionStatus }
    $python = Resolve-AgentMachinesRuntime
    if ($python) { Invoke-AgentMachinesRuntime $python }
    [Console]::Error.WriteLine(
        '[agent-machines] provisioning completed without a resolvable runtime.'
    )
    exit 1
}

if (-not (Test-Path -LiteralPath $runtimeRoot)) {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
}
$lockPath = Join-Path $runtimeRoot '.payload-provision.lock'
$lock = $null
while (-not $lock) {
    try {
        $lock = [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        Start-Sleep -Milliseconds 200
    }
}

try {
    $python = Resolve-AgentMachinesRuntime
    if ($python) { Invoke-AgentMachinesRuntime $python }

    & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer `
        -Action stamp 2>&1 |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $snapshot = ''
    try {
        $snapshot = ([IO.File]::ReadAllText(
            (Join-Path $legacyRoot 'payload-dir')
        )).Trim()
    } catch {}
    $snapshotInstaller = if ($snapshot) {
        Join-Path $snapshot 'scripts\init.ps1'
    } else {
        ''
    }
    if (
        -not $snapshotInstaller -or
        -not (Test-Path -LiteralPath $snapshotInstaller -PathType Leaf)
    ) {
        [Console]::Error.WriteLine(
            "[agent-machines] stamped snapshot installer not found: " +
            $snapshotInstaller
        )
        exit 127
    }
    & $hostExe -NoProfile -ExecutionPolicy Bypass -File $snapshotInstaller `
        -Action provision 2>&1 |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    $provisionStatus = $LASTEXITCODE
    if ($provisionStatus -ne 0) { exit $provisionStatus }

    $python = Resolve-AgentMachinesRuntime
    if ($python) { Invoke-AgentMachinesRuntime $python }
} finally {
    if ($lock) { $lock.Dispose() }
}
[Console]::Error.WriteLine(
    '[agent-machines] provisioning completed without a resolvable runtime.'
)
exit 1
