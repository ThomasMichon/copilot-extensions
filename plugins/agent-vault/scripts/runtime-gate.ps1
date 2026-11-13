$ErrorActionPreference = 'Stop'
# Installation driver contract: vault-first-use-v1
# Configuration root contract: vault-context-v1
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = @($args)
$identitySourceHome = [string]$env:AGENT_VAULT_HOME
$identitySourceId = [string]$env:AGENT_VAULT_INSTALLATION_ID
$identityCacheDir = [string]$env:AGENT_VAULT_CACHE_DIR
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_VAULT_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-vault] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$location = Get-Location
if ($location.Provider.Name -ne 'FileSystem') {
    [Console]::Error.WriteLine('[agent-vault] invocation requires a filesystem project location.')
    exit 126
}
$project = $location.ProviderPath
$invocationDirectory = $project
$hasConfigurationRoot = $false
$commandArguments = [Collections.Generic.List[string]]::new()
for ($index = 0; $index -lt $forwardArgs.Count; $index++) {
    $argument = [string]$forwardArgs[$index]
    if ($argument -ceq '--') {
        for ($remaining = $index; $remaining -lt $forwardArgs.Count; $remaining++) {
            $commandArguments.Add([string]$forwardArgs[$remaining])
        }
        break
    }
    $selectedRoot = $null
    if ($argument -ceq '--configuration-root') {
        if ($index + 1 -ge $forwardArgs.Count) {
            [Console]::Error.WriteLine('[agent-vault] --configuration-root requires a directory.')
            exit 2
        }
        $index++
        $selectedRoot = [string]$forwardArgs[$index]
    } elseif ($argument.StartsWith('--configuration-root=', [StringComparison]::Ordinal)) {
        $selectedRoot = $argument.Substring('--configuration-root='.Length)
    }
    if ($null -ne $selectedRoot) {
        if ($hasConfigurationRoot) {
            [Console]::Error.WriteLine('[agent-vault] --configuration-root may be supplied only once.')
            exit 2
        }
        $validRoot = $false
        try {
            $selectedPathRoot = [IO.Path]::GetPathRoot($selectedRoot)
            $validRoot = [IO.Path]::IsPathRooted($selectedRoot) -and
                [string]::Equals(
                    $selectedPathRoot,
                    [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($selectedRoot)),
                    [StringComparison]::OrdinalIgnoreCase
                ) -and
                (Test-Path -LiteralPath $selectedRoot -PathType Container)
        } catch { }
        if (-not $validRoot) {
            [Console]::Error.WriteLine('[agent-vault] --configuration-root must name an existing absolute directory.')
            exit 2
        }
        $project = $selectedRoot
        $hasConfigurationRoot = $true
    } else {
        $commandArguments.Add($argument)
    }
}
if ($hasConfigurationRoot) {
    if ($env:_AGENT_WORKTREES_VAULT_RUNTIME_GUARD -ceq '1') {
        Remove-Item Env:AGENT_VAULT_NO_SELFPROVISION -ErrorAction SilentlyContinue
    }
    Remove-Item Env:_AGENT_WORKTREES_VAULT_RUNTIME_GUARD -ErrorAction SilentlyContinue
}
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$runtimeEnvironment = Join-Path $scriptDir 'runtime-environment.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = if ($env:AGENT_VAULT_HOME) {
    $env:AGENT_VAULT_HOME
} else {
    Join-Path $env:USERPROFILE '.agent-vault' # marketplace-isolation: allow legacy compatibility root
}
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeEnvironment -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-vault] installation-context runtime support is unavailable.'
    )
    exit 126
}
. $runtimeEnvironment
if (-not (Select-String -LiteralPath $runtimeResolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-vault] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.')
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-vault] PowerShell host executable is unavailable.')
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
    '-PluginId', 'agent-vault',
    '-LegacyRoot', $legacyRoot, '-ProjectRoot', $project
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
        '[agent-vault] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-vault] installation context returned malformed status.'
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
            '[agent-vault] requested installation context is not active.'
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
            "[agent-vault] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    $marketplaceId = [string]$resolution.marketplaceId
    if (-not $runtimeRoot -or -not $context) {
        [Console]::Error.WriteLine(
            '[agent-vault] active installation context is incomplete.'
        )
        exit 126
    }
    $serviceSuffix = Get-RuntimeServiceSuffix -RuntimeRoot $runtimeRoot
    $installationId = if ($marketplaceId) { "$marketplaceId/agent-vault" } else { '' }
    $validationDurableHome = $context
    1..5 | ForEach-Object {
        $validationDurableHome = Split-Path -Parent $validationDurableHome
    }
    $validationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'validate',
        '-Context', $context,
        '-DurableHome', $validationDurableHome,
        '-ExpectedPluginId', 'agent-vault',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-vault] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-vault] installation context validation was malformed.'
        )
        exit 126
    }
    if ([string]$validatedContext.generation -cne $installGeneration) {
        [Console]::Error.WriteLine(
            '[agent-vault] installation context generation does not match governance.'
        )
        exit 126
    }
}
else {
    [Console]::Error.WriteLine(
        "[agent-vault] installation context blocks invocation: " +
        "status=$resolutionStatus reason=$resolutionReason."
    )
    exit 126
}

