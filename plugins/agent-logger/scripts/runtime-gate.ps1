$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = @($args)
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_LOGGER_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-logger] owning payload root is unavailable.')
    exit 126
}
$commandName = if ($env:COPILOT_EXTENSIONS_PAYLOAD_COMMAND) {
    [string]$env:COPILOT_EXTENSIONS_PAYLOAD_COMMAND
} else {
    'agent-logger'
}
$moduleName = if ($env:COPILOT_EXTENSIONS_PAYLOAD_MODULE) {
    [string]$env:COPILOT_EXTENSIONS_PAYLOAD_MODULE
} else {
    'agent_logger'
}
$allowedTargets = @{
    'agent-logger' = 'agent_logger'
    'collate-session' = 'agent_logger.segmenter.collate'
    'read-session-digest' = 'agent_logger.segmenter.read_digest'
    'prepare-session-log' = 'agent_logger.segmenter.prepare_log'
    'ramp-up-session' = 'agent_logger.segmenter.ramp_up'
    'session-sync' = 'agent_logger.sync.engine'
}
if (-not ($allowedTargets.ContainsKey($commandName) -and $allowedTargets[$commandName] -ceq $moduleName)) {
    [Console]::Error.WriteLine(
        "[agent-logger] unsupported payload dispatch target: $commandName -> $moduleName"
    )
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = if ($env:AGENT_LOGGER_HOME) {
    $env:AGENT_LOGGER_HOME
} else {
    Join-Path $env:USERPROFILE '.agent-logger'
} # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-logger] installation-context runtime support is unavailable.'
    )
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-logger] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$installationId = ''
$serviceSuffix = ''
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'status',
    '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-logger',
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
        '[agent-logger] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-logger] installation context returned malformed status.'
    )
    exit 126
}

$resolutionStatus = [string]$resolution.status
$resolutionReason = [string]$resolution.reason
$actualMode = [string]$resolution.actualMode
$desiredMode = [string]$resolution.desiredMode
$installGeneration = [string]$resolution.installGeneration
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
    $marketplaces = $resolution.installationMode.PSObject.Properties['marketplaces']
    $simplePolicyLegacy = (
        $null -eq $marketplaces -or
        $marketplaces.Value.PSObject.Properties.Count -eq 0
    )
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
            '[agent-logger] requested installation context is not active.'
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
            "[agent-logger] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $context = [string]$resolution.context
    $marketplaceId = [string]$resolution.marketplaceId
    if (-not $context -or -not $marketplaceId) {
        [Console]::Error.WriteLine(
            '[agent-logger] active installation context is incomplete.'
        )
        exit 126
    }
    $validationDurableHome = $context
    1..5 | ForEach-Object {
        $validationDurableHome = Split-Path -Parent $validationDurableHome
    }
    $validationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'validate',
        '-Context', $context,
        '-DurableHome', $validationDurableHome,
        '-ExpectedPluginId', 'agent-logger',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-logger] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-logger] installation context validation was malformed.'
        )
        exit 126
    }
    if (
        $installGeneration -and
        [string]$validatedContext.generation -cne $installGeneration
    ) {
        [Console]::Error.WriteLine(
            '[agent-logger] installation context generation does not match governance.'
        )
        exit 126
    }
    $runtimeRoot = [string]$validatedContext.pluginRoot
    if (-not $runtimeRoot) {
        [Console]::Error.WriteLine(
            '[agent-logger] active installation context is missing the plugin root.'
        )
        exit 126
    }
    $normalizedRoot = [IO.Path]::GetFullPath($runtimeRoot).ToLowerInvariant()
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $serviceSuffix = (
            [BitConverter]::ToString(
                $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalizedRoot))
            )
        ).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
    $installationId = "$marketplaceId/agent-logger"
}
else {
    [Console]::Error.WriteLine(
        "[agent-logger] installation context blocks invocation: " +
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
    $env:AGENT_LOGGER_HOME = $runtimeRoot
    if ($installationId) {
        $env:AGENT_LOGGER_INSTALLATION_ID = $installationId
        $env:AGENT_LOGGER_TIMER_NAME = "agent-logger-sync-$serviceSuffix"
        $env:AGENT_LOGGER_TASK_NAME = "Agent Logger Session Sync - $serviceSuffix"
    } else {
        Remove-Item Env:AGENT_LOGGER_INSTALLATION_ID -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_LOGGER_TIMER_NAME -ErrorAction SilentlyContinue
        Remove-Item Env:AGENT_LOGGER_TASK_NAME -ErrorAction SilentlyContinue
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
    & $Python -m $moduleName @forwardArgs
    exit $LASTEXITCODE
}

$runtimePython = Resolve-RuntimePython
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}

if ($env:AGENT_LOGGER_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        "[$commandName] runtime not provisioned (AGENT_LOGGER_NO_SELFPROVISION set)."
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
try {
    $_provisionedPy = Resolve-RuntimePython
    if (-not $_provisionedPy) {
        [Console]::Error.WriteLine(
            "[$commandName] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout."
        )
        [Console]::Error.WriteLine(
            "::agent-provisioning:: plugin=$commandName eta_seconds=120 reason=first-use status=$statusPath"
        )
        try {
            "provisioning $((Get-Date).ToUniversalTime().ToString('s'))Z" |
                Set-Content -LiteralPath $statusPath -Encoding utf8
        } catch {}

        Set-RuntimeEnvironment
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer provision -InstallDir $runtimeRoot 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $_provisionRc = $LASTEXITCODE
        if ($_provisionRc -eq 0) {
            $_provisionedPy = Resolve-RuntimePython
        }
    }
} finally {
    if ($_lock) { $_lock.Dispose() }
}

if ($_provisionRc -ne 0) {
    try {
        "failed rc=$_provisionRc $((Get-Date).ToUniversalTime().ToString('s'))Z" |
            Set-Content -LiteralPath $statusPath -Encoding utf8
    } catch {}
    [Console]::Error.WriteLine(
        "[$commandName] provisioning FAILED. See the log above; retry, or run: $installer provision -InstallDir $runtimeRoot"
    )
    exit $_provisionRc
}

if ($_provisionedPy) {
    try {
        "ready $((Get-Date).ToUniversalTime().ToString('s'))Z" |
            Set-Content -LiteralPath $statusPath -Encoding utf8
    } catch {}
    Invoke-Runtime -Python $_provisionedPy
}

[Console]::Error.WriteLine(
    "[$commandName] provisioning completed without a resolvable runtime."
)
exit 1
