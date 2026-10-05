$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$forwardArgs = @($args)

$payloadRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_WORKTREES_PAYLOAD_ROOT',
    'Process'
)
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-worktrees] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = Join-Path $env:USERPROFILE '.agent-worktrees' # marketplace-isolation: allow legacy compatibility root

function Get-BootTraceIsoTimestamp {
    return [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:sszzz')
}

function Escape-BootTraceJson([string]$Value) {
    if ($null -eq $Value) { return '' }
    return $Value.Replace('\', '\\').Replace('"', '\"')
}

function Write-BootTraceRecord(
    [string]$Phase,
    [long]$TimestampMs,
    [string]$DispatchPath = ''
) {
    if (-not $runtimeRoot) { return }
    # Never force-create the LEGACY root purely to write a boot-trace
    # record: an ordinary read-only command (e.g. --version) against an
    # already-resolved runtime in legacy-default mode must stay side-
    # effect-free, an invariant this repo tests extensively (Copilot
    # review follow-up, PR #3310 -- distinct from the namespaced-cell
    # case below, where a resolved ACTIVE context is always expected to
    # get its own directory touched regardless). A genuine first-ever
    # legacy install is NOT suppressed by this guard: `$script:willProvision`
    # is set true as soon as self-provisioning is actually committed to
    # (see the call site below), which creates the legacy root eagerly so
    # even the earlier `resolver-loaded`/`shim-start` phases -- emitted
    # before `provision-start`'s own explicit `New-Item` -- are captured
    # instead of silently dropped (Copilot review follow-up, PR #3310).
    if ($runtimeRoot -eq $legacyRoot -and -not (Test-Path -LiteralPath $runtimeRoot)) {
        if (-not $script:willProvision) { return }
        try {
            New-Item -ItemType Directory -Path $runtimeRoot -Force -ErrorAction Stop | Out-Null
        } catch {
            return
        }
    }
    $bootTraceLogPath = Join-Path $runtimeRoot 'logs\activity.jsonl'
    try {
        [IO.Directory]::CreateDirectory((Split-Path -Parent $bootTraceLogPath)) | Out-Null
        $parts = [System.Collections.Generic.List[string]]::new()
        [void]$parts.Add('"ts":"' + (Escape-BootTraceJson (Get-BootTraceIsoTimestamp)) + '"')
        [void]$parts.Add('"event":"boot_trace"')
        [void]$parts.Add('"plugin":"agent-worktrees"')
        [void]$parts.Add('"phase":"' + (Escape-BootTraceJson $Phase) + '"')
        [void]$parts.Add('"t_ms":' + $TimestampMs)
        [void]$parts.Add('"pid":' + $PID)
        $hostName = [Environment]::MachineName
        if ($hostName) {
            [void]$parts.Add('"host":"' + (Escape-BootTraceJson $hostName) + '"')
        }
        [void]$parts.Add('"source":"launcher"')
        if ($DispatchPath) {
            [void]$parts.Add('"path":"' + (Escape-BootTraceJson $DispatchPath) + '"')
        }
        $line = '{' + ($parts -join ',') + '}'
        # See `powershell-shim.tmpl`'s own identical `AppendAllText` comment
        # for the full synchronous-write-latency rationale (Copilot review,
        # PR #3310): a real, bounded tradeoff, never a fire-and-forget
        # guarantee, kept synchronous deliberately rather than backgrounded.
        [IO.File]::AppendAllText(
            $bootTraceLogPath,
            $line + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
        Invoke-BootTraceMaybePrune -LogPath $bootTraceLogPath
    } catch {}
}

# Mirrors agent_worktrees.activity._maybe_prune/_prune's own retention window
# (RETENTION_DAYS=7, _PRUNE_SIZE_BYTES=512*1024) so a launch that emits
# boot_trace lines but never triggers a later Python-side log_event() call
# does not grow activity.jsonl unbounded.
#
# The rewrite itself is NEVER run inline here. This function can run on
# every single launch (including a key/action dispatched from a live
# picker session), and rewriting a multi-megabyte log line-by-line takes
# several seconds -- long enough to freeze that picker between keypresses.
# Instead, once the file is large, this claims the current debounce
# window's marker (see Invoke-ClaimPruneMarker) and hands the actual
# rewrite to a detached, windowless `agent_worktrees activity-prune-worker`
# child -- mirroring the Python-side activity._dispatch_background_prune --
# so the caller never waits on it. Best-effort throughout: a pruning
# failure (or a runtime not yet resolved to dispatch the worker with)
# never affects the caller.
function Invoke-BootTraceMaybePrune([string]$LogPath) {
    try {
        $info = Get-Item -LiteralPath $LogPath -ErrorAction Stop
        if ($info.Length -lt 524288) { return }
    } catch { return }
    if (-not $script:python) { return }  # no runtime yet to dispatch the worker with
    if (-not (Invoke-ClaimPruneMarker -LogPath $LogPath)) { return }
    try {
        Start-Process -FilePath 'conhost.exe' -ArgumentList (@(
            '--headless', "`"$script:python`"", '-I', '-m', 'agent_worktrees',
            'activity-prune-worker', "`"$LogPath`"", '7'
        )) -WindowStyle Hidden -ErrorAction Stop | Out-Null
    } catch {}
}

# Atomically claims *this debounce window's* dispatch slot for $LogPath, so
# a burst of concurrent launches -- all seeing the log large at the same
# time -- dispatches at most one background prune for this window, not one
# per launch. Mirrors the Python-side activity._claim_prune_marker: each
# window gets its own marker file (named by its epoch-hour bucket number),
# claimed with an exclusive create (`CreateNew`, which throws if the file
# already exists). Unlike a single shared marker refreshed in place, there
# is no separate "renew a stale marker" step and therefore no window where
# multiple processes can all believe they renewed the same claim.
function Invoke-ClaimPruneMarker([string]$LogPath) {
    $bucket = [long][Math]::Floor(
        ((Get-Date).ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds / 3600
    )
    $marker = "$LogPath.prune-marker.$bucket"
    try {
        $fs = [IO.File]::Open($marker, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $fs.Dispose()
    } catch {
        return $false
    }
    # Cleans up markers at least 2 whole windows behind $bucket -- never the
    # immediately-preceding one ($bucket - 1). Mirrors the Python-side
    # activity._PRUNE_MARKER_CLEANUP_GRACE_WINDOWS: a caller that read the
    # clock right at the previous window's tail and was then descheduled
    # before its (otherwise instantaneous) exclusive create can still be
    # holding that bucket's claim-in-flight -- deleting it here would let
    # that delayed caller's create succeed a second time once it resumes,
    # dispatching a duplicate worker. Requiring a full extra window's worth
    # of delay between reading the clock and one file-create call makes
    # that race a scheduling pathology, not a realistic occurrence -- same
    # best-effort posture as the rest of this module.
    try {
        $dir = Split-Path -Parent $LogPath
        $leaf = Split-Path -Leaf $LogPath
        $cutoff = $bucket - 1
        Get-ChildItem -LiteralPath $dir -Filter "$leaf.prune-marker.*" -ErrorAction SilentlyContinue |
            Where-Object {
                $siblingBucket = $null
                [long]::TryParse(
                    $_.Name.Substring("$leaf.prune-marker.".Length), [ref]$siblingBucket
                ) -and $siblingBucket -lt $cutoff
            } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    } catch {}
    return $true
}



function Write-BootTrace([string]$Phase, [string]$DispatchPath = '') {
    $timestampMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Write-BootTraceRecord -Phase $Phase -TimestampMs $timestampMs -DispatchPath $DispatchPath
    if (-not $env:COPILOT_EXTENSIONS_BOOT_TRACE) { return }
    $plugin = if ($env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN) {
        $env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN
    } else {
        'agent-worktrees'
    }
    $line = "::boot-trace:: plugin=$plugin phase=$Phase t=$timestampMs"
    if ($DispatchPath) { $line += " path=$DispatchPath" }
    [Console]::Error.WriteLine($line)
}
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-worktrees] installation-context runtime support is unavailable.'
    )
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-worktrees] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$resolutionStatus = 'ready'
$resolutionReason = 'policy-default-false'
$actualMode = 'legacy'
$desiredMode = 'legacy'
$policy = Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'
$policyPresent = (
    (Test-Path -LiteralPath $policy) -or
    $null -ne (Get-Item -LiteralPath $policy -Force -ErrorAction SilentlyContinue)
)
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'status',
    '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-worktrees',
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
        '[agent-worktrees] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-worktrees] installation context returned malformed status.'
    )
    exit 126
}
$resolutionStatus = [string]$resolution.status
$resolutionReason = [string]$resolution.reason
$actualMode = [string]$resolution.actualMode
$desiredMode = [string]$resolution.desiredMode
$activationGeneration = [string]$resolution.activationGeneration
$namespaceGeneration = [string]$resolution.namespaceGeneration
$installGeneration = [string]$resolution.installGeneration
$simplePolicyLegacy = $false
if (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    -not $policyPresent -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    [string]$resolution.policy.state -ceq 'missing' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled -and
    [string]$resolution.policy.reason -ceq 'policy-default-false' -and
    $null -eq $resolution.legacy.tombstone -and
    [string]$resolution.legacy.disposition -ceq 'active'
) {
    $simplePolicyLegacy = $true
}
elseif (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    $resolutionStatus -ceq 'provenance-blocked' -and
    [string]$resolution.policy.state -ceq 'valid' -and
    $resolution.policy.enabled -is [bool] -and
    -not $resolution.policy.enabled -and
    $null -eq $resolution.legacy.tombstone -and
    [string]$resolution.legacy.disposition -ceq 'active'
) {
    try {
        $policyDocument = Get-Content -LiteralPath $policy -Raw | ConvertFrom-Json
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
            '[agent-worktrees] requested installation context is not active.'
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
            "[agent-worktrees] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    if (-not $runtimeRoot -or -not $context) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] active installation context is incomplete.'
        )
        exit 126
    }
}
else {
    [Console]::Error.WriteLine(
        "[agent-worktrees] installation context blocks invocation: " +
        "status=$resolutionStatus reason=$resolutionReason."
    )
    exit 126
}

