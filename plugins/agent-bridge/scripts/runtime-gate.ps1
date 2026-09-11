$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = @($args)
$converge = $forwardArgs.Count -gt 0 -and $forwardArgs[0] -ceq 'provision'
if ($converge -and (
    $forwardArgs.Count -ne 3 -or $forwardArgs[1] -cne '--current-payload' -or
    $forwardArgs[2] -cne '--json'
)) {
    [Console]::Error.WriteLine('[agent-bridge] usage: provision --current-payload --json')
    exit 2
}
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_BRIDGE_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-bridge] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = if ($env:AGENT_BRIDGE_INSTALL_DIR) {
    $env:AGENT_BRIDGE_INSTALL_DIR
} elseif ($env:AGENT_BRIDGE_CONFIG_DIR) {
    $env:AGENT_BRIDGE_CONFIG_DIR
} else {
    Join-Path $env:USERPROFILE '.agent-bridge'
} # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-bridge] installation-context runtime support is unavailable.'
    )
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-bridge] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$installationId = ''
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'status',
    '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-bridge',
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
        '[agent-bridge] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-bridge] installation context returned malformed status.'
    )
    exit 126
}

$resolutionStatus = [string]$resolution.status
$resolutionReason = [string]$resolution.reason
$actualMode = [string]$resolution.actualMode
$desiredMode = [string]$resolution.desiredMode
$installGeneration = [string]$resolution.installGeneration

if (
    $resolutionStatus -ceq 'ready' -and
    $actualMode -ceq 'legacy' -and
    $desiredMode -ceq 'legacy'
) {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        [Console]::Error.WriteLine(
            '[agent-bridge] requested installation context is not active.'
        )
        exit 126
    }
}
elseif (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled -and
    $null -eq $resolution.legacy.tombstone -and
    [string]$resolution.legacy.disposition -ceq 'active'
) {
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
            "[agent-bridge] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    $marketplaceId = [string]$resolution.marketplaceId
    if (-not $runtimeRoot -or -not $context) {
        [Console]::Error.WriteLine(
            '[agent-bridge] active installation context is incomplete.'
        )
        exit 126
    }
    $installationId = if ($marketplaceId) { "$marketplaceId/agent-bridge" } else { '' }
    $validationDurableHome = $context
    1..5 | ForEach-Object {
        $validationDurableHome = Split-Path -Parent $validationDurableHome
    }
    $validationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'validate',
        '-Context', $context,
        '-DurableHome', $validationDurableHome,
        '-ExpectedPluginId', 'agent-bridge',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-bridge] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-bridge] installation context validation was malformed.'
        )
        exit 126
    }
    if ([string]$validatedContext.generation -cne $installGeneration) {
        [Console]::Error.WriteLine(
            '[agent-bridge] installation context generation does not match governance.'
        )
        exit 126
    }
}
else {
    [Console]::Error.WriteLine(
        "[agent-bridge] installation context blocks invocation: " +
        "status=$resolutionStatus reason=$resolutionReason."
    )
    exit 126
}

function Resolve-RuntimePython {
    $script:AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver
    return $AgentRtPy
}

function Set-RuntimeEnvironment {
    $env:AGENT_BRIDGE_INSTALL_DIR = $runtimeRoot
    $env:AGENT_BRIDGE_CONFIG_DIR = $runtimeRoot
    $env:AGENT_BRIDGE_CONNECT_LOG = Join-Path (Join-Path $runtimeRoot 'logs') 'connect.log'
    if ($installationId) {
        $env:AGENT_BRIDGE_INSTALLATION_ID = $installationId
    } else {
        Remove-Item Env:AGENT_BRIDGE_INSTALLATION_ID -ErrorAction SilentlyContinue
    }
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
}

function Invoke-Runtime {
    param([Parameter(Mandatory)][string]$Python)
    Set-RuntimeEnvironment
    & $Python -m agent_bridge @forwardArgs
    exit $LASTEXITCODE
}

function Assert-ConvergenceAdmission {
    $probeArgs = @($statusArgs)
    if (-not $context) {
        $probeArgs[5] = 'probe-legacy'
    }
    $decisionJson = @(& $hostExe @probeArgs)
    if ($LASTEXITCODE -ne 0) {
        throw '[agent-bridge] current-payload convergence authorization failed.'
    }
    $decision = ($decisionJson -join "`n") | ConvertFrom-Json
    if ($context) {
        if (
            $decision.status -cne 'ready' -or
            $decision.reason -cne 'namespaced-active' -or
            $decision.desiredMode -cne 'namespaced' -or
            $decision.runtimeRoot -cne $runtimeRoot -or
            $decision.context -cne $context -or
            [string]$decision.installGeneration -cne $installGeneration
        ) {
            throw '[agent-bridge] current-payload convergence requires an active, unchanged namespace.'
        }
    } elseif (
        $decision.allowMutation -isnot [bool] -or -not $decision.allowMutation -or
        $decision.probeReason -cne 'legacy-active'
    ) {
        throw '[agent-bridge] current-payload convergence lacks positive legacy authorization.'
    }
}

