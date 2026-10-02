# --- bootstrap-killswitch guard (vendored; see libs/bootstrap-killswitch/README.md) ---
# One shared, repo-wide switch (not per-plugin) that pauses EVERY adopting
# plugin's reconcile-on-session-start at once, for when an operator/agent is
# hand-diagnosing a venv/install and a background reconcile must not race it.
# Legacy/default installation ONLY: a namespaced marketplace cell
# (COPILOT_EXTENSIONS_CONTEXT set) reconciles through its own cell-scoped
# mechanism, never this global state file -- crossing that installation-cell
# boundary would let one marketplace's switch pause an unrelated,
# independently-owned cell's reconcile (visions/plugin-services/
# installation-cells). This guard is therefore a deliberate no-op under a
# cell context, same as this hook's own existing cell-context exit below.
if (-not $env:COPILOT_EXTENSIONS_CONTEXT) {
  $_bksGuardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $_bksGuard = Join-Path $_bksGuardDir "bootstrap-killswitch-guard.ps1"
  if (Test-Path -LiteralPath $_bksGuard) {
    & $_bksGuard check *>&2
    if ($LASTEXITCODE -eq 0) { [Console]::Out.Write('{}'); exit 0 }
  }
}
# --- end bootstrap-killswitch guard ---

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
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        # Two-stage stamp (#3303, full-harness-startup-reliability Round 2):
        # 1. SYNCHRONOUSLY run the fast 'stamp-binstub' action -- just a
        #    couple of small file writes under a lock, sub-second -- so the
        #    launcher is guaranteed on PATH before this hook returns. A prior
        #    version backgrounded the WHOLE 'stamp' action (including its
        #    snapshot copy of the entire plugin payload, independently
        #    measured at ~9s standalone), leaving a real window where the
        #    session's first turn could invoke `agent-machines` before the
        #    background job had created it (command-not-found race).
        # 2. Background the full 'stamp' (unchanged) so the slower snapshot
        #    copy still completes, without blocking the hook on it.
        # NOTE: no Test-LegacyMutationAllowed pre-check here -- init.ps1's OWN
        # top-level dispatch (every $Action except the cell-/slot- family)
        # already re-runs the SAME legacy-entrypoint-probe with equivalent
        # -PayloadRoot/-LegacyRoot values before 'stamp-binstub' or 'stamp'
        # ever executes, so a separate pre-check here would only re-validate
        # what each call already validates on its own; init.ps1 exits
        # non-zero on its own if the probe disallows the mutation, which this
        # hook already discards output/exit code for either way.
        # Review finding (round 4): removing the pre-check does NOT reduce
        # the probe cost to one spawn per hook invocation the way an earlier
        # version of this comment claimed -- the two-stage split itself now
        # makes TWO separate init.ps1 invocations ('stamp-binstub' then
        # 'stamp'), and EACH one re-runs its own probe subprocess via its own
        # top-level dispatch. Net probe-process count here is therefore
        # unchanged from before this split (removing the old single explicit
        # pre-check saves one spawn; the new second init.ps1 invocation's own
        # embedded probe adds one back). The synchronous 'stamp-binstub' path
        # still pays this probe's cost every time. Carrying a validated
        # result across the two stages to actually cut this would need a
        # short-lived trust token between the two init.ps1 invocations --
        # real future work, out of scope for this short-PR-cycle round.
        & $exe -NoProfile -ExecutionPolicy Bypass -File $payloadInit stamp-binstub *> $null
        # Review finding: the launcher-before-return guarantee this split
        # exists for does not hold if the synchronous stage itself failed
        # (probe denial, lock timeout, file-write failure) -- a discarded
        # nonzero exit here still backgrounded the full 'stamp' and returned
        # success, silently leaving no binstub. Check $LASTEXITCODE and skip
        # backgrounding 'stamp' when the fast stage did not actually publish
        # a usable launcher; a background stamp with no binstub to build on
        # top of would just repeat the same failure.
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine("[agent-machines] stamp-binstub failed (exit $LASTEXITCODE); skipping background stamp.")
            Exit-SessionStart
        }
        $command = "& `"$payloadInit`" stamp *> `$null"
        $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        Start-Process -FilePath 'conhost.exe' `
            -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc) `
            -WindowStyle Hidden | Out-Null
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