$validatedNamespaceGeneration = ''
if ($actualMode -ceq 'namespaced') {
    $validationDurableHome = $context
    1..5 | ForEach-Object {
        $validationDurableHome = Split-Path -Parent $validationDurableHome
    }
    $validationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'validate',
        '-Context', $context,
        '-DurableHome', $validationDurableHome,
        '-ExpectedPluginId', 'agent-worktrees',
        '-ExpectedPayloadRoot', $payloadRoot
    )
    $validationJson = @(& $hostExe @validationArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation context validation failed.'
        )
        exit 126
    }
    try {
        $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation context validation was malformed.'
        )
        exit 126
    }
    $validatedNamespaceGeneration = [string]$validatedContext.namespaceGeneration
    if ([string]$validatedContext.generation -cne $installGeneration) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation context generation does not match governance.'
        )
        exit 126
    }
}

function Test-InstallationResolutionCurrent {
    $currentJson = @(& $hostExe @statusArgs)
    if ($LASTEXITCODE -ne 0) { return $false }
    try {
        $current = ($currentJson -join "`n") | ConvertFrom-Json
    } catch {
        return $false
    }
    if (
        [string]$current.status -cne $resolutionStatus -or
        [string]$current.reason -cne $resolutionReason -or
        [string]$current.actualMode -cne $actualMode -or
        [string]$current.desiredMode -cne $desiredMode -or
        [string]$current.activationGeneration -cne $activationGeneration -or
        [string]$current.namespaceGeneration -cne $namespaceGeneration -or
        [string]$current.installGeneration -cne $installGeneration
    ) {
        return $false
    }
    if ($actualMode -ceq 'namespaced') {
        if (
            [string]$current.runtimeRoot -cne $runtimeRoot -or
            [string]$current.context -cne $context
        ) {
            return $false
        }
        $currentValidationJson = @(& $hostExe @validationArgs)
        if ($LASTEXITCODE -ne 0) { return $false }
        try {
            $currentValidated = (
                $currentValidationJson -join "`n"
            ) | ConvertFrom-Json
        } catch {
            return $false
        }
        return (
            [string]$currentValidated.namespaceGeneration -ceq
                $validatedNamespaceGeneration -and
            [string]$currentValidated.generation -ceq $installGeneration
        )
    }
    return $true
}

