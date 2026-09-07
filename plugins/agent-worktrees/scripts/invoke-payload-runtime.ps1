$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$forwardArgs = @($args)

$payloadRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_WORKTREES_PAYLOAD_ROOT',
    'Process'
)
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-worktrees] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = Join-Path $env:USERPROFILE '.agent-worktrees' # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-worktrees] installation-context runtime support is unavailable.'
    )
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-worktrees] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$resolutionStatus = 'ready'
$resolutionReason = 'policy-default-false'
$actualMode = 'legacy'
$desiredMode = 'legacy'
$policy = Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'
$policyPresent = (
    (Test-Path -LiteralPath $policy) -or
    $null -ne (Get-Item -LiteralPath $policy -Force -ErrorAction SilentlyContinue)
)
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'status',
    '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-worktrees',
    '-LegacyRoot', $legacyRoot
)
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $statusArgs += @('-Context', $env:COPILOT_EXTENSIONS_CONTEXT)
    $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
    1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
    $statusArgs += @('-DurableHome', $durableHome)
}
$resolutionJson = @(& $hostExe @statusArgs)
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine(
        '[agent-worktrees] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-worktrees] installation context returned malformed status.'
    )
    exit 126
}
$resolutionStatus = [string]$resolution.status
$resolutionReason = [string]$resolution.reason
$actualMode = [string]$resolution.actualMode
$desiredMode = [string]$resolution.desiredMode
$activationGeneration = [string]$resolution.activationGeneration
$namespaceGeneration = [string]$resolution.namespaceGeneration
$installGeneration = [string]$resolution.installGeneration
$simplePolicyLegacy = $false
if (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    -not $policyPresent -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    [string]$resolution.policy.state -ceq 'missing' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled -and
    [string]$resolution.policy.reason -ceq 'policy-default-false' -and
    $null -eq $resolution.legacy.tombstone -and
    [string]$resolution.legacy.disposition -ceq 'active'
) {
    $simplePolicyLegacy = $true
}
elseif (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    [string]$resolution.policy.state -ceq 'valid' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled -and
    $null -eq $resolution.legacy.tombstone -and
    [string]$resolution.legacy.disposition -ceq 'active'
) {
    try {
        $policyDocument = Get-Content -LiteralPath $policy -Raw | ConvertFrom-Json
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
            '[agent-worktrees] requested installation context is not active.'
        )
        exit 126
    }
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
            "[agent-worktrees] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    if (-not $runtimeRoot -or -not $context) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] active installation context is incomplete.'
        )
        exit 126
    }
}
else {
    [Console]::Error.WriteLine(
        "[agent-worktrees] installation context blocks invocation: " +
        "status=$resolutionStatus reason=$resolutionReason."
    )
    exit 126
}

$validatedNamespaceGeneration = ''
if ($actualMode -ceq 'namespaced') {
    $validationDurableHome = $context
    1..5 | ForEach-Object {
        $validationDurableHome = Split-Path -Parent $validationDurableHome
    }
    $validationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'validate',
        '-Context', $context,
        '-DurableHome', $validationDurableHome,
        '-ExpectedPluginId', 'agent-worktrees',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation context validation was malformed.'
        )
        exit 126
    }
    $validatedNamespaceGeneration = [string]$validatedContext.namespaceGeneration
    if ([string]$validatedContext.generation -cne $installGeneration) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation context generation does not match governance.'
        )
        exit 126
    }
}

