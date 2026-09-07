<#
    agent-machines session-start hook -- version-gated runtime reconcile.

    Runs at session start (via hooks.json). Ensures the installed
    `agent-machines` binstub/venv matches the plugin source version, so a
    `copilot plugin update` that bumps the payload is picked up automatically --
    without ever running machine *restoration* itself.

    Fast path: compare the deployed and payload versions. Legacy deployments
    read ~/.agent-machines/deploy-manifest.json; an explicit validated
    installation context may redirect that read to its plugin root. Namespaced
    writes remain blocked until the context-aware installer is operative.

    Deployed to ~/.agent-machines/bin/ by scripts/init.ps1. Never installs from
    scratch (that is the one-time `agent-machines-setup` step) -- it only exists
    once the runtime has been installed, and only reconciles staleness. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'
$script:SessionStartJsonEmitted = $false
function Write-SessionStartJson {
    if (-not $script:SessionStartJsonEmitted) {
        [Console]::Out.Write('{}')
        $script:SessionStartJsonEmitted = $true
    }
}
function Exit-SessionStart {
    Write-SessionStartJson
    exit 0
}

$PluginDir = Split-Path -Parent $PSScriptRoot
function Test-LegacyMutationAllowed {
    $probe = Join-Path $PSScriptRoot 'installation-context\legacy-entrypoint-probe.ps1'
    if (-not (Test-Path -LiteralPath $probe -PathType Leaf)) {
        [Console]::Error.WriteLine('[agent-machines] legacy mutation probe is unavailable; skipping reconcile.')
        return $false
    }
    $hostExe = (Get-Process -Id $PID).Path
    if (-not $hostExe) { return $false }
    $global:LASTEXITCODE = 1
    try {
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $probe `
            -PayloadRoot $PluginDir -LegacyRoot (Join-Path $env:USERPROFILE '.agent-machines') |
            Out-Null
    } catch {
        return $false
    }
    return $LASTEXITCODE -eq 0
}
$contextSelected = $false
$contextActive = $false
$contextPath = ''
$contextMarketplaceId = ''
$InstallDir = Join-Path $env:USERPROFILE '.agent-machines'
$policyPath = Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'
$policyPresent = (
    (Test-Path -LiteralPath $policyPath) -or
    $null -ne (
        Get-Item -LiteralPath $policyPath -Force -ErrorAction SilentlyContinue
    )
)
    $resolver = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path $resolver)) {
        [Console]::Error.WriteLine('[agent-machines] installation context is selected but its validator is unavailable; skipping reconcile.')
        Exit-SessionStart
    }
    $hostExe = (Get-Process -Id $PID).Path
    $statusArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $resolver,
        'status',
        '-PayloadRoot', $PluginDir,
        '-PluginId', 'agent-machines',
        '-LegacyRoot', (Join-Path $env:USERPROFILE '.agent-machines') # marketplace-isolation: allow legacy compatibility root
    )
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $statusArgs += @('-Context', $env:COPILOT_EXTENSIONS_CONTEXT)
        $contextDurableHome = $env:COPILOT_EXTENSIONS_CONTEXT
        1..5 | ForEach-Object {
            $contextDurableHome = Split-Path -Parent $contextDurableHome
        }
        $statusArgs += @('-DurableHome', $contextDurableHome)
    }
    $statusJson = @(& $hostExe @statusArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine('[agent-machines] installation status is invalid; skipping reconcile without legacy fallback.')
        Exit-SessionStart
    }
    try { $status = ($statusJson -join "`n") | ConvertFrom-Json } catch {
        [Console]::Error.WriteLine('[agent-machines] installation status is malformed; skipping reconcile without legacy fallback.')
        Exit-SessionStart
    }
    $simplePolicyLegacy = $false
    if (
        -not $env:COPILOT_EXTENSIONS_CONTEXT -and
        [string]$status.status -ceq 'provenance-blocked' -and
        $status.policy.enabled -is [bool] -and
        -not $status.policy.enabled -and
        $null -eq $status.legacy.tombstone -and
        [string]$status.legacy.disposition -ceq 'active'
    ) {
        if (
            -not $policyPresent -and
            [string]$status.policy.state -ceq 'missing' -and
            [string]$status.policy.reason -ceq 'policy-default-false'
        ) {
            $simplePolicyLegacy = $true
        }
        elseif ([string]$status.policy.state -ceq 'valid') {
            try {
                $policyDocument = Get-Content -LiteralPath $policyPath -Raw |
                    ConvertFrom-Json
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
    }
    if (
        (
            [string]$status.status -ceq 'ready' -and
            [string]$status.actualMode -ceq 'legacy' -and
            [string]$status.desiredMode -ceq 'legacy'
        ) -or
        $simplePolicyLegacy
    ) {
        if ($env:COPILOT_EXTENSIONS_CONTEXT) {
            [Console]::Error.WriteLine('[agent-machines] requested installation context is not active; skipping reconcile without legacy fallback.')
            Exit-SessionStart
        }
    }
    elseif (
        (
            [string]$status.status -ceq 'ready' -and
            [string]$status.reason -ceq 'namespaced-active'
        ) -or
        [string]$status.status -ceq 'deactivation-required'
    ) {
        if ([string]$status.actualMode -cne 'namespaced') {
            Exit-SessionStart
        }
        $InstallDir = [string]$status.runtimeRoot
        $contextPath = [string]$status.context
        $contextMarketplaceId = [string]$status.marketplaceId
        if (-not $InstallDir -or -not $contextPath -or -not $contextMarketplaceId) {
            [Console]::Error.WriteLine('[agent-machines] active installation context is incomplete; skipping reconcile.')
            Exit-SessionStart
        }
        $contextSelected = $true
        $contextActive = (
            [string]$status.status -ceq 'ready' -and
            [string]$status.reason -ceq 'namespaced-active'
        )
    }
    else {
        [Console]::Error.WriteLine(
            '[agent-machines] installation governance blocks reconcile without legacy fallback: ' +
            "status=$($status.status) reason=$($status.reason)."
        )
        Exit-SessionStart
    }
$Manifest   = Join-Path $InstallDir 'deploy-manifest.json'
$Binstub    = Join-Path $env:USERPROFILE '.local\bin\agent-machines.cmd'

# Not provisioned yet -> do the cheap FIRST install ('stamp') so the binstub is
# on PATH this session; the self-provisioning binstub then builds the venv on
# first use (#1393). hooks.json runs the PAYLOAD copy, so $PSScriptRoot is the
# plugin's scripts/ dir even on a fresh box. Fires only when init.ps1 declares a
# 'stamp' action; else a safe no-op.
if (-not (Test-Path $Manifest)) {
    if ($contextSelected) {
        if ($contextActive) {
            $payloadInit = Join-Path $PSScriptRoot 'init.ps1'
            if (Test-Path $payloadInit) {
                $pw = Get-Command pwsh -ErrorAction SilentlyContinue
                $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
                $command = "& `"$payloadInit`" -Action cell-provision -Context `"$contextPath`" -ExpectedMarketplaceId `"$contextMarketplaceId`""
                $enc = [Convert]::ToBase64String(
                    [Text.Encoding]::Unicode.GetBytes($command)
                )
                Start-Process -FilePath 'conhost.exe' `
                    -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc) `
                    -WindowStyle Hidden | Out-Null
            }
        }
        Exit-SessionStart
    }
    $payloadInit = Join-Path $PSScriptRoot 'init.ps1'
    if ((Test-Path $payloadInit) -and (Select-String -Path $payloadInit -Pattern "'stamp'" -Quiet)) {
        if (-not (Test-LegacyMutationAllowed)) { Exit-SessionStart }
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        & $exe -NoProfile -ExecutionPolicy Bypass -File $payloadInit stamp *> $null
    }
    Exit-SessionStart
}

try {
    $m = Get-Content $Manifest -Raw | ConvertFrom-Json
    if ($contextSelected) {
        $manifestContext = $contextPath -replace '\\', '/'
        $sourcePathText = [string]$m.source.path
        $deployed = [string]$m.source.version
        $activeVersion = [string]$m.runtime.version
        $runtimePathText = [string]$m.runtime.path
        $runtimeInterpreterText = [string]$m.runtime.interpreter
        $selectedByVersion = [string]$m.runtime.selectedBy.version
        $validManifest = (
            (
                $m.schema_version -is [int] -or
                $m.schema_version -is [long]
            ) -and
            [long]$m.schema_version -eq 4 -and
            [string]$m.service -ceq 'agent-machines' -and
            [string]$m.source.repo -ceq 'copilot-extensions' -and
            [string]$m.source.plugin -ceq 'agent-machines' -and
            -not [string]::IsNullOrWhiteSpace([string]$m.source.kind) -and
            -not [string]::IsNullOrWhiteSpace($sourcePathText) -and
            -not [string]::IsNullOrWhiteSpace($deployed) -and
            $m.source.dirty -is [bool] -and
            [string]$m.runtime.kind -ceq 'python' -and
            -not [string]::IsNullOrWhiteSpace($activeVersion) -and
            -not [string]::IsNullOrWhiteSpace($runtimePathText) -and
            -not [string]::IsNullOrWhiteSpace($runtimeInterpreterText) -and
            -not [string]::IsNullOrWhiteSpace([string]$m.runtime.selectedBy.kind) -and
            -not [string]::IsNullOrWhiteSpace([string]$m.runtime.selectedBy.path) -and
            $selectedByVersion -ceq $activeVersion -and
            [string]$m.installation.marketplaceId -ceq $contextMarketplaceId -and
            [string]$m.installation.pluginId -ceq 'agent-machines' -and
            [string]$m.installation.context -ceq $manifestContext
        )
        if (-not $validManifest) {
            [Console]::Error.WriteLine('[agent-machines] active cell deploy manifest is invalid; skipping reconcile without legacy fallback.')
            Exit-SessionStart
        }
        try {
            if ($env:OS -eq 'Windows_NT') {
                $sourcePathText = $sourcePathText -replace '/', '\'
                $runtimePathText = $runtimePathText -replace '/', '\'
                $runtimeInterpreterText = $runtimeInterpreterText -replace '/', '\'
            }
            $sourcePath = [IO.Path]::GetFullPath($sourcePathText)
            $runtimePath = [IO.Path]::GetFullPath($runtimePathText)
            $runtimeInterpreter = [IO.Path]::GetFullPath($runtimeInterpreterText)
        } catch {
            [Console]::Error.WriteLine('[agent-machines] active cell deploy manifest is invalid; skipping reconcile without legacy fallback.')
            Exit-SessionStart
        }
        $expectedRuntimePath = Join-Path (Join-Path $InstallDir 'versions') $activeVersion
        $expectedInterpreter = if ($env:OS -eq 'Windows_NT') {
            Join-Path $expectedRuntimePath 'Scripts\python.exe'
        } else {
            Join-Path $expectedRuntimePath 'bin/python'
        }
        $pathComparer = if ($env:OS -eq 'Windows_NT') {
            [StringComparer]::OrdinalIgnoreCase
        } else {
            [StringComparer]::Ordinal
        }
        $currentMarker = Join-Path $InstallDir 'current-version'
        $markerValid = (
            (Test-Path -LiteralPath $currentMarker -PathType Leaf) -and
            -not ((Get-Item -LiteralPath $currentMarker -Force).Attributes -band
                [IO.FileAttributes]::ReparsePoint) -and
            ([IO.File]::ReadAllText($currentMarker)).Trim() -ceq $activeVersion
        )
        if (
            -not $markerValid -or
            -not $pathComparer.Equals(
                $runtimePath,
                [IO.Path]::GetFullPath($expectedRuntimePath)
            ) -or
            -not $pathComparer.Equals(
                $runtimeInterpreter,
                [IO.Path]::GetFullPath($expectedInterpreter)
            ) -or
            -not (Test-Path -LiteralPath $runtimeInterpreter -PathType Leaf)
        ) {
            [Console]::Error.WriteLine('[agent-machines] active cell deploy manifest is invalid; skipping reconcile without legacy fallback.')
            Exit-SessionStart
        }
        $current = $deployed
        $pyproj = Join-Path $PluginDir 'pyproject.toml'
        if (Test-Path $pyproj) {
            $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' |
                Select-Object -First 1
            if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
        }
        $samePayloadPath = $pathComparer.Equals(
            $sourcePath,
            [IO.Path]::GetFullPath($PluginDir)
        )
        if (($deployed -ceq $current -and $samePayloadPath) -or -not $contextActive) { Exit-SessionStart }
        $init = Join-Path $PluginDir 'scripts\init.ps1'
        if (-not (Test-Path $init)) { Exit-SessionStart }
        [Console]::Error.WriteLine("[agent-machines] active cell payload $deployed -> $current (runtime $activeVersion); reconciling in background...")
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        $command = "& `"$init`" -Action cell-provision -Context `"$contextPath`" -ExpectedMarketplaceId `"$contextMarketplaceId`""
        $enc = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes($command)
        )
        Start-Process -FilePath 'conhost.exe' `
            -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc) `
            -WindowStyle Hidden | Out-Null
        Exit-SessionStart
    }

    $pluginDir = $m.source.path
    if (-not $pluginDir) { Exit-SessionStart }
    $pluginDir = $pluginDir -replace '/', '\'
    if (-not (Test-Path $pluginDir)) { Exit-SessionStart }

    $deployed = "" + $m.source.version
    $current  = $deployed
    $pyproj = Join-Path $pluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }

    # Up to date and binstub present -> fast no-op (the common case).
    if ((Test-Path $Binstub) -and $deployed -eq $current) { Exit-SessionStart }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { Exit-SessionStart }

    if (-not (Test-LegacyMutationAllowed)) { Exit-SessionStart }
    [Console]::Error.WriteLine("[agent-machines] runtime $deployed -> $current; reconciling in background...")
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    # conhost --headless so Windows Terminal / the DefTerm handoff can't surface
    # it as a window -- -WindowStyle Hidden ALONE is ignored by DefTerm (see
    # agent-bridge). Base64-encode the reconcile command to avoid arg quoting.
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("& `"$init`""))
    Start-Process -FilePath 'conhost.exe' `
        -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc) `
        -WindowStyle Hidden | Out-Null
} catch { }

Exit-SessionStart
