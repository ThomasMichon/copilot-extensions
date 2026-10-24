# Native governance admission must work before any Python has been installed.
$ErrorActionPreference = 'Stop'
$payload = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$legacy = Join-Path $env:USERPROFILE '.agent-pull-requests'
$stageRoot = Join-Path $legacy '.install-stage'
$stage = Split-Path -Parent $payload
if ((Split-Path -Parent $stage) -eq $stageRoot) {
    # Staging hints locate evidence, never waive native governance admission.
    if ($env:COPILOT_PLUGIN_INSTALL_STAGED -cne '1' -or
        -not [IO.Path]::IsPathRooted([string]$env:COPILOT_PLUGIN_STAGED_FROM) -or
        (Split-Path -Leaf $payload) -cne 'agent-pull-requests') {
        throw 'Staged installer provenance is incomplete'
    }
    $origin = (Resolve-Path -LiteralPath $env:COPILOT_PLUGIN_STAGED_FROM).Path
    $marketplace = Split-Path -Parent $origin
    $installed = Join-Path $env:USERPROFILE '.copilot\installed-plugins'
    if ((Split-Path -Parent $marketplace) -ne $installed -or
        (Split-Path -Leaf $origin) -cne 'agent-pull-requests') {
        throw 'Staged installer origin is not a canonical installed payload'
    }
    foreach ($root in @($payload, $origin)) {
        $cursor = $root
        while ($cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Staged installer provenance may not traverse links'
            }
            $cursor = Split-Path -Parent $cursor
        }
    }
    $entries = @(Get-ChildItem -LiteralPath $payload -Recurse -Force)
    $originalEntries = @(Get-ChildItem -LiteralPath $origin -Recurse -Force)
    foreach ($entry in @($entries + $originalEntries)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Staged installer payload contains a link or mismatched entry'
        }
    }
    $completeCopy = $entries.Count -eq $originalEntries.Count
    foreach ($entry in $entries) {
        $relative = $entry.FullName.Substring($payload.Length + 1)
        $original = Get-Item -LiteralPath (Join-Path $origin $relative) -Force -ErrorAction SilentlyContinue
        if (-not $original -or $entry.PSIsContainer -ne $original.PSIsContainer) {
            $completeCopy = $false
            break
        }
        if (-not $entry.PSIsContainer -and
            (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath $original.FullName -Algorithm SHA256).Hash) {
            $completeCopy = $false
            break
        }
    }
    if (-not $completeCopy) {
        # The canonical freezer excludes generated artifacts and empty directories.
        $generated = @('.git', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache',
            'build', 'dist', 'node_modules')
        $sourceFiles = @($originalEntries | Where-Object {
            if ($_.PSIsContainer) { return $false }
            $parts = $_.FullName.Substring($origin.Length + 1).Split('\')
            for ($i = 0; $i -lt $parts.Length - 1; $i++) {
                if ($parts[$i] -cin $generated -or $parts[$i].EndsWith('.egg-info') -or
                    $parts[$i].EndsWith('.dist-info')) { return $false }
            }
            return -not $_.Name.EndsWith('.pyc') -and -not $_.Name.EndsWith('.pyo')
        })
        $stagedFiles = @($entries | Where-Object { -not $_.PSIsContainer })
        if ($stagedFiles.Count -ne $sourceFiles.Count) {
            throw 'Staged installer payload differs from its origin'
        }
        foreach ($original in $sourceFiles) {
            $relative = $original.FullName.Substring($origin.Length + 1)
            $copy = Get-Item -LiteralPath (Join-Path $payload $relative) -Force -ErrorAction SilentlyContinue
            if (-not $copy -or $copy.PSIsContainer -or
                (Get-FileHash -LiteralPath $copy.FullName -Algorithm SHA256).Hash -cne
                (Get-FileHash -LiteralPath $original.FullName -Algorithm SHA256).Hash) {
                throw 'Staged installer payload differs from its origin'
            }
        }
    }
    $payload = $origin
}
$helper = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
$forward = @{
    Action = 'status'; PayloadRoot = $payload
    PluginId = 'agent-pull-requests'; LegacyRoot = $legacy
}
if (-not (Get-Item -LiteralPath $legacy -Force -ErrorAction SilentlyContinue)) {
    $forward.LegacyProbeJson = '{"declared":true,"result":"absent","checkedAt":null}'
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
    $forward.Context = $receipt
    $forward.DurableHome = $durable
    $forward.ExpectedPluginId = 'agent-pull-requests'
    $forward.ExpectedPayloadRoot = $payload
}
$raw = @(& $helper @forward)
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
    # Without source identity, only absent/empty overrides admit legacy mode.
    # The native validator has already rejected invalid member types.
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
