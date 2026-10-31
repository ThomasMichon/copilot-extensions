# Installation driver contract: bridge-first-use-v1
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = @($args)
$hasPipelineInput = $MyInvocation.ExpectingInput -and $MyInvocation.Line
$pipelineInput = if ($hasPipelineInput) { @(Get-Variable input -ValueOnly) } else { @() }
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
    Join-Path $env:USERPROFILE '.agent-bridge' # marketplace-isolation: allow legacy compatibility root
}
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

if (-not (Select-String -LiteralPath $runtimeResolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-bridge] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.')
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
$statusArgs = @{
    Action = 'status'
    PayloadRoot = $payloadRoot
    PluginId = 'agent-bridge'
    LegacyRoot = $legacyRoot
}
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $statusArgs.Context = $env:COPILOT_EXTENSIONS_CONTEXT
    $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
    1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
    $statusArgs.DurableHome = $durableHome
}
$global:LASTEXITCODE = 1
$resolutionJson = @(& $modeRunner @statusArgs)
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
    $validationArgs = @{
        Action = 'validate'
        Context = $context
        DurableHome = $validationDurableHome
        ExpectedPluginId = 'agent-bridge'
        ExpectedPayloadRoot = $payloadRoot
    }
    $global:LASTEXITCODE = 1
    $validationJson = @(& $modeRunner @validationArgs)
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
    if ([string]$validatedContext.generation -cne $installGeneration -or
        [string]$validatedContext.pluginRoot -cne $runtimeRoot) {
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

if (-not $context) {
    $runtimeRoot = [string]$resolution.legacy.root
    if (-not $runtimeRoot -or -not [IO.Path]::IsPathRooted($runtimeRoot)) {
        [Console]::Error.WriteLine('[agent-bridge] installation context is missing the canonical legacy root.')
        exit 126
    }
}

function Resolve-RuntimePython {
    param([string]$TraceMode = 'ready')
    $script:AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver -TraceMode $TraceMode
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
    $nativeArgs = $forwardArgs
    $argumentMode = Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue
    if (-not $argumentMode -or $argumentMode -eq 'Legacy') {
        # Legacy PowerShell reparses native arguments, including literal quotes.
        $nativeArgs = @($forwardArgs | ForEach-Object {
            '"' + [regex]::Replace([regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
        })
    }
    # Relax EAP for just this forwarding call: the wrapped module's own
    # exit code is the only contract this dispatcher relays, so a
    # legitimate stderr diagnostic from a non-zero exit must never become
    # a terminating exception here (copilot-extensions#5494).
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($hasPipelineInput) {
            $pipelineInput | & $Python -m agent_bridge @nativeArgs
        } else {
            & $Python -m agent_bridge @nativeArgs
        }
    } finally {
        $ErrorActionPreference = $previousEap
    }
    exit $LASTEXITCODE
}

$runtimePython = Resolve-RuntimePython
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}

if ($env:AGENT_BRIDGE_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-bridge] runtime not provisioned (AGENT_BRIDGE_NO_SELFPROVISION set).'
    )
    exit 1
}

function Assert-RuntimePublication {
    $publicationArgs = @{
        Action = 'probe-plugin-publication'
        PayloadRoot = $payloadRoot
        PluginId = 'agent-bridge'
        LegacyRoot = $runtimeRoot
    }
    if ($context) {
        $publicationArgs.Context = $context
        $publicationArgs.DurableHome = $validationDurableHome
    }
    try {
        $global:LASTEXITCODE = 3
        $publicationJson = @(& $modeRunner @publicationArgs)
        $publicationCode = $LASTEXITCODE
        $publication = ($publicationJson -join "`n") | ConvertFrom-Json -ErrorAction Stop
        $publicationRoot = if ($context) {
            [string]$publication.runtimeRoot
        } else { [string]$publication.legacy.root }
        if ($publicationCode -ne 0 -or
            $publication.deployment.allowPluginPublication -isnot [bool] -or
            -not $publication.deployment.allowPluginPublication -or
            $publicationRoot -cne $runtimeRoot -or
            [string]$publication.deployment.path -cne
                (Join-Path $runtimeRoot 'runtime-deployment.json')) {
            throw ($publicationJson -join ' ')
        }
        if ($context) {
            if ([string]$publication.status -cnotin @('ready', 'deactivation-required') -or
                [string]$publication.actualMode -cne 'namespaced' -or
                [string]$publication.context -cne $context -or
                [string]$publication.installGeneration -cne $installGeneration) {
                throw ($publicationJson -join ' ')
            }
        } else {
            $legacyArgs = $publicationArgs.Clone()
            $legacyArgs.Action = 'probe-legacy'
            $global:LASTEXITCODE = 3
            $legacyJson = @(& $modeRunner @legacyArgs)
            $legacyCode = $LASTEXITCODE
            $legacy = ($legacyJson -join "`n") | ConvertFrom-Json -ErrorAction Stop
            if ($legacyCode -ne 0 -or $legacy.allowMutation -isnot [bool] -or
                -not $legacy.allowMutation -or
                [string]$legacy.legacy.root -cne $runtimeRoot) {
                throw ($legacyJson -join ' ')
            }
        }
    } catch {
        [Console]::Error.WriteLine(
            "[agent-bridge] Runtime publication refused: $($_.Exception.Message) " +
            'Use explicit installation management to install or repair this runtime.'
        )
        exit 3
    }
}

Assert-RuntimePublication
if (Test-Path Env:AGENT_BRIDGE_PINNED_CONTEXT) {
    [Console]::Error.WriteLine('[agent-bridge] pinned invocation requires a prepared owning runtime; provision independently.')
    exit 126
}
$defaultRoot = Join-Path $env:USERPROFILE '.agent-bridge'
if ($context -or $runtimeRoot -ine $defaultRoot -or
    (Test-Path Env:AGENT_BRIDGE_INSTALLATION_ID)) {
    [Console]::Error.WriteLine('[agent-bridge] cold namespaced/custom-root provisioning requires explicit installation management.')
    exit 126
}
if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
    [Console]::Error.WriteLine('[agent-bridge] nested first-use submission is unsupported; retain the current installation owner.')
    exit 126
}
$location = Get-Location
if ($location.Provider.Name -cne 'FileSystem') {
    throw 'Invocation requires a filesystem project location.'
}
$firstUse = Join-Path $payloadRoot 'libs\update-driver\scripts\first-use.ps1'
if (-not (Test-Path -LiteralPath $firstUse -PathType Leaf) -or -not (
    Select-String -LiteralPath $firstUse -SimpleMatch `
        '# Installation driver lifecycle contract: bridge-command-v1' -Quiet
)) {
    throw 'The packaged installation driver lacks the Bridge command contract.'
}
$executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
[Console]::Error.WriteLine(
    '[agent-bridge] runtime not provisioned -- waiting for the installation driver. Extend your timeout; caller exit does not cancel the operation.'
)
[Console]::Error.WriteLine(
    "::agent-provisioning:: plugin=agent-bridge reason=first-use status=$executionRoot"
)
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
[Console]::Error.WriteLine(
    '[agent-bridge] driver completed without a resolvable runtime.'
)
exit 1