if (-not $context) {
    $runtimeRoot = [string]$resolution.legacy.root
    if (-not $runtimeRoot -or -not [IO.Path]::IsPathRooted($runtimeRoot)) {
        [Console]::Error.WriteLine('[agent-vault] installation context is missing the canonical legacy root.')
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

function Invoke-Runtime {
    param([Parameter(Mandatory)][string]$Python)
    Set-RuntimeEnvironment -RuntimeRoot $runtimeRoot -Context $context `
        -InstallationId $installationId -ServiceSuffix $serviceSuffix
    if (Test-Path Env:AGENT_VAULT_PINNED_CONTEXT) {
        $validationArgs = @(
            '-I', (Join-Path $scriptDir 'validate_identity_cache_context.py'),
            "--runtime-root=$runtimeRoot", "--installation-id=$installationId",
            "--source-home=$identitySourceHome", "--source-id=$identitySourceId",
            "--cache-dir=$identityCacheDir"
        )
        $validatedCache = @(& $Python @validationArgs)
        if ($LASTEXITCODE -ne 0) { exit 126 }
        $env:AGENT_VAULT_CACHE_DIR = ($validatedCache -join "`n").Trim()
    }
    # Relax EAP for just this forwarding call: the wrapped module's own
    # exit code is the only contract this dispatcher relays, so a
    # legitimate stderr diagnostic from a non-zero exit must never become
    # a terminating exception here (copilot-extensions#5494).
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -I -X utf8 -m agent_vault @forwardArgs
    } finally {
        $ErrorActionPreference = $previousEap
    }
    exit $LASTEXITCODE
}

