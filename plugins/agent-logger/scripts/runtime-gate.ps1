# Installation driver contract: logger-first-use-v1
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
    Join-Path $env:USERPROFILE '.agent-logger' # marketplace-isolation: allow legacy compatibility root
}
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
$legacyUntransferred = (
    $null -ne $resolution.legacy -and
    [string]$resolution.legacy.disposition -ceq 'active' -and
    $null -ne $resolution.legacy.PSObject.Properties['tombstone'] -and
    $null -eq $resolution.legacy.tombstone
)
$simplePolicyLegacy = $false
if (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    -not $policyPresent -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    $legacyUntransferred -and
    [string]$resolution.policy.state -ceq 'missing' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled -and
    [string]$resolution.policy.reason -ceq 'policy-default-false'
) {
    $simplePolicyLegacy = $true
}
elseif (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    $policyPresent -and
    $legacyUntransferred -and
    [string]$resolution.policy.state -ceq 'valid' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled
) {
    try {
        $policyDocument = Get-Content -LiteralPath $policy -Raw |
            ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-logger] installation policy could not be read.'
        )
        exit 126
    }
    if ($policyDocument -isnot [pscustomobject]) {
        [Console]::Error.WriteLine(
            '[agent-logger] installation context blocks invocation: invalid policy document.'
        )
        exit 126
    }
    $installationMode = $policyDocument.PSObject.Properties['installationMode']
    if (
        $null -ne $installationMode -and
        $installationMode.Value -isnot [pscustomobject]
    ) {
        [Console]::Error.WriteLine(
            '[agent-logger] installation context blocks invocation: invalid installation mode.'
        )
        exit 126
    }
    $enabled = if ($null -ne $installationMode) {
        $installationMode.Value.PSObject.Properties['enabled']
    } else { $null }
    $marketplaces = if ($null -ne $installationMode) {
        $installationMode.Value.PSObject.Properties['marketplaces']
    } else { $null }
    $simplePolicyLegacy = (
        (
            $null -eq $enabled -or
            ($enabled.Value -is [bool] -and -not $enabled.Value)
        ) -and
        (
            $null -eq $marketplaces -or
            (
                $marketplaces.Value -is [pscustomobject] -and
                @($marketplaces.Value.PSObject.Properties).Count -eq 0
            )
        )
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
    $runtimeRoot = [string]$resolution.legacy.root
    if (-not $runtimeRoot -or -not [IO.Path]::IsPathRooted($runtimeRoot)) {
        [Console]::Error.WriteLine('[agent-logger] installation context is missing the canonical legacy root.')
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
    param([string]$TraceMode = 'ready')
    $script:AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    if (-not (Select-String -LiteralPath $runtimeResolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
        throw 'Read-only runtime resolver is unavailable; update or repair the owning payload.'
    }
    . $runtimeResolver -TraceMode $TraceMode
    return $AgentRtPy
}

function Set-RuntimeEnvironment {
    $env:AGENT_LOGGER_HOME = $runtimeRoot
    if ($installationId) {
        $env:AGENT_LOGGER_INSTALLATION_ID = $installationId
        $env:AGENT_LOGGER_TIMER_NAME = "agent-logger-sync-$serviceSuffix"
        $env:AGENT_LOGGER_TASK_NAME = "Agent Logger Session Sync - $serviceSuffix" # marketplace-isolation: allow cell-derived-suffix
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
    # Relax EAP for just this forwarding call: the wrapped module's own
    # exit code is the only contract this dispatcher relays, so a
    # legitimate stderr diagnostic from a non-zero exit must never become
    # a terminating exception here (copilot-extensions#5494).
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -m $moduleName @forwardArgs
    } finally {
        $ErrorActionPreference = $previousEap
    }
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

if (Test-Path Env:AGENT_LOGGER_PINNED_CONTEXT) {
    [Console]::Error.WriteLine('[agent-logger] pinned invocation requires a prepared owning runtime; provision independently.')
    exit 126
}
$defaultRoot = Join-Path $env:USERPROFILE '.agent-logger'
if ($context -or $runtimeRoot -ine $defaultRoot -or
    (Test-Path Env:AGENT_LOGGER_INSTALLATION_ID)) {
    [Console]::Error.WriteLine('[agent-logger] cold namespaced/custom-root provisioning requires explicit installation management.')
    exit 126
}
if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
    [Console]::Error.WriteLine('[agent-logger] nested first-use submission is unsupported; retain the current installation owner.')
    exit 126
}
$location = Get-Location
if ($location.Provider.Name -cne 'FileSystem') {
    throw 'Invocation requires a filesystem project location.'
}
$firstUse = Join-Path $payloadRoot 'libs\update-driver\scripts\first-use.ps1'
if (-not (Test-Path -LiteralPath $firstUse -PathType Leaf) -or -not (
    Select-String -LiteralPath $firstUse -SimpleMatch `
        '# Installation driver lifecycle contract: logger-command-v1' -Quiet
)) {
    throw 'The packaged installation driver lacks the Logger command contract.'
}
$executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
$previousEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $nativeShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $nativeShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $firstUse `
        -Root $executionRoot -Payload $payloadRoot -RuntimeRoot $runtimeRoot `
        -Project $location.ProviderPath -InstallerAction provision -Wait
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousEap }
if ($code -ne 0) { exit $code }
$runtimePython = Resolve-RuntimePython
if ($runtimePython) { Invoke-Runtime -Python $runtimePython }
[Console]::Error.WriteLine("[$commandName] driver completed without a resolvable runtime.")
exit 1