function Resolve-AgentWorktreesRuntime {
    $AgentRtPy = $null
    if ($env:COPILOT_EXTENSIONS_BOOT_TRACE) {
        $env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN = 'agent-worktrees'
    }
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver
    return $AgentRtPy
}

function Invoke-AgentWorktreesRuntime([string]$Python) {
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
    & $Python -m agent_worktrees @forwardArgs
    exit $LASTEXITCODE
}

$python = Resolve-AgentWorktreesRuntime
# Commit to self-provisioning (and, in doing so, permit the boot-trace
# writer to eagerly create a not-yet-existing legacy root) as soon as we
# actually know provisioning will happen -- i.e. no runtime resolved AND
# self-provisioning isn't disabled -- so the traces below, which fire
# strictly before `provision-start`'s own `New-Item`, are captured on a
# genuine first-ever launch instead of silently dropped (Copilot review
# follow-up, PR #3310).
$script:willProvision = (-not $python) -and (-not $env:AGENT_WORKTREES_NO_SELFPROVISION)
Write-BootTrace 'resolver-loaded'
# Log the outer dispatcher template's own 'shim-start' phase here, now
# that $runtimeRoot reflects whichever root (legacy or an active
# namespaced context) is genuinely active -- the outer template forwards
# its own timestamp via this env var but never writes the durable record
# itself, precisely because it cannot know which root is correct
# (Copilot review, PR #3310; see the outer dispatcher-powershell.tmpl's
# own comment for the full rationale).
if ($env:COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS) {
    Write-BootTraceRecord -Phase 'shim-start' -TimestampMs ([long]$env:COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS)
}
if ($python) {
    Write-BootTrace 'dispatch' 'fast'
    Invoke-AgentWorktreesRuntime $python
}
if ($env:AGENT_WORKTREES_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-worktrees] runtime not provisioned ' +
        '(AGENT_WORKTREES_NO_SELFPROVISION set).'
    )
    exit 1
}