function Test-InstallationResolutionCurrent {
    $currentJson = @(& $hostExe @statusArgs)
    if ($LASTEXITCODE -ne 0) { return $false }
    try {
        $current = ($currentJson -join "`n") | ConvertFrom-Json
    } catch {
        return $false
    }
    if (
        [string]$current.status -cne $resolutionStatus -or
        [string]$current.reason -cne $resolutionReason -or
        [string]$current.actualMode -cne $actualMode -or
        [string]$current.desiredMode -cne $desiredMode -or
        [string]$current.activationGeneration -cne $activationGeneration -or
        [string]$current.namespaceGeneration -cne $namespaceGeneration -or
        [string]$current.installGeneration -cne $installGeneration
    ) {
        return $false
    }
    if ($actualMode -ceq 'namespaced') {
        if (
            [string]$current.runtimeRoot -cne $runtimeRoot -or
            [string]$current.context -cne $context
        ) {
            return $false
        }
        $currentValidationJson = @(& $hostExe @validationArgs)
        if ($LASTEXITCODE -ne 0) { return $false }
        try {
            $currentValidated = (
                $currentValidationJson -join "`n"
            ) | ConvertFrom-Json
        } catch {
            return $false
        }
        return (
            [string]$currentValidated.namespaceGeneration -ceq
                $validatedNamespaceGeneration -and
            [string]$currentValidated.generation -ceq $installGeneration
        )
    }
    return $true
}

function Resolve-AgentWorktreesRuntime {
    $AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver
    return $AgentRtPy
}

function Invoke-AgentWorktreesRuntime([string]$Python) {
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
    & $Python -m agent_worktrees @forwardArgs
    exit $LASTEXITCODE
}

$python = Resolve-AgentWorktreesRuntime
if ($python) { Invoke-AgentWorktreesRuntime $python }
if ($env:AGENT_WORKTREES_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-worktrees] runtime not provisioned ' +
        '(AGENT_WORKTREES_NO_SELFPROVISION set).'
    )
    exit 1
}

[Console]::Error.WriteLine(
    '[agent-worktrees] runtime not provisioned -- provisioning from the owning payload.'
)
[Console]::Error.WriteLine(
    '::agent-provisioning:: plugin=agent-worktrees eta_seconds=120 reason=first-use'
)
if (-not (Test-Path -LiteralPath $runtimeRoot)) {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
}
$lockPath = Join-Path $runtimeRoot '.provision.lock'
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
    if (-not (Test-InstallationResolutionCurrent)) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation governance changed while waiting; retry.'
        )
        exit 126
    }
    $python = Resolve-AgentWorktreesRuntime
    if ($python) { Invoke-AgentWorktreesRuntime $python }

    if ($actualMode -ceq 'namespaced') {
        if (
            $resolutionStatus -cne 'ready' -or
            $resolutionReason -cne 'namespaced-active'
        ) {
            [Console]::Error.WriteLine(
                '[agent-worktrees] deactivation-pending installation cannot ' +
                'provision a new runtime.'
            )
            exit 126
        }
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer `
            install -InstallDir $runtimeRoot 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $provisionStatus = $LASTEXITCODE
        if ($provisionStatus -ne 0) { exit $provisionStatus }
    } else {
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer stamp 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $snapshot = ''
        try {
            $snapshot = ([IO.File]::ReadAllText(
                (Join-Path $legacyRoot 'payload-dir')
            )).Trim()
        } catch {}
        $snapshotInstaller = if ($snapshot) {
            Join-Path $snapshot 'scripts\install.ps1'
        } else {
            ''
        }
        if (
            -not $snapshotInstaller -or
            -not (Test-Path -LiteralPath $snapshotInstaller -PathType Leaf)
        ) {
            [Console]::Error.WriteLine(
                "[agent-worktrees] stamped snapshot installer not found: " +
                $snapshotInstaller
            )
            exit 127
        }
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $snapshotInstaller `
            provision 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $provisionStatus = $LASTEXITCODE
        if ($provisionStatus -ne 0) { exit $provisionStatus }
    }

    if (-not (Test-InstallationResolutionCurrent)) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation governance changed during provisioning.'
        )
        exit 126
    }
    $python = Resolve-AgentWorktreesRuntime
    if ($python) { Invoke-AgentWorktreesRuntime $python }
} finally {
    if ($lock) { $lock.Dispose() }
}
[Console]::Error.WriteLine(
    '[agent-worktrees] provisioning completed without a resolvable runtime.'
)
exit 1