$runtimePython = Resolve-RuntimePython
if ($commandArguments.Count -gt 0 -and $commandArguments[0] -ceq 'invocation-contract') {
    if ($commandArguments.Count -ne 2 -or $commandArguments[1] -cne '--json') {
        [Console]::Error.WriteLine('[agent-vault] usage: invocation-contract --json')
        exit 2
    }
    if ($runtimePython) { Invoke-Runtime -Python $runtimePython }
    [Console]::Out.WriteLine('{"schema":"agent-vault.invocation","version":1,"capabilities":[],"runtime_prepared":false}')
    exit 0
}
if (-not (Test-Path Env:AGENT_VAULT_PINNED_CONTEXT)) {
    try {
        . (Join-Path $scriptDir 'runtime-activation.ps1')
        $activation = Get-VaultRuntimeActivation -Project $project -InvocationDirectory $invocationDirectory -RuntimeRoot $runtimeRoot -Context $context -Python $runtimePython -RetainNativeSelector
    } catch {
        if ($_.Exception.Data['VaultRepositoryContextMissing']) {
            [Console]::Error.WriteLine("[agent-vault] repository context is missing. Run from an adopted repository or use '<repo> vault unlock' from any directory.")
        } else {
            [Console]::Error.WriteLine('[agent-vault] activation configuration could not be resolved; correct its configuration, binding or payload.')
        }
        exit 3
    }
    if ($activation.state -ceq 'configuration-empty') {
        if ($commandArguments.Count -gt 0 -and $commandArguments[0] -ceq 'installer-readiness') {
            $activation | ConvertTo-Json -Compress
            exit 0
        }
        if ($commandArguments.Count -gt 0 -and $commandArguments[0] -in @('stop', 'lock')) {
            $env:AGENT_VAULT_NO_SELFPROVISION = '1'
        } else {
            [Console]::Error.WriteLine($activation.detail)
            exit 2
        }
    }
}
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}
if (
    $commandArguments.Count -ge 2 -and $commandArguments -contains '--require-running' -and
    (
        ($commandArguments[0] -eq 'identity-key' -and $commandArguments[1] -eq 'setup') -or
        ($commandArguments[0] -eq 'ssh-key' -and $commandArguments[1] -in @('prepare', 'install'))
    )
) {
    [Console]::Out.WriteLine(
        '{"version":1,"error":"service_unavailable","message":"Provision and start the selected vault independently before delegated setup."}'
    )
    exit 1
}
if (Test-Path Env:AGENT_VAULT_PINNED_CONTEXT) {
    [Console]::Error.WriteLine(
        '[agent-vault] pinned identity reads require a prepared owning runtime; provision independently.'
    )
    exit 126
}

if ($env:AGENT_VAULT_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-vault] runtime not provisioned (AGENT_VAULT_NO_SELFPROVISION set).'
    )
    exit 1
}

$publicationArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'probe-plugin-publication', '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-vault', '-LegacyRoot', $runtimeRoot, '-ProjectRoot', $project
)
if ($context) {
    $publicationArgs += @('-Context', $context, '-DurableHome', $validationDurableHome)
}
$publicationJson = @(& $hostExe @publicationArgs)
$publicationCode = $LASTEXITCODE
try {
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
} catch {
    [Console]::Error.WriteLine(
        "[agent-vault] Runtime publication refused: $($_.Exception.Message) " +
        'Use explicit installation management to install or repair this runtime.'
    )
    exit 3
}
$runtimePython = Resolve-RuntimePython
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}
if ($context) {
    [Console]::Error.WriteLine(
        '[agent-vault] cold namespaced provisioning requires a qualified installation driver; use explicit installation management.'
    )
    exit 126
}
$firstUse = Join-Path $payloadRoot 'libs\update-driver\scripts\first-use.ps1'
if (-not (Test-Path -LiteralPath $firstUse -PathType Leaf)) {
    [Console]::Error.WriteLine(
        '[agent-vault] packaged installation driver is unavailable; update or repair the owning payload.'
    )
    exit 126
}
$executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
$nativeSelector = $activation.PSObject.Properties['native_configuration_file']
if ($nativeSelector) {
    $env:AGENT_VAULT_CONFIG = [string]$nativeSelector.Value
}
[Console]::Error.WriteLine(
    "[agent-vault] submitting first-use provisioning to the installation driver: $executionRoot"
)
$previousEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $nativeShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Set-RuntimeEnvironment -RuntimeRoot $runtimeRoot -Context $context `
        -InstallationId $installationId -ServiceSuffix $serviceSuffix
    & $nativeShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $firstUse `
        -Root $executionRoot -Payload $payloadRoot -RuntimeRoot $runtimeRoot -Project $project -Wait
    $provisionCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEap
}
if ($provisionCode -ne 0) { exit $provisionCode }
$runtimePython = Resolve-RuntimePython
if ($runtimePython) { Invoke-Runtime -Python $runtimePython }
[Console]::Error.WriteLine('[agent-vault] driver completed without a resolvable runtime.')
exit 1
