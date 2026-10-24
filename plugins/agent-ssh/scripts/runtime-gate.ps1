# Installation driver contract: ssh-first-use-v1
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = @($args)
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_SSH_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-ssh] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = Join-Path $env:USERPROFILE '.agent-ssh' # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-ssh] installation-context runtime support is unavailable.'
    )
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-ssh] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'status',
    '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-ssh',
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
        '[agent-ssh] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-ssh] installation context returned malformed status.'
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
            '[agent-ssh] requested installation context is not active.'
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
            "[agent-ssh] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    if (-not $runtimeRoot -or -not $context) {
        [Console]::Error.WriteLine(
            '[agent-ssh] active installation context is incomplete.'
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
        '-ExpectedPluginId', 'agent-ssh',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-ssh] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-ssh] installation context validation was malformed.'
        )
        exit 126
    }
    if ([string]$validatedContext.generation -cne $installGeneration) {
        [Console]::Error.WriteLine(
            '[agent-ssh] installation context generation does not match governance.'
        )
        exit 126
    }
}
else {
    [Console]::Error.WriteLine(
        "[agent-ssh] installation context blocks invocation: " +
        "status=$resolutionStatus reason=$resolutionReason."
    )
    exit 126
}

function Resolve-RuntimePython {
    $script:AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    if (-not (Select-String -LiteralPath $runtimeResolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
        throw 'Read-only runtime resolver is unavailable; update or repair the owning payload.'
    }
    . $runtimeResolver -TraceMode ready
    return $AgentRtPy
}

function Invoke-Runtime {
    param([Parameter(Mandatory)][string]$Python)
    $env:AGENT_SSH_HOME = $runtimeRoot
    $env:AGENT_SSH_PAYLOAD_ROOT = $payloadRoot
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
    # Relax EAP for just this forwarding call: the wrapped module's own
    # exit code is the only contract this dispatcher relays, so a
    # legitimate stderr diagnostic from a non-zero exit must never become
    # a terminating exception here (copilot-extensions#5494).
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -m agent_ssh @forwardArgs
    } finally {
        $ErrorActionPreference = $previousEap
    }
    exit $LASTEXITCODE
}

$runtimePython = Resolve-RuntimePython
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}

if ($env:AGENT_SSH_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-ssh] runtime not provisioned (AGENT_SSH_NO_SELFPROVISION set).'
    )
    exit 1
}

if (Test-Path Env:AGENT_SSH_PINNED_CONTEXT) {
    [Console]::Error.WriteLine('[agent-ssh] pinned invocation requires a prepared owning runtime; provision independently.')
    exit 126
}
if ($context -or $runtimeRoot -ine $legacyRoot -or
    ($env:AGENT_SSH_HOME -and $env:AGENT_SSH_HOME -ine $legacyRoot)) {
    [Console]::Error.WriteLine('[agent-ssh] cold namespaced/custom-root provisioning requires explicit installation management.')
    exit 126
}
if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
    [Console]::Error.WriteLine('[agent-ssh] nested first-use submission is unsupported; retain the current installation owner.')
    exit 126
}
$location = Get-Location
if ($location.Provider.Name -cne 'FileSystem') {
    throw 'Invocation requires a filesystem project location.'
}
$project = $location.ProviderPath
$executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
function Invoke-FirstUse {
    param([string]$Payload, [string]$Action)
    $firstUse = Join-Path $Payload 'libs\update-driver\scripts\first-use.ps1'
    if (-not (Test-Path -LiteralPath $firstUse -PathType Leaf) -or -not (
        Select-String -LiteralPath $firstUse -SimpleMatch `
            '# Installation driver lifecycle contract: ssh-command-v1' -Quiet
    )) {
        throw 'The packaged installation driver lacks the SSH stamp lifecycle contract.'
    }
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $nativeShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        & $nativeShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $firstUse `
            -Root $executionRoot -Payload $Payload -RuntimeRoot $runtimeRoot `
            -Project $project -InstallerAction $Action -Wait
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousEap }
    if ($code -ne 0) { exit $code }
}

Invoke-FirstUse -Payload $payloadRoot -Action stamp-provision
$runtimePython = Resolve-RuntimePython
if ($runtimePython) { Invoke-Runtime -Python $runtimePython }
[Console]::Error.WriteLine('[agent-ssh] driver completed without a resolvable runtime.')
exit 1
