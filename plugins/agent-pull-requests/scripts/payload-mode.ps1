# Native governance admission must work before any Python has been installed.
$ErrorActionPreference = 'Stop'
$payload = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$legacy = Join-Path $env:USERPROFILE '.agent-pull-requests'
$hostExe = (Get-Process -Id $PID).Path
$forward = @(
    '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
    (Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'),
    'status', '-PayloadRoot', $payload, '-PluginId', 'agent-pull-requests',
    '-LegacyRoot', $legacy
)
if (-not (Get-Item -LiteralPath $legacy -Force -ErrorAction SilentlyContinue)) {
    $forward += @('-LegacyProbeJson', '{"declared":true,"result":"absent","checkedAt":null}')
}
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $receipt = $env:COPILOT_EXTENSIONS_CONTEXT
    if (-not [IO.Path]::IsPathRooted($receipt) -or
        (Split-Path -Leaf $receipt) -cne 'install.json' -or
        (Split-Path -Leaf (Split-Path -Parent $receipt)) -cne 'agent-pull-requests') {
        throw 'Explicit context must be an absolute canonical install receipt'
    }
    $durable = $receipt
    1..5 | ForEach-Object { $durable = Split-Path -Parent $durable }
    $forward += @(
        '-Context', $receipt, '-DurableHome', $durable,
        '-ExpectedPluginId', 'agent-pull-requests', '-ExpectedPayloadRoot', $payload
    )
}
$raw = @(& $hostExe @forward)
if ($LASTEXITCODE -ne 0) { throw 'Installation governance could not be resolved' }
$status = ($raw -join "`n") | ConvertFrom-Json
$legacyAllowed = (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and $status.status -ceq 'ready' -and
    $status.actualMode -ceq 'legacy' -and $status.desiredMode -ceq 'legacy'
)
if (-not $env:COPILOT_EXTENSIONS_CONTEXT -and
    $status.status -ceq 'provenance-blocked' -and
    $status.policy.state -cin @('missing', 'valid') -and
    $status.policy.enabled -is [bool] -and -not $status.policy.enabled -and
    $status.legacy.disposition -ceq 'active' -and $null -eq $status.legacy.tombstone) {
    # With no source identity, scoped overrides cannot be evaluated safely.
    $policy = Get-Item -LiteralPath $status.policy.path -Force -ErrorAction SilentlyContinue
    if (-not $policy) {
        $legacyAllowed = $status.policy.state -ceq 'missing'
    } else {
        $document = Get-Content -LiteralPath $policy.FullName -Raw | ConvertFrom-Json
        $mode = @($document.PSObject.Properties | Where-Object { $_.Name -ceq 'installationMode' })
        $overrides = @()
        if ($mode.Count -eq 1) {
            $overrides = @($mode[0].Value.PSObject.Properties | Where-Object { $_.Name -ceq 'marketplaces' })
        }
        $legacyAllowed = $overrides.Count -eq 0 -or @($overrides[0].Value.PSObject.Properties).Count -eq 0
    }
}
if ($legacyAllowed) { 'legacy'; return }
if ($status.status -ceq 'ready' -and
    ($status.reason -ceq 'namespaced-active' -or $status.reason -ceq 'activation-required')) {
    'cell'; return
}
throw "Installation blocks invocation: status=$($status.status) reason=$($status.reason)"
