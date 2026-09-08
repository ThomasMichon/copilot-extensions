$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = @($args)
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_MCP_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-mcp] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'init.ps1'
$legacyRoot = if ($env:AGENT_MCP_HOME) {
    $env:AGENT_MCP_HOME
} else {
    Join-Path $env:USERPROFILE '.agent-mcp'
} # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-mcp] installation-context runtime support is unavailable.'
    )
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-mcp] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$marketplaceRoot = ''
$context = ''
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'status',
    '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-mcp',
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
        '[agent-mcp] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-mcp] installation context returned malformed status.'
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
            '[agent-mcp] requested installation context is not active.'
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
            "[agent-mcp] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    if (-not $runtimeRoot -or -not $context) {
        [Console]::Error.WriteLine(
            '[agent-mcp] active installation context is incomplete.'
        )
        exit 126
    }
    $marketplaceRoot = Split-Path -Parent $payloadRoot
    $validationDurableHome = $context
    1..5 | ForEach-Object {
        $validationDurableHome = Split-Path -Parent $validationDurableHome
    }
    $validationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'validate',
        '-Context', $context,
        '-DurableHome', $validationDurableHome,
        '-ExpectedPluginId', 'agent-mcp',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-mcp] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-mcp] installation context validation was malformed.'
        )
        exit 126
    }
    if ([string]$validatedContext.generation -cne $installGeneration) {
        [Console]::Error.WriteLine(
            '[agent-mcp] installation context generation does not match governance.'
        )
        exit 126
    }
}
else {
    [Console]::Error.WriteLine(
        "[agent-mcp] installation context blocks invocation: " +
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

function Invoke-Runtime {
    param([Parameter(Mandatory)][string]$Python)
    $env:AGENT_MCP_HOME = $runtimeRoot
    if ($marketplaceRoot) {
        $env:AGENT_MCP_MARKETPLACE_ROOT = $marketplaceRoot
    } else {
        Remove-Item Env:AGENT_MCP_MARKETPLACE_ROOT -ErrorAction SilentlyContinue
    }
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
    & $Python -m agent_mcp @forwardArgs
    exit $LASTEXITCODE
}

$runtimePython = Resolve-RuntimePython
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}

if ($env:AGENT_MCP_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-mcp] runtime not provisioned (AGENT_MCP_NO_SELFPROVISION set).'
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
            '[agent-mcp] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.'
        )
        [Console]::Error.WriteLine(
            "::agent-provisioning:: plugin=agent-mcp eta_seconds=120 reason=first-use status=$statusPath"
        )
        try {
            "provisioning $((Get-Date).ToUniversalTime().ToString('s'))Z" |
                Set-Content -LiteralPath $statusPath -Encoding utf8
        } catch {}

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
        "[agent-mcp] provisioning FAILED. See the log above; retry, or run: $installer provision -InstallDir $runtimeRoot"
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

try {
    "failed rc=1 $((Get-Date).ToUniversalTime().ToString('s'))Z" |
        Set-Content -LiteralPath $statusPath -Encoding utf8
} catch {}
[Console]::Error.WriteLine(
    '[agent-mcp] provisioning reported success but no runtime slot resolved.'
)
exit 1