[Console]::Error.WriteLine(
    '[agent-worktrees] runtime not provisioned -- provisioning from the owning payload.'
)
[Console]::Error.WriteLine(
    '::agent-provisioning:: plugin=agent-worktrees eta_seconds=120 reason=first-use'
)
Write-BootTrace 'provision-start'
if (-not (Test-Path -LiteralPath $runtimeRoot)) {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
}
$lockPath = Join-Path $runtimeRoot '.provision.lock'
$lock = $null
while (-not $lock) {
    try {
        $lock = [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        Start-Sleep -Milliseconds 200
    }
}

try {
    if (-not (Test-InstallationResolutionCurrent)) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation governance changed while waiting; retry.'
        )
        exit 126
    }
    $python = Resolve-AgentWorktreesRuntime
    Write-BootTrace 'resolver-loaded'
    if ($python) {
        Write-BootTrace 'dispatch' 'locked-fast'
        Invoke-AgentWorktreesRuntime $python
    }

    if ($actualMode -ceq 'namespaced') {
        if (
            $resolutionStatus -cne 'ready' -or
            $resolutionReason -cne 'namespaced-active'
        ) {
            [Console]::Error.WriteLine(
                '[agent-worktrees] deactivation-pending installation cannot ' +
                'provision a new runtime.'
            )
            exit 126
        }
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer `
            install -InstallDir $runtimeRoot 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $provisionStatus = $LASTEXITCODE
        if ($provisionStatus -ne 0) { exit $provisionStatus }
    } else {
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer stamp 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $snapshot = ''
        try {
            $snapshot = ([IO.File]::ReadAllText(
                (Join-Path $legacyRoot 'payload-dir')
            )).Trim()
        } catch {}
        $snapshotInstaller = if ($snapshot) {
            Join-Path $snapshot 'scripts\install.ps1'
        } else {
            ''
        }
        if (
            -not $snapshotInstaller -or
            -not (Test-Path -LiteralPath $snapshotInstaller -PathType Leaf)
        ) {
            [Console]::Error.WriteLine(
                "[agent-worktrees] stamped snapshot installer not found: " +
                $snapshotInstaller
            )
            exit 127
        }
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $snapshotInstaller `
            provision 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $provisionStatus = $LASTEXITCODE
        if ($provisionStatus -ne 0) { exit $provisionStatus }
    }
    Write-BootTrace 'provision-end'

    if (-not (Test-InstallationResolutionCurrent)) {
        [Console]::Error.WriteLine(
            '[agent-worktrees] installation governance changed during provisioning.'
        )
        exit 126
    }
    $python = Resolve-AgentWorktreesRuntime
    Write-BootTrace 'resolver-loaded'
    if ($python) {
        Write-BootTrace 'dispatch' 'provisioned'
        Invoke-AgentWorktreesRuntime $python
    }
} finally {
    if ($lock) { $lock.Dispose() }
}
[Console]::Error.WriteLine(
    '[agent-worktrees] provisioning completed without a resolvable runtime.'
)
exit 1