function Test-CurrentPayload {
    param([string]$Python, [string]$Action = 'unchanged')
    if (-not $Python) { return $false }
    $mode = if ($context) { 'namespaced' } else { 'legacy' }
    $hash = ''
    if (-not $context) {
        . (Join-Path $scriptDir 'payload-hash.ps1')
        $hash = Get-BridgePayloadHash -Payload $payloadRoot
    }
    $verifyArgs = @(
        '-I', '-B', (Join-Path $scriptDir 'current-payload.py'),
        '--payload', $payloadRoot, '--root', $runtimeRoot, '--mode', $mode,
        "--payload-hash=$hash", "--context=$context",
        "--generation=$installGeneration", "--marketplace=$marketplaceId",
        '--action', $Action
    )
    $receipt = @('' | & $Python @verifyArgs)
    if ($LASTEXITCODE -ne 0) { return $false }
    [Console]::Out.WriteLine(($receipt -join "`n"))
    return $true
}

$runtimePython = Resolve-RuntimePython
if ($converge) {
    Assert-ConvergenceAdmission
    if (Test-CurrentPayload -Python $runtimePython) { exit 0 }
    if ($context) {
        [Console]::Error.WriteLine(
            '[agent-bridge] namespaced runtime is stale, incomplete, or unsupported; no namespaced bridge update transaction is available. No legacy fallback was attempted.'
        )
        exit 126
    }
} elseif ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}

if ($env:AGENT_BRIDGE_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-bridge] runtime not provisioned (AGENT_BRIDGE_NO_SELFPROVISION set).'
    )
    exit 1
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$statusPath = Join-Path $runtimeRoot '.provision-status'
$_lockPath = Join-Path $runtimeRoot '.provision.lock'
$_lock = $null
while (-not $_lock) {
    try {
        $_lock = [IO.File]::Open(
            $_lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        Start-Sleep -Milliseconds 200
    }
}

$_provisionedPy = $null
$_provisionRc = 0
$_convergenceAction = 'unchanged'
try {
    $_provisionedPy = Resolve-RuntimePython
    $_installAction = 'provision'
    if ($converge) {
        Assert-ConvergenceAdmission
        if (Test-CurrentPayload -Python $_provisionedPy) { exit 0 }
        $_convergenceAction = 'provisioned'
        if ($_provisionedPy) {
            $_installAction = 'update'
            $_convergenceAction = 'updated'
        }
    }
    if ($converge -or -not $_provisionedPy) {
        if ($converge) {
            [Console]::Error.WriteLine(
                "[agent-bridge] converging selected legacy runtime through owner $_installAction."
            )
        } else {
            [Console]::Error.WriteLine(
                '[agent-bridge] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.'
            )
        }
        [Console]::Error.WriteLine(
            "::agent-provisioning:: plugin=agent-bridge eta_seconds=120 reason=first-use status=$statusPath"
        )
        "provisioning $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" |
            Set-Content -LiteralPath $statusPath -Encoding utf8
        Set-RuntimeEnvironment
        '' | & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer $_installAction -InstallDir $runtimeRoot 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $_provisionRc = $LASTEXITCODE
        if ($_provisionRc -eq 0) {
            $_provisionedPy = Resolve-RuntimePython
            if ($converge) {
                Assert-ConvergenceAdmission
                if (-not (Test-CurrentPayload -Python $_provisionedPy -Action $_convergenceAction)) {
                    $_provisionRc = 1
                    [Console]::Error.WriteLine(
                        '[agent-bridge] installer returned success without current-payload readiness.'
                    )
                }
            }
        }
    }
} finally {
    if ($_lock) { $_lock.Dispose() }
}
if ($_provisionRc -ne 0) {
    "failed rc=$_provisionRc $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" |
        Set-Content -LiteralPath $statusPath -Encoding utf8
    [Console]::Error.WriteLine(
        "[agent-bridge] provisioning FAILED. See the log above; retry, or run: `"$installer`" provision -InstallDir `"$runtimeRoot`""
    )
    exit $_provisionRc
}
if ($_provisionedPy) {
    "ready $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" |
        Set-Content -LiteralPath $statusPath -Encoding utf8
    if ($converge) { exit 0 }
    Invoke-Runtime -Python $_provisionedPy
}
"failed rc=1 $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" |
    Set-Content -LiteralPath $statusPath -Encoding utf8
[Console]::Error.WriteLine(
    '[agent-bridge] provisioning reported success but no runtime slot resolved.'
)
exit 1
