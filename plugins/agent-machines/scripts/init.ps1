<#
.SYNOPSIS
    Bootstrap the agent-machines runtime. PS5+ compatible.

.DESCRIPTION
    Creates the shared runtime at ~/.agent-machines/ -- a venv with the
    agent_machines package installed (via uv pip install) -- and deploys the
    `agent-machines` binstub into ~/.local/bin.

    Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-machines).

.PARAMETER Force
    Re-create the venv even if it already exists.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'init', 'stamp', 'provision', 'cell-provision', 'slot-provision', 'slot-validate', 'slot-complete', 'slot-completion-validate', 'slot-cutover')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$DurableHome,
    [string]$OriginPayloadRoot,
    [string]$ExpectedNamespaceGeneration,
    [string]$ExpectedInstallGeneration,
    [string]$ExpectedCurrentVersion,
    [switch]$ExpectCurrentAbsent,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$CellMode = $false

if ($InstallDir) {
    $InstallDir = [IO.Path]::GetFullPath($InstallDir)
    $PSBoundParameters['InstallDir'] = $InstallDir
}

# Refuse every legacy mutation before self-staging creates or removes files.
$probePayload = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
    [IO.Path]::GetFullPath($env:COPILOT_PLUGIN_STAGED_FROM)
} else {
    (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$probeHost = (Get-Process -Id $PID).Path
if ($Action -notin @(
    'cell-provision',
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate',
    'slot-cutover'
)) {
    $probeLegacyRoot = if ($InstallDir) {
        [IO.Path]::GetFullPath($InstallDir)
    } else {
        Join-Path $env:USERPROFILE '.agent-machines'
    }
    $legacyProbe = Join-Path $PSScriptRoot 'installation-context\legacy-entrypoint-probe.ps1'
    if (-not (Test-Path -LiteralPath $legacyProbe -PathType Leaf)) {
        Write-Host '  [FAIL] Legacy mutation probe is unavailable' -ForegroundColor Red
        exit 1
    }
    & $probeHost -NoProfile -ExecutionPolicy Bypass -File $legacyProbe `
        -PayloadRoot $probePayload -LegacyRoot $probeLegacyRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# The dependency-light cell-slot runner does not need the legacy installer's
# payload self-stage, whose staging root is itself legacy state.
$cellSlotAction = $Action -in @(
    'cell-provision',
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate',
    'slot-cutover'
)
if ($cellSlotAction) {
    Set-Location -LiteralPath $env:USERPROFILE
    [IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
}
$cellSlotDirect = $cellSlotAction -and -not $env:COPILOT_PLUGIN_INSTALL_STAGED
if ($cellSlotDirect) {
    $env:COPILOT_PLUGIN_INSTALL_STAGED = 'cell-slot-action'
}

# === install-contract:v4 self-stage -- keep byte-identical across plugins ===
# dotfiles #935: a plugin installer reads its own payload (src/, libs/,
# pyproject.toml) to build the venv, so while it runs -- especially if it wedges
# or times out -- it holds the SINGLETON `installed-plugins/<mkt>/<plugin>`
# payload dir open (CWD/handles). A concurrent `copilot plugin update <plugin>`
# then fails on Windows with os error 32 ("used by another process"): the payload
# freezes at the old version and reconcile keeps reverting the runtime toward it
# (the version-drift saga). Fix: when running from the marketplace payload, copy
# the WHOLE payload into a UNIQUE per-invocation staging dir OUTSIDE the payload
# and re-exec from there, so the singleton is touched only for the fast copy. A
# stalled run then holds only its own throwaway stage dir, never blocking the
# next invocation or a `copilot plugin update`. COPILOT_PLUGIN_STAGED_FROM tells
# Get-SourceKind the payload was really the marketplace (see below). Env-guarded
# against re-exec loops; the stage-dir path (not under installed-plugins) is a
# second guard. Best-effort, non-blocking reap of old stage dirs.
if (-not $env:COPILOT_PLUGIN_INSTALL_STAGED) {
    try {
        $__selfStageScriptDir = $PSScriptRoot
        $__selfStagePayload = (Resolve-Path (Join-Path $__selfStageScriptDir '..')).Path
        if (($__selfStagePayload -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
            $__selfStageName = (Get-Content (Join-Path $__selfStagePayload 'plugin.json') -Raw | ConvertFrom-Json).name
            if ($__selfStageName) {
                # CWD guard (#1366): the sessionStart hook launches this installer
                # with CWD = the SINGLETON payload dir, so our process CWD is an
                # open directory handle that blocks `copilot plugin update` (os
                # error 32) for our whole lifetime -- including the watchdog
                # WaitForExit below and, on a self-stage failure, an in-place run.
                # Self-stage relocates our FILE reads but NOT the CWD handle, so
                # re-root the process CWD OFF the payload BEFORE the copy (absolute
                # paths make this safe). Set the WIN32 cwd (the real dir handle),
                # not just the PS provider location.
                try {
                    Set-Location -LiteralPath $env:USERPROFILE
                    [System.IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
                } catch {}
                $__selfStageRoot = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") '.install-stage'
                $__selfStageDir = Join-Path $__selfStageRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff') + "-$PID")
                New-Item -ItemType Directory -Force -Path $__selfStageDir | Out-Null
                Copy-Item -LiteralPath $__selfStagePayload -Destination $__selfStageDir -Recurse -Force
                $__selfStagedPayload = Join-Path $__selfStageDir (Split-Path -Leaf $__selfStagePayload)
                $__selfStagedEntry = Join-Path (Join-Path $__selfStagedPayload 'scripts') (Split-Path -Leaf $PSCommandPath)
                # Best-effort reap of prior stage dirs; NEVER touch a live one.
                # Only remove a sibling whose owner pid (the <ts>-<pid> suffix) is
                # DEAD -- so a concurrent or wedged installer's dir is left alone
                # (it uses its own unique dir), honoring "a stalled install must
                # never block another copy". Dead leftovers are cleaned up.
                Get-ChildItem $__selfStageRoot -Directory -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.FullName -ne $__selfStageDir } |
                    ForEach-Object {
                        $__selfStageOwnerPid = 0
                        if ($_.Name -match '-(\d+)$') { [void][int]::TryParse($Matches[1], [ref]$__selfStageOwnerPid) }
                        $__selfStageOwnerAlive = $false
                        if ($__selfStageOwnerPid -gt 0) {
                            $__selfStageOwnerAlive = [bool](Get-Process -Id $__selfStageOwnerPid -ErrorAction SilentlyContinue)
                        }
                        if (-not $__selfStageOwnerAlive) {
                            try { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop } catch {}
                        }
                    }
                # Faithful arg forwarding, independent of this script's param()
                # shape AND of the invocation form. Rebuild the child arg list from
                # $PSBoundParameters (a switch as a bare -Name, else -Name Value), so
                # the staged re-exec carries the SAME action/flags whether the
                # installer was launched via `pwsh -File install.ps1 update` OR the
                # call/`-Command` form `.\install.ps1 update` (the documented
                # interactive form). The old approach -- slicing args after `-File`
                # out of GetCommandLineArgs() -- returned NOTHING for the call form,
                # so the staged child re-ran with the DEFAULT action: a silent no-op
                # that still reported success (#205). All installer args are declared
                # params, so nothing is unbound -- and $args is unavailable in a
                # param()-script under StrictMode, so it is deliberately not consulted.
                $__selfStageFwd = @()
                foreach ($__selfStageK in $PSBoundParameters.Keys) {
                    $__selfStageV = $PSBoundParameters[$__selfStageK]
                    if ($__selfStageV -is [System.Management.Automation.SwitchParameter]) {
                        if ($__selfStageV.IsPresent) { $__selfStageFwd += "-$__selfStageK" }
                    } else {
                        $__selfStageFwd += "-$__selfStageK"
                        $__selfStageFwd += [string]$__selfStageV
                    }
                }
                $env:COPILOT_PLUGIN_INSTALL_STAGED = '1'
                $env:COPILOT_PLUGIN_STAGED_FROM = $__selfStagePayload
                $__selfStageExe = (Get-Process -Id $PID).Path
                # WATCHDOG (#935): the staging parent is already outside the
                # payload and wraps the child's whole lifetime, so it doubles as
                # a watchdog -- launch the staged child, then enforce a deadline.
                # A stalled install (the (4) session-start-hook failure class)
                # self-terminates instead of leaking forever: kill the WHOLE tree
                # (taskkill /T -- Windows' subprocess kill leaves grandchildren)
                # and log. The killed child's stage dir has a dead owner pid, so
                # the next run's pid-guarded reap cleans it; its half-built slot
                # has no completion marker, so it is tossed + rebuilt (retry).
                # Deadline: <NAME>_INSTALL_DEADLINE_SEC, else
                # COPILOT_PLUGIN_INSTALL_DEADLINE_SEC, else 480s; <=0 disables.
                $__wdDeadline = 480
                $__wdEnvVar = (($__selfStageName -replace '[^A-Za-z0-9]+', '_').ToUpper()) + '_INSTALL_DEADLINE_SEC'
                $__wdRaw = [Environment]::GetEnvironmentVariable($__wdEnvVar)
                if (-not $__wdRaw) { $__wdRaw = $env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC }
                if ($__wdRaw) { [void][int]::TryParse([string]$__wdRaw, [ref]$__wdDeadline) }
                $__wdChild = Start-Process -FilePath $__selfStageExe -PassThru -NoNewWindow `
                    -WorkingDirectory $__selfStagedPayload `
                    -ArgumentList (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $__selfStagedEntry) + $__selfStageFwd)
                if ($__wdDeadline -gt 0 -and -not $__wdChild.WaitForExit($__wdDeadline * 1000)) {
                    try { & taskkill.exe /PID $__wdChild.Id /T /F 2>&1 | Out-Null } catch {}
                    try { Stop-Process -Id $__wdChild.Id -Force -ErrorAction SilentlyContinue } catch {}
                    $__wdLog = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") 'reconcile.err.log'
                    try {
                        Add-Content -LiteralPath $__wdLog -Value ("[{0}] WATCHDOG-KILL {1}: install exceeded {2}s deadline (child pid {3}); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: {4}" -f ((Get-Date).ToUniversalTime().ToString('s') + 'Z'), $__selfStageName, $__wdDeadline, $__wdChild.Id, $__selfStageDir)
                    } catch {}
                    exit 124
                }
                $__wdChild.WaitForExit()
                exit $__wdChild.ExitCode
            }
        }
    } catch {
        Write-Host "  [WARN] self-stage failed, running in place: $_" -ForegroundColor Yellow
    }
}
# === end install-contract:v4 self-stage ===
if ($cellSlotDirect) { Remove-Item Env:COPILOT_PLUGIN_INSTALL_STAGED }

# === install-contract:v4 smoke seam (test-only) -- keep byte-identical ===
# #935 install-flow test hook. When COPILOT_PLUGIN_INSTALL_SMOKE is set, prove
# the self-stage/lock behavior WITHOUT a heavy venv build: this (post-stage)
# process records where it is running from + the recorded marketplace origin,
# then sleeps to simulate a slow/wedged install so a test can assert the
# SINGLETON payload dir stays replaceable meanwhile. Never set in production.
if ($env:COPILOT_PLUGIN_INSTALL_SMOKE) {
    try {
        $__smokePayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
        $__smokeName = (Get-Content (Join-Path $__smokePayload 'plugin.json') -Raw | ConvertFrom-Json).name
        $__smokeHome = Join-Path $env:USERPROFILE ".$__smokeName"
        New-Item -ItemType Directory -Force -Path $__smokeHome | Out-Null
        $__smokeSleep = 6
        [void][int]::TryParse([string]$env:COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP, [ref]$__smokeSleep)
        # Optionally spawn a GRANDCHILD sleeper so a watchdog test can prove the
        # WHOLE tree is killed (Windows subprocess-kill leaves grandchildren).
        $__smokeGrandPid = 0
        if ($env:COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD) {
            try {
                $__g = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -WindowStyle Hidden `
                    -ArgumentList @('-NoProfile', '-Command', "Start-Sleep -Seconds $([Math]::Max($__smokeSleep, 3600))")
                $__smokeGrandPid = $__g.Id
            } catch {}
        }
        ([ordered]@{
            ran_from     = $PSScriptRoot
            staged_from  = [string]$env:COPILOT_PLUGIN_STAGED_FROM
            staged       = [bool]$env:COPILOT_PLUGIN_INSTALL_STAGED
            child_pid    = $PID
            grandchild_pid = $__smokeGrandPid
        } | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $__smokeHome 'smoke.json')
        Start-Sleep -Seconds $__smokeSleep
    } catch {}
    exit 0
}
# === end install-contract:v4 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }


# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

function Get-CellDeployManifest {
    param(
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$ContextPath,
        [Parameter(Mandatory)][string]$MarketplaceId
    )
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        if (
            (Test-Path -LiteralPath $ManifestPath) -or
            $null -ne (
                Get-Item -LiteralPath $ManifestPath -Force -ErrorAction SilentlyContinue
            )
        ) {
            throw 'Cell deploy manifest must be an ordinary file'
        }
        return $null
    }
    if (
        (Get-Item -LiteralPath $ManifestPath -Force).Attributes -band
        [IO.FileAttributes]::ReparsePoint
    ) {
        throw 'Cell deploy manifest must be an ordinary file'
    }
    try {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw |
            ConvertFrom-Json
    } catch {
        throw 'Cell deploy manifest is malformed'
    }
    if (
        (
            $manifest.schema_version -isnot [int] -and
            $manifest.schema_version -isnot [long]
        ) -or
        [long]$manifest.schema_version -ne 4 -or
        [string]$manifest.service -cne 'agent-machines' -or
        [string]$manifest.installation.marketplaceId -cne $MarketplaceId -or
        [string]$manifest.installation.pluginId -cne 'agent-machines' -or
        [string]$manifest.installation.context -cne ($ContextPath -replace '\\', '/') -or
        [string]$manifest.source.repo -cne 'copilot-extensions' -or
        [string]$manifest.source.plugin -cne 'agent-machines' -or
        [string]::IsNullOrWhiteSpace([string]$manifest.source.kind) -or
        [string]::IsNullOrWhiteSpace([string]$manifest.source.path) -or
        [string]::IsNullOrWhiteSpace([string]$manifest.source.version) -or
        (
            $null -ne $manifest.source.commit -and
            $manifest.source.commit -isnot [string]
        ) -or
        (
            $null -ne $manifest.source.branch -and
            $manifest.source.branch -isnot [string]
        ) -or
        $manifest.source.dirty -isnot [bool] -or
        [string]$manifest.runtime.kind -cne 'python' -or
        [string]::IsNullOrWhiteSpace([string]$manifest.runtime.version) -or
        [string]::IsNullOrWhiteSpace([string]$manifest.runtime.path) -or
        [string]::IsNullOrWhiteSpace([string]$manifest.runtime.interpreter) -or
        [string]::IsNullOrWhiteSpace([string]$manifest.runtime.selectedBy.kind) -or
        [string]::IsNullOrWhiteSpace([string]$manifest.runtime.selectedBy.path) -or
        [string]$manifest.runtime.selectedBy.version -cne
            [string]$manifest.runtime.version
    ) {
        throw 'Cell deploy manifest identity or source provenance is invalid'
    }
    $pluginRoot = Split-Path -Parent $ManifestPath
    $expectedRuntime = Join-Path (
        Join-Path $pluginRoot 'versions'
    ) ([string]$manifest.runtime.version)
    $expectedInterpreter = if ($env:OS -eq 'Windows_NT') {
        Join-Path $expectedRuntime 'Scripts\python.exe'
    } else {
        Join-Path $expectedRuntime 'bin/python'
    }
    $pathComparer = if ($env:OS -eq 'Windows_NT') {
        [StringComparer]::OrdinalIgnoreCase
    } else {
        [StringComparer]::Ordinal
    }
    try {
        $runtimePathValue = [string]$manifest.runtime.path
        $runtimeInterpreterValue = [string]$manifest.runtime.interpreter
        if ($env:OS -eq 'Windows_NT') {
            $runtimePathValue = $runtimePathValue -replace '/', '\'
            $runtimeInterpreterValue = $runtimeInterpreterValue -replace '/', '\'
        }
        $runtimePath = [IO.Path]::GetFullPath(
            $runtimePathValue
        )
        $runtimeInterpreter = [IO.Path]::GetFullPath(
            $runtimeInterpreterValue
        )
    } catch {
        throw 'Cell deploy manifest runtime paths are invalid'
    }
    if (
        -not $pathComparer.Equals(
            $runtimePath,
            [IO.Path]::GetFullPath($expectedRuntime)
        ) -or
        -not $pathComparer.Equals(
            $runtimeInterpreter,
            [IO.Path]::GetFullPath($expectedInterpreter)
        )
    ) {
        throw 'Cell deploy manifest runtime selection escapes its installation'
    }
    return $manifest
}

function Write-CellDeployManifest {
    param(
        [Parameter(Mandatory)][string]$PluginRoot,
        [Parameter(Mandatory)][string]$SourcePluginDir,
        [Parameter(Mandatory)][string]$SourceVersion,
        [Parameter(Mandatory)][string]$RuntimeSlot,
        [Parameter(Mandatory)][string]$RuntimeVersion,
        [Parameter(Mandatory)][string]$ContextPath,
        [Parameter(Mandatory)][string]$MarketplaceId,
        [switch]$PreserveSource
    )
    $selectedSourcePath = $SourcePluginDir
    $selectedSourceVersion = $SourceVersion
    $sourcePath = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
        $env:COPILOT_PLUGIN_STAGED_FROM
    } else {
        $SourcePluginDir
    }
    $kind = if (($sourcePath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        'marketplace'
    } else {
        'local'
    }
    $commit = $null
    $branch = $null
    $dirty = $false
    $manifestPath = Join-Path $PluginRoot 'deploy-manifest.json'
    $existing = if ($PreserveSource) {
        Get-CellDeployManifest `
            -ManifestPath $manifestPath `
            -ContextPath $ContextPath `
            -MarketplaceId $MarketplaceId
    } else {
        $null
    }
    if ($null -ne $existing) {
        $kind = [string]$existing.source.kind
        $SourcePluginDir = [string]$existing.source.path
        $SourceVersion = [string]$existing.source.version
        $commit = $existing.source.commit
        $branch = $existing.source.branch
        $dirty = [bool]$existing.source.dirty
    }
    elseif ($kind -eq 'local') {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $SourcePluginDir)
        try {
            $commit = git -C $repoRoot rev-parse --short HEAD 2>$null
            $branch = git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null
            $dirty = [bool](git -C $repoRoot status --porcelain 2>$null)
        } catch {
            $commit = 'unknown'
            $branch = 'unknown'
            $dirty = $false
        }
    }
    $runtimeInterpreter = if ($env:OS -eq 'Windows_NT') {
        (Join-Path $RuntimeSlot 'Scripts\python.exe') -replace '\\', '/'
    } else {
        (Join-Path $RuntimeSlot 'bin/python') -replace '\\', '/'
    }
    $selectedKind = if (
        ($selectedSourcePath -replace '\\', '/') -match
        '/\.copilot/installed-plugins/'
    ) {
        'marketplace'
    } else {
        'local'
    }
    $manifest = [ordered]@{
        schema_version = 4
        service = 'agent-machines'
        deployed_at = (Get-Date -Format 'o')
        deployed_by = "$([Environment]::MachineName.ToLowerInvariant())-$(
            if ($env:OS -eq 'Windows_NT') { 'windows' } else { 'posix' }
        )"
        source = [ordered]@{
            kind = $kind
            path = ($SourcePluginDir -replace '\\', '/')
            repo = 'copilot-extensions'
            plugin = 'agent-machines'
            version = $SourceVersion
            commit = $commit
            branch = $branch
            dirty = $dirty
        }
        runtime = [ordered]@{
            kind = 'python'
            version = $RuntimeVersion
            path = ($RuntimeSlot -replace '\\', '/')
            interpreter = $runtimeInterpreter
            selectedBy = [ordered]@{
                kind = $selectedKind
                path = ($selectedSourcePath -replace '\\', '/')
                version = $selectedSourceVersion
            }
        }
        installation = [ordered]@{
            marketplaceId = $MarketplaceId
            pluginId = 'agent-machines'
            context = ($ContextPath -replace '\\', '/')
        }
    }
    $tmp = "$manifestPath.tmp.$PID"
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllText(
        $tmp,
        (($manifest | ConvertTo-Json -Depth 6) + [Environment]::NewLine),
        $utf8
    )
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        $backup = "$manifestPath.backup.$PID"
        [IO.File]::Replace($tmp, $manifestPath, $backup)
        Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    } else {
        [IO.File]::Move($tmp, $manifestPath)
    }
}

function Get-CellSnapshotOwnerText {
    return (
        @(
            'copilot-extensions.agent-machines.snapshot-publish:v1'
            "marketplaceId=$ExpectedMarketplaceId"
            'pluginId=agent-machines'
            "snapshotId=$SrcVersion"
        ) -join "`n"
    ) + "`n"
}

function Test-OwnedCellSnapshot {
    param([Parameter(Mandatory)][string]$Root)
    $marker = Join-Path $Root '.agent-machines-snapshot-publish-owner'
    if (
        -not (Test-Path -LiteralPath $Root -PathType Container) -or
        ((Get-Item -LiteralPath $Root -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint) -or
        -not (Test-Path -LiteralPath $marker -PathType Leaf) -or
        ((Get-Item -LiteralPath $marker -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint)
    ) {
        return $false
    }
    $actual = [IO.File]::ReadAllText($marker).Replace("`r`n", "`n").TrimEnd("`n")
    $expected = (Get-CellSnapshotOwnerText).Replace("`r`n", "`n").TrimEnd("`n")
    return $actual -ceq $expected
}

function Remove-OwnedCellSnapshot {
    param([Parameter(Mandatory)][string]$Root)
    if (-not (Test-OwnedCellSnapshot -Root $Root)) { return $false }
    Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction Stop
    return $true
}

function Ensure-CellSnapshot {
    param([Parameter(Mandatory)][string]$SnapshotRoot)
    $ownerMarkerName = '.agent-machines-snapshot-publish-owner'
    $ownerMarker = Join-Path $SnapshotRoot $ownerMarkerName
    $provenance = Join-Path $SnapshotRoot 'snapshot-provenance.json'
    if (
        (Test-Path -LiteralPath $SnapshotRoot) -or
        $null -ne (Get-Item -LiteralPath $SnapshotRoot -Force -ErrorAction SilentlyContinue)
    ) {
        if (
            -not (Test-Path -LiteralPath $provenance) -and
            $null -eq (Get-Item -LiteralPath $provenance -Force -ErrorAction SilentlyContinue) -and
            (Test-OwnedCellSnapshot -Root $SnapshotRoot)
        ) {
            if (-not (Remove-OwnedCellSnapshot -Root $SnapshotRoot)) {
                throw 'Cannot recover the owned incomplete cell snapshot'
            }
        } else {
            & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
                snapshot-validate `
                -Context $Context `
                -ExpectedMarketplaceId $ExpectedMarketplaceId `
                -ExpectedPluginId agent-machines `
                -SnapshotId $SrcVersion `
                -DurableHome $DurableHome | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw 'Existing cell snapshot provenance validation failed'
            }
            if (Test-OwnedCellSnapshot -Root $SnapshotRoot) {
                Remove-Item -LiteralPath $ownerMarker -Force -ErrorAction Stop
            }
            return
        }
    }

    if (-not (Test-Path -LiteralPath $snapshotsRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $snapshotsRoot -Force | Out-Null
    }
    $payloadOwnerMarker = Join-Path $PluginDir $ownerMarkerName
    if (
        (Test-Path -LiteralPath $payloadOwnerMarker) -or
        $null -ne (
            Get-Item -LiteralPath $payloadOwnerMarker -Force -ErrorAction SilentlyContinue
        )
    ) {
        throw 'Payload uses the reserved cell snapshot publication marker'
    }
    $stage = Join-Path $snapshotsRoot (
        ".agent-machines-snapshot-$SrcVersion-$PID-$([Guid]::NewGuid().ToString('N'))"
    )
    New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
    $stageMarker = Join-Path $stage $ownerMarkerName
    $utf8NoBomLocal = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllText(
        $stageMarker,
        (Get-CellSnapshotOwnerText),
        $utf8NoBomLocal
    )
    try {
        Get-ChildItem -LiteralPath $PluginDir -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName `
                -Destination (Join-Path $stage $_.Name) `
                -Recurse -Force -ErrorAction Stop
        }
    } catch {
        [void](Remove-OwnedCellSnapshot -Root $stage)
        throw 'Cannot copy the payload into the cell snapshot staging directory'
    }
    if (
        (Test-Path -LiteralPath $SnapshotRoot) -or
        $null -ne (Get-Item -LiteralPath $SnapshotRoot -Force -ErrorAction SilentlyContinue)
    ) {
        [void](Remove-OwnedCellSnapshot -Root $stage)
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
            snapshot-validate `
            -Context $Context `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -ExpectedPluginId agent-machines `
            -SnapshotId $SrcVersion `
            -DurableHome $DurableHome | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw 'Concurrent cell snapshot publication is invalid'
        }
        return
    }
    try {
        [IO.Directory]::Move($stage, $SnapshotRoot)
    } catch {
        if (Test-Path -LiteralPath $stage -PathType Container) {
            [void](Remove-OwnedCellSnapshot -Root $stage)
        }
        throw 'Cannot atomically publish the staged cell snapshot'
    }

    # Test-only interruption seam: production never sets this variable.
    if ($env:AGENT_MACHINES_CELL_SNAPSHOT_FAIL_BEFORE_STAMP) {
        [void](Remove-OwnedCellSnapshot -Root $SnapshotRoot)
        throw 'Injected failure before cell snapshot provenance publication'
    }
    & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
        snapshot-stamp `
        -Context $Context `
        -ExpectedMarketplaceId $ExpectedMarketplaceId `
        -ExpectedPluginId agent-machines `
        -ExpectedNamespaceGeneration $cellNamespaceGeneration `
        -ExpectedInstallGeneration $cellInstallGeneration `
        -SnapshotId $SrcVersion `
        -DurableHome $DurableHome | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if (
            -not (Test-Path -LiteralPath $provenance) -and
            $null -eq (
                Get-Item -LiteralPath $provenance -Force -ErrorAction SilentlyContinue
            )
        ) {
            [void](Remove-OwnedCellSnapshot -Root $SnapshotRoot)
        }
        throw 'Cell snapshot provenance publication failed'
    }
    & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
        snapshot-validate `
        -Context $Context `
        -ExpectedMarketplaceId $ExpectedMarketplaceId `
        -ExpectedPluginId agent-machines `
        -SnapshotId $SrcVersion `
        -DurableHome $DurableHome | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Published cell snapshot provenance validation failed'
    }
    if (-not (Test-OwnedCellSnapshot -Root $SnapshotRoot)) {
        throw 'Cell snapshot publication ownership marker changed'
    }
    Remove-Item -LiteralPath $ownerMarker -Force -ErrorAction Stop
}

# -- Paths --------------------------------------------------------------

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_machines'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-machines'
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}

# === install-contract:v3 versioned-venv -- keep byte-identical across plugins ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and publish the active one via the `<root>/current-version` plain-text marker.
# On Windows there is NO junction at all -- a reparse point was blocked by
# RedirectionGuard (WinError 448) on managed devices -- so the version-pinned
# binstub + deploy-manifest resolve the active slot straight from the marker. On
# POSIX a `.venv` symlink (not a reparse point) still publishes the active slot,
# but the marker is authoritative. A version bump builds a new slot beside the old
# one and republishes the marker (never mutates a live venv). The
# COPILOT_EXT_NO_VERSIONED opt-out is fully retired -- always versioned.
# scripts/versioned_runtime.py owns the marker publish + migration.
$LinkDir = $VenvDir                       # stable path the binstub/manifest reference
$LinkPython = $VenvPython
$VersionedRuntime = $true  # always versioned (junction-free marker model; COPILOT_EXT_NO_VERSIONED retired)
$SrcVersion = $null
$pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
if (Test-Path $pyprojForVer) {
    $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
    if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
}
if ($SrcVersion) {
    $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    if ($env:OS -eq 'Windows_NT') { $VenvPython = Join-Path $VenvDir 'Scripts\python.exe' }
    else { $VenvPython = Join-Path $VenvDir 'bin/python' }
    $LinkDir = $VenvDir
    $LinkPython = $VenvPython
} else {
    $VersionedRuntime = $false
}
# === end install-contract:v3 versioned-venv ===

if (
    $Action -in @('cell-provision', 'slot-cutover') -and
    -not $env:AGENT_MACHINES_CELL_PROVISION_LOCK_HELD
) {
    if ([string]::IsNullOrWhiteSpace($Context)) {
        Write-Fail "$Action requires -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization"
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedMarketplaceId)) {
        Write-Fail "$Action requires -ExpectedMarketplaceId"
        exit 2
    }
    $lockRunner = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path -LiteralPath $lockRunner -PathType Leaf)) {
        Write-Fail 'Installation-context runner is unavailable'
        exit 1
    }
    $lockDurableHome = $DurableHome
    if (-not $lockDurableHome) {
        $lockDurableHome = $Context
        1..5 | ForEach-Object {
            $lockDurableHome = Split-Path -Parent $lockDurableHome
        }
    }
    $lockValidatedJson = @(
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $lockRunner `
            validate `
            -Context $Context `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -ExpectedPluginId agent-machines `
            -DurableHome $lockDurableHome
    )
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "$Action context receipt validation failed before provisioning lock"
        exit 1
    }
    try {
        $lockValidated = ($lockValidatedJson -join "`n") | ConvertFrom-Json
    } catch {
        Write-Fail "$Action received malformed context validation before provisioning lock"
        exit 1
    }
    $lockPluginRoot = [string]$lockValidated.pluginRoot
    if (
        -not $lockPluginRoot -or
        -not (Test-Path -LiteralPath $lockPluginRoot -PathType Container)
    ) {
        Write-Fail "$Action context receipt did not resolve a plugin root"
        exit 1
    }
    $lockPath = Join-Path $lockPluginRoot '.payload-provision.lock'
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
    $lockForward = @()
    foreach ($key in $PSBoundParameters.Keys) {
        $value = $PSBoundParameters[$key]
        if ($value -is [Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) { $lockForward += "-$key" }
        } else {
            $lockForward += "-$key"
            $lockForward += [string]$value
        }
    }
    $priorLockMarker = $env:AGENT_MACHINES_CELL_PROVISION_LOCK_HELD
    try {
        $env:AGENT_MACHINES_CELL_PROVISION_LOCK_HELD = '1'
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath @lockForward
        $lockedStatus = $LASTEXITCODE
    } finally {
        if ($null -eq $priorLockMarker) {
            Remove-Item Env:AGENT_MACHINES_CELL_PROVISION_LOCK_HELD -ErrorAction SilentlyContinue
        } else {
            $env:AGENT_MACHINES_CELL_PROVISION_LOCK_HELD = $priorLockMarker
        }
        $lock.Dispose()
    }
    exit $lockedStatus
}

# Test-only witness: the parent lock wrapper remains alive while this child
# represents the complete cell transaction. Concurrent tests assert that these
# start/end pairs never overlap. Production never sets this variable.
if (
    $Action -eq 'cell-provision' -and
    $env:AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE
) {
    Add-Content -LiteralPath $env:AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE `
        -Value "start $PID"
    $smokeDelay = 1000
    if ($env:AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE_MILLISECONDS) {
        [void][int]::TryParse(
            $env:AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE_MILLISECONDS,
            [ref]$smokeDelay
        )
    }
    Start-Sleep -Milliseconds ([Math]::Max(0, $smokeDelay))
    Add-Content -LiteralPath $env:AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE `
        -Value "end $PID"
    exit 0
}

if ($Action -in @(
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate'
)) {
    if ([string]::IsNullOrWhiteSpace($Context)) {
        Write-Fail "$Action requires -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization"
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedMarketplaceId)) {
        Write-Fail "$Action requires -ExpectedMarketplaceId"
        exit 2
    }
    if (-not $SrcVersion) {
        Write-Fail 'Cannot determine plugin version from pyproject.toml'
        exit 1
    }
    $slotRunner = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path -LiteralPath $slotRunner -PathType Leaf)) {
        Write-Fail 'Installation-context runner is unavailable'
        exit 1
    }
    $slotArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $slotRunner,
        $Action,
        '-Context', $Context,
        '-ExpectedMarketplaceId', $ExpectedMarketplaceId,
        '-ExpectedPluginId', 'agent-machines',
        '-ExpectedPayloadRoot', $PluginDir,
        '-ExpectedPayloadVersion', $SrcVersion,
        '-SnapshotId', $SrcVersion,
        '-RuntimeVersion', $SrcVersion
    )
    if ($DurableHome) { $slotArgs += @('-DurableHome', $DurableHome) }
    & $probeHost @slotArgs
    exit $LASTEXITCODE
}

if ($Action -eq 'slot-cutover') {
    if ([string]::IsNullOrWhiteSpace($Context)) {
        Write-Fail 'slot-cutover requires -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization'
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedMarketplaceId)) {
        Write-Fail 'slot-cutover requires -ExpectedMarketplaceId'
        exit 2
    }
    if (
        [string]::IsNullOrWhiteSpace($ExpectedNamespaceGeneration) -or
        [string]::IsNullOrWhiteSpace($ExpectedInstallGeneration)
    ) {
        Write-Fail 'slot-cutover requires expected namespace and install generations'
        exit 2
    }
    if (-not $ExpectCurrentAbsent -and [string]::IsNullOrWhiteSpace($ExpectedCurrentVersion)) {
        Write-Fail 'slot-cutover requires -ExpectedCurrentVersion or -ExpectCurrentAbsent'
        exit 2
    }
    if ($ExpectCurrentAbsent -and -not [string]::IsNullOrWhiteSpace($ExpectedCurrentVersion)) {
        Write-Fail 'slot-cutover accepts only one current-version expectation'
        exit 2
    }
    if (-not $SrcVersion) {
        Write-Fail 'Cannot determine plugin version from pyproject.toml'
        exit 1
    }
    $slotRunner = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path -LiteralPath $slotRunner -PathType Leaf)) {
        Write-Fail 'Installation-context runner is unavailable'
        exit 1
    }
    $slotArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $slotRunner,
        'slot-cutover',
        '-Context', $Context,
        '-ExpectedMarketplaceId', $ExpectedMarketplaceId,
        '-ExpectedPluginId', 'agent-machines',
        '-ExpectedPayloadRoot', $PluginDir,
        '-ExpectedPayloadVersion', $SrcVersion,
        '-SnapshotId', $SrcVersion,
        '-RuntimeVersion', $SrcVersion,
        '-ExpectedNamespaceGeneration', $ExpectedNamespaceGeneration,
        '-ExpectedInstallGeneration', $ExpectedInstallGeneration
    )
    if ($ExpectCurrentAbsent) {
        $slotArgs += '-ExpectCurrentAbsent'
    } else {
        $slotArgs += @('-ExpectedCurrentVersion', $ExpectedCurrentVersion)
    }
    if ($DurableHome) { $slotArgs += @('-DurableHome', $DurableHome) }
    $cutoverDurableHome = $DurableHome
    if (-not $cutoverDurableHome) {
        $cutoverDurableHome = $Context
        1..5 | ForEach-Object {
            $cutoverDurableHome = Split-Path -Parent $cutoverDurableHome
        }
    }
    $validatedJson = @(
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
            validate `
            -Context $Context `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -ExpectedPluginId agent-machines `
            -DurableHome $cutoverDurableHome
    )
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'slot-cutover could not validate manifest paths'
        exit 1
    }
    try {
        $validated = ($validatedJson -join "`n") | ConvertFrom-Json
    } catch {
        Write-Fail 'slot-cutover received malformed manifest paths'
        exit 1
    }
    if (-not $validated.pluginRoot -or -not $validated.versionsRoot) {
        Write-Fail 'slot-cutover could not resolve manifest paths'
        exit 1
    }
    try {
        $currentManifest = Get-CellDeployManifest `
            -ManifestPath (Join-Path ([string]$validated.pluginRoot) 'deploy-manifest.json') `
            -ContextPath $Context `
            -MarketplaceId $ExpectedMarketplaceId
        if ($ExpectCurrentAbsent) {
            if ($null -ne $currentManifest) {
                throw 'Cell deploy manifest exists while current runtime is expected absent'
            }
        } else {
            $manifestCurrentMarker = Join-Path (
                [string]$validated.pluginRoot
            ) 'current-version'
            if (
                $null -eq $currentManifest -or
                -not (Test-Path -LiteralPath $manifestCurrentMarker -PathType Leaf) -or
                ((Get-Item -LiteralPath $manifestCurrentMarker -Force).Attributes -band
                    [IO.FileAttributes]::ReparsePoint) -or
                ([IO.File]::ReadAllText($manifestCurrentMarker)).Trim() -cne
                    [string]$currentManifest.runtime.version
            ) {
                throw 'Cell deploy manifest does not match the current runtime selection'
            }
        }
    } catch {
        Write-Fail "Existing cell deploy manifest is invalid; refusing runtime cutover: $_"
        exit 1
    }
    $cutoverJson = @(& $probeHost @slotArgs)
    $cutoverStatus = $LASTEXITCODE
    if ($cutoverJson.Count -gt 0) {
        $cutoverJson | ForEach-Object { Write-Output $_ }
    }
    if ($cutoverStatus -ne 0) { exit $cutoverStatus }
    try {
        $cutover = ($cutoverJson -join "`n") | ConvertFrom-Json
    } catch {
        Write-Fail 'slot-cutover returned an invalid result'
        exit 1
    }
    if ([string]$cutover.status -ceq 'ready') {
        Write-CellDeployManifest `
            -PluginRoot ([string]$validated.pluginRoot) `
            -SourcePluginDir $PluginDir `
            -SourceVersion $SrcVersion `
            -RuntimeSlot (Join-Path ([string]$validated.versionsRoot) $SrcVersion) `
            -RuntimeVersion $SrcVersion `
            -ContextPath $Context `
            -MarketplaceId $ExpectedMarketplaceId `
            -PreserveSource
    }
    elseif ([string]$cutover.status -cne 'revalidation-required') {
        Write-Fail 'slot-cutover returned an invalid result'
        exit 1
    }
    exit 0
}

if ($Action -eq 'cell-provision') {
    if ([string]::IsNullOrWhiteSpace($Context)) {
        Write-Fail 'cell-provision requires -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization'
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedMarketplaceId)) {
        Write-Fail 'cell-provision requires -ExpectedMarketplaceId'
        exit 2
    }
    if (-not $OriginPayloadRoot) { $OriginPayloadRoot = $PluginDir }
    try {
        $OriginPayloadRoot = (Resolve-Path -LiteralPath $OriginPayloadRoot).Path
    } catch {
        Write-Fail 'cell-provision origin payload root is unavailable'
        exit 2
    }
    if (-not $DurableHome) {
        $DurableHome = $Context
        1..5 | ForEach-Object { $DurableHome = Split-Path -Parent $DurableHome }
    }
    $slotRunner = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    $statusJson = @(
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
            status `
            -Context $Context `
            -PayloadRoot $OriginPayloadRoot `
            -PluginId agent-machines `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -ExpectedPluginId agent-machines `
            -ExpectedPayloadRoot $OriginPayloadRoot `
            -DurableHome $DurableHome `
            -LegacyRoot (Join-Path $env:USERPROFILE '.agent-machines') # marketplace-isolation: allow legacy compatibility root
    )
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'cell-provision could not validate installation activation'
        exit 1
    }
    try { $status = ($statusJson -join "`n") | ConvertFrom-Json } catch {
        Write-Fail 'cell-provision received malformed installation status'
        exit 1
    }
    if (
        [string]$status.status -cne 'ready' -or
        [string]$status.reason -cne 'namespaced-active' -or
        [string]$status.actualMode -cne 'namespaced'
    ) {
        Write-Fail (
            'cell-provision requires an active validated namespaced installation ' +
            "(status=$($status.status) reason=$($status.reason))"
        )
        exit 3
    }
    $validatedJson = @(
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
            validate `
            -Context $Context `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -ExpectedPluginId agent-machines `
            -ExpectedPayloadRoot $OriginPayloadRoot `
            -DurableHome $DurableHome
    )
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'cell-provision context receipt validation failed'
        exit 1
    }
    try { $validated = ($validatedJson -join "`n") | ConvertFrom-Json } catch {
        Write-Fail 'cell-provision received malformed context validation'
        exit 1
    }
    $pluginRoot = [string]$validated.pluginRoot
    $snapshotsRoot = [string]$validated.snapshotsRoot
    $versionsRoot = [string]$validated.versionsRoot
    $cellNamespaceGeneration = [string]$validated.namespaceGeneration
    $cellInstallGeneration = [string]$validated.generation
    if (
        -not $pluginRoot -or
        -not $snapshotsRoot -or
        -not $versionsRoot -or
        -not $cellNamespaceGeneration -or
        -not $cellInstallGeneration
    ) {
        Write-Fail 'cell-provision context receipt is incomplete'
        exit 1
    }
    $currentMarker = Join-Path (Split-Path -Parent $versionsRoot) 'current-version'
    if (
        (Test-Path -LiteralPath $currentMarker -PathType Leaf) -and
        -not ((Get-Item -LiteralPath $currentMarker -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint) -and
        ([IO.File]::ReadAllText($currentMarker)).Trim() -ceq $SrcVersion
    ) {
        $currentCutoverJson = @(
            & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
                slot-cutover `
                -Context $Context `
                -ExpectedMarketplaceId $ExpectedMarketplaceId `
                -ExpectedPluginId agent-machines `
                -ExpectedPayloadRoot $OriginPayloadRoot `
                -ExpectedPayloadVersion $SrcVersion `
                -SnapshotId $SrcVersion `
                -RuntimeVersion $SrcVersion `
                -ExpectedNamespaceGeneration $cellNamespaceGeneration `
                -ExpectedInstallGeneration $cellInstallGeneration `
                -ExpectedCurrentVersion $SrcVersion `
                -DurableHome $DurableHome
        )
        $currentCutoverStatus = $LASTEXITCODE
        try {
            $currentCutover = ($currentCutoverJson -join "`n") | ConvertFrom-Json
        } catch {
            $currentCutover = $null
        }
        if (
            $currentCutoverStatus -ne 0 -or
            $null -eq $currentCutover -or
            [string]$currentCutover.status -cne 'ready'
        ) {
            Write-Fail "selected cell runtime $SrcVersion failed immutable cutover validation"
            exit 1
        }
        Write-CellDeployManifest `
            -PluginRoot $pluginRoot `
            -SourcePluginDir $OriginPayloadRoot `
            -SourceVersion $SrcVersion `
            -RuntimeSlot (Join-Path $versionsRoot $SrcVersion) `
            -RuntimeVersion $SrcVersion `
            -ContextPath $Context `
            -MarketplaceId $ExpectedMarketplaceId
        Write-Ok "Runtime version $SrcVersion is already selected in installation cell"
        exit 0
    }
    $snapshotRoot = Join-Path $snapshotsRoot $SrcVersion
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($PluginDir, $snapshotRoot)) {
        try {
            Ensure-CellSnapshot -SnapshotRoot $snapshotRoot
        } catch {
            Write-Fail "cell snapshot publication failed: $_"
            exit 1
        }
        $snapshotInstaller = Join-Path $snapshotRoot 'scripts\init.ps1'
        if (-not (Test-Path -LiteralPath $snapshotInstaller -PathType Leaf)) {
            Write-Fail 'cell snapshot installer is unavailable'
            exit 1
        }
        $env:COPILOT_PLUGIN_STAGED_FROM = $OriginPayloadRoot
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $snapshotInstaller `
            -Action cell-provision `
            -Context $Context `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -DurableHome $DurableHome `
            -OriginPayloadRoot $OriginPayloadRoot
        exit $LASTEXITCODE
    }
    $CellMode = $true
    $InstallDir = $pluginRoot
    $VenvDir = Join-Path $versionsRoot $SrcVersion
    if ($env:OS -eq 'Windows_NT') {
        $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
    } else {
        $VenvPython = Join-Path $VenvDir 'bin/python'
    }
    $LinkDir = $VenvDir
    $LinkPython = $VenvPython
    $env:COPILOT_PLUGIN_STAGED_FROM = $OriginPayloadRoot
    & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
        slot-provision `
        -Context $Context `
        -ExpectedMarketplaceId $ExpectedMarketplaceId `
        -ExpectedPluginId agent-machines `
        -ExpectedPayloadRoot $OriginPayloadRoot `
        -ExpectedPayloadVersion $SrcVersion `
        -SnapshotId $SrcVersion `
        -RuntimeVersion $SrcVersion `
        -DurableHome $DurableHome | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'cell runtime-slot ownership provisioning failed'
        exit 1
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 strip-trampolines -- keep byte-identical across plugins ===
function Remove-ConsoleTrampolines {
    <# Strip the uv-regenerated Scripts\<name>.exe console-script trampolines from
       the venv after install. They are unsigned, zero-reputation PEs that Smart
       App Control blocks (CodeIntegrity 3077); nothing launches them (binstubs,
       services, and probes all use "python.exe -m <pkg>"), so remove every
       agent-*.exe. Best-effort -- rename a locked copy aside, then sweep stale
       stashes. Windows-only: POSIX console scripts are the sanctioned launch
       path and must be preserved. #>
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe') -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item $_.FullName -Force -ErrorAction Stop
        } catch {
            try { Rename-Item $_.FullName "$($_.FullName).old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {}
        }
    }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
# === end install-contract:v3 strip-trampolines ===

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
# === install-contract:v4 marker/toss helpers (#935) ===
function Get-BootstrapPython {
    <# A python to run the stdlib-only versioned_runtime.py helper (#935).
       Prefers the freshly-built slot venv python ($VenvDir, present at
       mark-complete before the link is swapped), then the active link's
       python, then a real base python via the `py` launcher -- avoiding the
       Windows Store 'python' alias stub. Returns $null if none. #>
    foreach ($d in @($VenvDir, $LinkDir)) {
        if ($d) { $p = Join-Path $d 'Scripts\python.exe'; if (Test-Path $p) { return $p } }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = (& py -3 -c 'import sys; print(sys.executable)' 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
    }
    foreach ($cand in 'python3', 'python') {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    return $null
}

function Get-PayloadHash(
    [long]$MaxEntries = 100000,
    [long]$MaxPathBytes = 4096,
    [long]$MaxContentBytes = 4294967296
) {
    <# Match installation-context snapshot content hashing over the whole payload.
       All non-root entries, including root snapshot-provenance.json, count
       toward the limits; only its digest record is excluded. #>
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $root = Get-Item -LiteralPath $PluginDir -Force -ErrorAction Stop
    if (-not $root.PSIsContainer -or
        (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Payload root must be an ordinary directory: $PluginDir"
    }
    $payloadIsWindows = $env:OS -eq 'Windows_NT'
    $statCommand = $null
    if (-not $payloadIsWindows) {
        $statCommand = Get-Command stat -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
    }
    if ($payloadIsWindows -and -not ('CePayloadDirectoryState' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class CePayloadDirectoryState {
    private const uint SHARE_READ = 1, SHARE_WRITE = 2, SHARE_DELETE = 4;
    private const uint OPEN_EXISTING = 3;
    private const uint OPEN_REPARSE = 0x00200000;
    private const uint BACKUP_SEMANTICS = 0x02000000;
    private const uint DIRECTORY = 0x10, REPARSE = 0x400;
    [StructLayout(LayoutKind.Sequential)]
    private struct Info {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Creation;
        public System.Runtime.InteropServices.ComTypes.FILETIME Access;
        public System.Runtime.InteropServices.ComTypes.FILETIME Write;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFile(
        string path, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out Info info);
    public static string Inspect(string path) {
        using (SafeFileHandle handle = CreateFile(
            path, 0, SHARE_READ | SHARE_WRITE | SHARE_DELETE, IntPtr.Zero,
            OPEN_EXISTING, OPEN_REPARSE | BACKUP_SEMANTICS, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            Info info;
            if (!GetFileInformationByHandle(handle, out info)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if ((info.Attributes & REPARSE) != 0 || (info.Attributes & DIRECTORY) == 0) {
                throw new IOException("path is not an ordinary directory");
            }
            return string.Format(
                "{0:x8}:{1:x8}{2:x8}|{3:x8}{4:x8}|{5:x8}{6:x8}|{7:x8}",
                info.Volume, info.IndexHigh, info.IndexLow,
                info.Write.dwHighDateTime, info.Write.dwLowDateTime,
                info.Creation.dwHighDateTime, info.Creation.dwLowDateTime,
                info.Attributes);
        }
    }
}
'@
    }
    function Get-PayloadKind($Entry, [string]$Relative) {
        if (($Entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Payload content may not contain symbolic links or reparse points: $Relative"
        }
        if ($payloadIsWindows) {
            return $(if ($Entry.PSIsContainer) { 'directory' } else { 'file' })
        }
        if ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                [Runtime.InteropServices.OSPlatform]::Linux
            )) {
            $kind = ("" + (& $statCommand.Source '--format=%F' '--' $Entry.FullName)).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Cannot inspect payload content: $Relative" }
            if ($kind -ceq 'directory') { return 'directory' }
            if ($kind -ceq 'regular file' -or $kind -ceq 'regular empty file') {
                return 'file'
            }
        } elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                [Runtime.InteropServices.OSPlatform]::OSX
            )) {
            $kind = ("" + (& $statCommand.Source '-f' '%HT' $Entry.FullName)).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Cannot inspect payload content: $Relative" }
            if ($kind -ceq 'Directory') { return 'directory' }
            if ($kind -ceq 'Regular File') { return 'file' }
        } else {
            throw 'Payload content classification is unavailable on this platform'
        }
        throw "Payload content entries must be ordinary files or directories: $Relative"
    }
    function Get-PayloadDirectoryToken([string]$Path, [string]$Relative) {
        $entry = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ((Get-PayloadKind $entry $Relative) -cne 'directory') {
            throw "Payload content changed during hashing: $Relative"
        }
        if ($payloadIsWindows) {
            return [CePayloadDirectoryState]::Inspect($Path)
        }
        if ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                [Runtime.InteropServices.OSPlatform]::Linux
            )) {
            $token = & $statCommand.Source '--format=%d|%i|%s|%y|%z' '--' $Path
        } else {
            $token = & $statCommand.Source '-f' '%d|%i|%z|%m|%c' $Path
        }
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect payload directory: $Relative" }
        return "" + $token
    }
    function Get-PayloadFileToken($Entry) {
        return (
            [string]$Entry.Length + '|' +
            [string]$Entry.LastWriteTimeUtc.Ticks + '|' +
            [string]$Entry.CreationTimeUtc.Ticks + '|' +
            [string][int64]$Entry.Attributes
        )
    }
    function Get-PayloadTreeState {
        $files = [Collections.Generic.SortedDictionary[string, object]]::new(
            [StringComparer]::Ordinal
        )
        $entries = [Collections.Generic.SortedDictionary[string, string]]::new(
            [StringComparer]::Ordinal
        )
        $directoryStates = [Collections.Generic.SortedDictionary[string, string]]::new(
            [StringComparer]::Ordinal
        )
        $directories = [Collections.Stack]::new()
        $directories.Push([pscustomobject]@{ path = $PluginDir; relative = '' })
        [long]$entryCount = 0
        [long]$totalBytes = 0
        while ($directories.Count -gt 0) {
            $directory = $directories.Pop()
            $directoryKey = if ($directory.relative) {
                ([BitConverter]::ToString(
                    $strictUtf8.GetBytes($directory.relative)
                )).Replace('-', '')
            } else { '' }
            $directoryStates.Add(
                $directoryKey,
                (Get-PayloadDirectoryToken $directory.path $directory.relative)
            )
            $enumerator = [IO.Directory]::EnumerateFileSystemEntries(
                $directory.path
            ).GetEnumerator()
            try {
                while ($enumerator.MoveNext()) {
                    $entryPath = [string]$enumerator.Current
                    $entryName = [IO.Path]::GetFileName($entryPath)
                    $relative = if ($directory.relative) {
                        $directory.relative + '/' + $entryName
                    } else { $entryName }
                    $pathBytes = $strictUtf8.GetBytes($relative)
                    if ($pathBytes.Length -gt $MaxPathBytes) {
                        throw "Payload content relative path exceeds the $MaxPathBytes-byte UTF-8 limit: $relative"
                    }
                    $entryCount++
                    if ($entryCount -gt $MaxEntries) {
                        throw "Payload content exceeds the $MaxEntries-entry limit"
                    }
                    $entry = Get-Item -LiteralPath $entryPath `
                        -Force -ErrorAction Stop
                    $kind = Get-PayloadKind $entry $relative
                    $sortKey = ([BitConverter]::ToString($pathBytes)).Replace('-', '')
                    $entries.Add($sortKey, $kind)
                    if ($kind -ceq 'directory') {
                        $directories.Push([pscustomobject]@{
                            path = $entry.FullName
                            relative = $relative
                        })
                        continue
                    }
                    [long]$length = $entry.Length
                    if ($length -gt ($MaxContentBytes - $totalBytes)) {
                        throw "Payload content exceeds the $MaxContentBytes-byte regular-file limit"
                    }
                    $totalBytes += $length
                    if ($directory.relative -or
                        $entry.Name -cne 'snapshot-provenance.json') {
                        $files.Add($sortKey, [pscustomobject]@{
                            path = $entry.FullName
                            relativeBytes = $pathBytes
                            token = Get-PayloadFileToken $entry
                        })
                    }
                }
            }
            finally {
                if ($enumerator -is [IDisposable]) {
                    $enumerator.Dispose()
                }
            }
        }
        return [pscustomobject]@{
            files = $files
            entries = $entries
            directories = $directoryStates
            entryCount = $entryCount
            totalBytes = $totalBytes
        }
    }
    function Assert-PayloadTreeState($Before, $After) {
        if ($Before.entryCount -ne $After.entryCount -or
            $Before.totalBytes -ne $After.totalBytes -or
            $Before.entries.Count -ne $After.entries.Count -or
            $Before.directories.Count -ne $After.directories.Count) {
            throw 'Payload content tree changed during hashing'
        }
        foreach ($pair in $Before.entries.GetEnumerator()) {
            if (-not $After.entries.ContainsKey($pair.Key) -or
                $After.entries[$pair.Key] -cne $pair.Value) {
                throw 'Payload content tree changed during hashing'
            }
        }
        foreach ($pair in $Before.directories.GetEnumerator()) {
            if (-not $After.directories.ContainsKey($pair.Key) -or
                $After.directories[$pair.Key] -cne $pair.Value) {
                throw 'Payload content tree changed during hashing'
            }
        }
    }
    $before = Get-PayloadTreeState
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $before.files.Values) {
            $entry = Get-Item -LiteralPath $file.path -Force -ErrorAction Stop
            if ($entry.PSIsContainer -or
                (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
                (Get-PayloadFileToken $entry) -cne $file.token) {
                throw "Payload content changed during hashing: $($entry.FullName)"
            }
            $stream = [IO.File]::Open(
                $file.path,
                [IO.FileMode]::Open,
                [IO.FileAccess]::Read,
                ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
            )
            $fileSha = [Security.Cryptography.SHA256]::Create()
            try {
                $fileDigest = ([BitConverter]::ToString(
                    $fileSha.ComputeHash($stream)
                )).Replace('-', '').ToLowerInvariant()
            }
            finally {
                $fileSha.Dispose()
                $stream.Dispose()
            }
            $entry = Get-Item -LiteralPath $file.path -Force -ErrorAction Stop
            if ($entry.PSIsContainer -or
                (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
                (Get-PayloadFileToken $entry) -cne $file.token) {
                throw "Payload content changed during hashing: $($entry.FullName)"
            }
            $prefix = [byte[]]@(0x46, 0x00)
            $separator = [byte[]]@(0x00)
            $digestBytes = [Text.Encoding]::ASCII.GetBytes($fileDigest)
            $newline = [byte[]]@(0x0A)
            [void]$sha.TransformBlock($prefix, 0, $prefix.Length, $null, 0)
            [void]$sha.TransformBlock(
                $file.relativeBytes,
                0,
                $file.relativeBytes.Length,
                $null,
                0
            )
            [void]$sha.TransformBlock($separator, 0, 1, $null, 0)
            [void]$sha.TransformBlock(
                $digestBytes,
                0,
                $digestBytes.Length,
                $null,
                0
            )
            [void]$sha.TransformBlock($newline, 0, 1, $null, 0)
        }
        Assert-PayloadTreeState $before (Get-PayloadTreeState)
        [void]$sha.TransformFinalBlock([byte[]]@(), 0, 0)
        return ([BitConverter]::ToString($sha.Hash)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Invoke-VersionedSlotClean {
    <# Toss an INCOMPLETE prior slot before building so we never `uv venv
       --allow-existing` over a corpse (#935); the current/active slot is never
       tossed (link-name derived from $LinkDir so the guard works per plugin).
       No-op in legacy mode. #>
    if (-not $VersionedRuntime -or $CellMode) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return }
    & $py $vr --root $InstallDir --link-name (Split-Path -Leaf $LinkDir) slot $SrcVersion --clean-incomplete 2>&1 |
        ForEach-Object { Write-Host "  ...    $_" }
}

function Invoke-VersionedMarkComplete {
    <# Write the slot's completion marker AFTER its isolated health gate passed,
       so "marker present" == "healthy, complete build". A crashed / watchdog-
       killed install never reaches here, leaving its slot markerless and thus
       tossable + retryable (#935). No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) {
        throw 'Cannot locate Python to write the runtime completion marker'
    }
    $ph = Get-PayloadHash
    $mcArgs = @($vr, '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir), 'mark-complete', $SrcVersion, '--payload-hash', $ph)
    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host "  ...    $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime completion marker writer failed with exit code $LASTEXITCODE"
    }
}
# === end install-contract:v4 marker/toss helpers ===

function Get-SourceKind {
    param([string]$PluginPath)
    # #935: when the installer self-staged out of the marketplace payload, its
    # live path is a throwaway stage dir, so infer the kind from the ORIGINAL
    # payload path the self-stage prologue recorded (else the current path).
    $__srcPath = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { $PluginPath }
    if (($__srcPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        if (git -C $Path status --porcelain 2>$null) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

function Deploy-SelfProvisioningBinstub {
    # agent-machines ships a SINGLE self-provisioning .cmd (no .ps1): it is
    # spawned by Copilot as a bare ``command: agent-machines`` stdio MCP server,
    # where a .cmd forwards stdin verbatim (a .ps1 shim does not) and wins
    # PATHEXT/PowerShell resolution. Fast-path the built slot python; if no slot
    # is built yet (a ``stamp`` deferred the venv), provision on first use from
    # the slot-local snapshot (``init.ps1 provision``) then dispatch. Opt out with
    # AGENT_MACHINES_NO_SELFPROVISION=1. POSIX gets its sh shim. (#1393)
    # Co-deploy the canonical resolvers so every launcher resolves identically
    # (uniform-runtime-resolution, #765).
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path $rSrc) { Copy-Item $rSrc (Join-Path $binDir $r) -Force }
    }
    if ($env:OS -ne 'Windows_NT') {
        $stubPath = Join-Path $LocalBin 'agent-machines'
        $stubContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
_root="`$HOME/.agent-machines"
AGENT_RT_PY=""
if [ -f "`$_root/bin/resolve-runtime.sh" ]; then AGENT_RT_ROOT="`$_root"; . "`$_root/bin/resolve-runtime.sh"; fi
[ -n "`$AGENT_RT_PY" ] && exec "`$AGENT_RT_PY" -m agent_machines "`$@"
_i="`$(cat "`$_root/payload-dir" 2>/dev/null)/scripts/init.sh"
[ -f "`$_i" ] || _i="`$(ls "`$HOME"/.copilot/installed-plugins/*/agent-machines/scripts/init.sh 2>/dev/null | head -n1)"
if [ -n "`$_i" ] && [ -f "`$_i" ]; then echo "[agent-machines] runtime not provisioned; run: bash \"`$_i\" provision" >&2; else echo "[agent-machines] runtime not provisioned and the installer was not found; re-enable the plugin, then retry." >&2; fi
exit 1
"@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
        Write-Ok "Binstub: $stubPath"
        return
    }
    # Remove any stale .ps1 so it can't shadow the stdio-safe .cmd.
    $ps1Path = Join-Path $LocalBin 'agent-machines.ps1'
    if (Test-Path $ps1Path) { Remove-Item $ps1Path -Force -ErrorAction SilentlyContinue }
    $cmdPath = Join-Path $LocalBin 'agent-machines.cmd'
    $cmdContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_ROOT=%USERPROFILE%\.agent-machines"
call :_resolve
if not defined _PY goto _prov
"%_PY%" -m agent_machines %*
exit /b %ERRORLEVEL%
:_prov
if defined AGENT_MACHINES_NO_SELFPROVISION goto _nope
set "_SNAP="
if exist "%_ROOT%\payload-dir" set /p _SNAP=<"%_ROOT%\payload-dir"
set "_INST=%_SNAP%\scripts\init.ps1"
if not exist "%_INST%" goto _noinst
set "_ORIGIN="
if exist "%_ROOT%\payload-origin" set /p _ORIGIN=<"%_ROOT%\payload-origin"
if defined _ORIGIN set "COPILOT_PLUGIN_STAGED_FROM=%_ORIGIN%"
echo [agent-machines] runtime not provisioned -- provisioning on first use ^(~30-120s^). Do not kill.>&2
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_INST%" provision 1>&2) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_INST%" provision 1>&2)
set "COPILOT_PLUGIN_STAGED_FROM="
call :_resolve
if not defined _PY goto _failprov
"%_PY%" -m agent_machines %*
exit /b %ERRORLEVEL%
:_noinst
echo [agent-machines] cannot self-provision: snapshot installer not found.>&2
exit /b 127
:_nope
echo [agent-machines] runtime not provisioned ^(AGENT_MACHINES_NO_SELFPROVISION set^).>&2
exit /b 1
:_failprov
echo [agent-machines] provisioning did not yield a runtime.>&2
exit /b 1
:_resolve
set "_PY="
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
if defined _VER if exist "%_ROOT%\versions\%_VER%\Scripts\python.exe" set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if defined _PY goto :eof
if not exist "%_ROOT%\bin\resolve-runtime.ps1" goto :eof
set "_PSX=powershell"
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 set "_PSX=pwsh"
for /f "usebackq delims=" %%p in (`%_PSX% -NoProfile -ExecutionPolicy Bypass -Command "$env:AGENT_RT_ROOT='%_ROOT%'; . '%_ROOT%\bin\resolve-runtime.ps1'; if ($AgentRtPy) { $AgentRtPy }" 2^>nul`) do set "_PY=%%p"
goto :eof
'@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $cmdPath (self-provisioning)"
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE
    # into ~/.agent-machines/snapshots/<ver>/, record markers, and deploy the
    # self-provisioning binstub -- deferring the heavy venv build to first use.
    # No venv, no uv; never holds the marketplace payload open (copies from the
    # already self-staged $PluginDir).
    Write-Host ''
    Write-Host '=== agent-machines stamp (defer runtime to first use) ===' -ForegroundColor Cyan
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $stampHash = [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($InstallDir.ToLowerInvariant())
        )
    ).Replace('-', '').Substring(0, 24)
    $stampMutexName = if ($env:OS -eq 'Windows_NT') {
        "Local\CopilotExtensions.AgentMachines.Stamp.$stampHash"
    } else {
        "CopilotExtensions.AgentMachines.Stamp.$stampHash"
    }
    $stampMutex = New-Object Threading.Mutex($false, $stampMutexName)
    $stampLockHeld = $false
    try {
        try {
            $stampLockHeld = $stampMutex.WaitOne([TimeSpan]::FromSeconds(20))
        } catch [Threading.AbandonedMutexException] {
            $stampLockHeld = $true
        }
        if (-not $stampLockHeld) { throw 'Timed out waiting for the agent-machines stamp lock.' }
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $payloadDirMarker = Join-Path $InstallDir 'payload-dir'
    $payloadOriginMarker = Join-Path $InstallDir 'payload-origin'
    Remove-Item $payloadDirMarker, $payloadOriginMarker -Force -ErrorAction SilentlyContinue
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    $snapTmp = "$snapDir.tmp-$PID"
    if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
    $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
    Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
    }
    if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
    [System.IO.File]::WriteAllText($payloadOriginMarker, $probePayload, $utf8NoBom)
    [System.IO.File]::WriteAllText($payloadDirMarker, $snapDir, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Write-Ok "Snapshot: $snapDir"
    Deploy-SelfProvisioningBinstub
    Write-Ok 'Stamped: agent-machines binstub on PATH; runtime provisions on first use.'
    } finally {
        if ($stampLockHeld) { [void]$stampMutex.ReleaseMutex() }
        $stampMutex.Dispose()
    }
}

if ($Action -eq 'stamp') { Invoke-Stamp; exit 0 }

# -- Preflight checks --------------------------------------------------

Write-Host ''
Write-Host '=== agent-machines init ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    Write-Host "  Are you running this from the correct plugin directory?"
    exit 1
}

$hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

# Find a Python interpreter (skip Windows Store aliases that aren't real)
$pythonCmd = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $testOut = & $found.Source --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') {
                $pythonCmd = $found.Source
            }
        } catch { }
        $ErrorActionPreference = $prevEAP
        if ($pythonCmd) { break }
    }
}
if (-not $pythonCmd) {
    Write-Fail 'Python not found on PATH (need 3.10+)'
    Write-Host '  Install Python from https://python.org or via winget:' -ForegroundColor DarkGray
    Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
    exit 1
}
Write-Ok "Python: $pythonCmd"

# Check for uv -- install via winget if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if ($hasWinget) {
        Write-Step 'uv not found -- installing via winget...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Ok 'uv installed' }
    }
}

# -- 1. Create directories ---------------------------------------------

foreach ($dir in @($InstallDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
if (-not $CellMode -and -not (Test-Path $LocalBin)) {
    New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
}
Write-Ok "Directories: $InstallDir"

# -- 1b. Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.agent-machines/bin/bootstrap-check.ps1 at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
if (-not $CellMode) {
    $BinHookDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $BinHookDir)) { New-Item -ItemType Directory -Path $BinHookDir -Force | Out-Null }
    foreach ($h in @('bootstrap-check.ps1', 'bootstrap-check.sh')) {
        $hSrc = Join-Path $PSScriptRoot $h
        if (Test-Path $hSrc) { Copy-Item $hSrc (Join-Path $BinHookDir $h) -Force }
    }
    Write-Ok "Session-start hook: $BinHookDir\bootstrap-check.ps1"
}

# -- 2. Create venv ----------------------------------------------------

if ($Force -or -not (Test-Path $VenvPython)) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Prefer a SAC-trusted signed base Python via `--copies` so the venv
    # python.exe is signed (Smart App Control blocks the unsigned uv-managed
    # python); then uv; then plain python -m venv.
    $signedBase = $null
    if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($v in '3.13', '3.12', '3.11') {
            $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $cand -and (Test-Path $cand)) {
                try { if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break } } catch {}
            }
        }
    }
    if ($signedBase -and (Test-Path $VenvPython)) {
        try { if ((Get-AuthenticodeSignature $VenvPython).Status -ne 'Valid') { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop } } catch {}
    }
    if ($signedBase -and -not (Test-Path $VenvPython)) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
    }
    if (-not (Test-Path $VenvPython)) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Write-Step 'Creating venv via uv...'
            Invoke-VersionedSlotClean
            & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Step 'uv venv failed -- falling back to python -m venv'
                & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
            }
        } else {
            Write-Step 'Creating venv via python -m venv...'
            & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        }
    }
    $ErrorActionPreference = $prevEAP
    if (-not (Test-Path $VenvPython)) {
        Write-Fail "Venv creation failed -- $VenvPython not found"
        exit 1
    }
    Write-Ok 'Venv created'
} else {
    Write-Skip 'Venv already exists'
}

# -- 3. Install the package into the venv (uv pip install) -------------

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
# Pre-strip any locked console-script trampoline so uv can overwrite it (os err 5).
Remove-ConsoleTrampolines -VenvDir $VenvDir
if (Get-Command uv -ErrorAction SilentlyContinue) {
    if ($CellMode) {
        & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1 |
            ForEach-Object { Write-Host $_ }
    } else {
        & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1 | Out-Null
    }
} else {
    if ($CellMode) {
        & $VenvPython -m pip install --quiet "$PluginDir" 2>&1 |
            ForEach-Object { Write-Host $_ }
    } else {
        & $VenvPython -m pip install --quiet "$PluginDir" 2>&1 | Out-Null
    }
}
$pkgResult = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pkgResult -ne 0) {
    Write-Fail 'Failed to install agent-machines package into venv'
    exit 1
}

# Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused).
Remove-ConsoleTrampolines -VenvDir $VenvDir
Write-Ok 'Package installed: agent-machines'

# === install-contract:v3 versioned-venv activate -- keep byte-identical across plugins ===
if ($VersionedRuntime) {
    # Point the stable `.venv` link at this version's freshly-built slot, moving a
    # legacy real `.venv` aside on the first migration. Run via the slot's own
    # python (stdlib-only helper); a CLI plugin has no daemon holding the link, so
    # the swap is immediately safe.
    $VrScript = Join-Path $PSScriptRoot 'versioned_runtime.py'
    # Health-gate: never swap the stable `.venv` link onto a slot whose package
    # does not import -- a broken build must not become the live runtime.
    & $VenvPython -c 'import agent_machines' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
        exit 1
    }
    Invoke-VersionedMarkComplete
    if ($CellMode) {
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $slotRunner `
            slot-complete `
            -Context $Context `
            -ExpectedMarketplaceId $ExpectedMarketplaceId `
            -ExpectedPluginId agent-machines `
            -ExpectedPayloadRoot $OriginPayloadRoot `
            -ExpectedPayloadVersion $SrcVersion `
            -SnapshotId $SrcVersion `
            -RuntimeVersion $SrcVersion `
            -DurableHome $DurableHome | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail 'Cell runtime completion publication failed'
            exit 1
        }
        $currentMarker = Join-Path $InstallDir 'current-version'
        $cutoverArgs = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $slotRunner,
            'slot-cutover',
            '-Context', $Context,
            '-ExpectedMarketplaceId', $ExpectedMarketplaceId,
            '-ExpectedPluginId', 'agent-machines',
            '-ExpectedPayloadRoot', $OriginPayloadRoot,
            '-ExpectedPayloadVersion', $SrcVersion,
            '-SnapshotId', $SrcVersion,
            '-RuntimeVersion', $SrcVersion,
            '-ExpectedNamespaceGeneration', $cellNamespaceGeneration,
            '-ExpectedInstallGeneration', $cellInstallGeneration,
            '-DurableHome', $DurableHome
        )
        if (
            (Test-Path -LiteralPath $currentMarker -PathType Leaf) -and
            -not ((Get-Item -LiteralPath $currentMarker -Force).Attributes -band
                [IO.FileAttributes]::ReparsePoint)
        ) {
            $cutoverArgs += @(
                '-ExpectedCurrentVersion',
                ([IO.File]::ReadAllText($currentMarker)).Trim()
            )
        } else {
            $cutoverArgs += '-ExpectCurrentAbsent'
        }
        & $probeHost @cutoverArgs | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail 'Cell runtime-slot cutover failed'
            exit 1
        }
        Write-Ok "Runtime version $SrcVersion selected in installation cell"
    } else {
        # Capture the currently-active version so gc can retain it as previous-good.
        $PrevVersion = ("" + (& $VenvPython $VrScript --root $InstallDir --link-name '.venv' current 2>$null)).Trim()
        & $VenvPython $VrScript --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
            exit 1
        }
        Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
        # GC superseded version slots, keeping the current + previous-good and any
        # slot with a live process (--protect-pids), so old versions do not pile up.
        if ($PrevVersion) {
            & $VenvPython $VrScript --root $InstallDir --link-name '.venv' gc --protect-pids --keep $PrevVersion 2>&1 |
                ForEach-Object { Write-Host "  ...    gc: $_" -ForegroundColor DarkGray }
        } else {
            & $VenvPython $VrScript --root $InstallDir --link-name '.venv' gc --protect-pids 2>&1 |
                ForEach-Object { Write-Host "  ...    gc: $_" -ForegroundColor DarkGray }
        }
    }
}
# === end install-contract:v3 versioned-venv activate ===

# -- 4. Deploy binstub -------------------------------------------------

if (-not $CellMode) {
    Deploy-SelfProvisioningBinstub
}

# -- 5. Write deploy manifest ------------------------------------------

# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
$manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$sourcePluginDir = if ($CellMode) { $OriginPayloadRoot } else { $PluginDir }
$kind = Get-SourceKind -PluginPath $sourcePluginDir
$ver = '0.0.0'
$pyproj = Join-Path $PluginDir 'pyproject.toml'
if (Test-Path $pyproj) {
    $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
    if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*', '$1') }
}
$commit = $null; $branch = $null; $dirty = $false
if ($kind -eq 'local') {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $sourcePluginDir)
    $git = Get-GitInfo -Path $repoRoot
    $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
}
if ($CellMode) {
    Write-CellDeployManifest `
        -PluginRoot $InstallDir `
        -SourcePluginDir $sourcePluginDir `
        -SourceVersion $ver `
        -RuntimeSlot $VenvDir `
        -RuntimeVersion $SrcVersion `
        -ContextPath $Context `
        -MarketplaceId $ExpectedMarketplaceId
    Write-Ok "Deploy manifest written (source: $kind)"
} else {
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-machines'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($sourcePluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-machines'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($LinkDir -replace '\\', '/')
        runtime        = 'python'
    }
    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
}

# -- 6. Verify ----------------------------------------------------------

Write-Host ''
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importOk = $false
for ($i = 0; $i -lt 3; $i++) {
    & $VenvPython -c 'import agent_machines' 2>$null
    if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
    Start-Sleep -Seconds 1
}
$ErrorActionPreference = $prevEAP
if ($importOk) {
    Write-Ok 'Verification: module imports successfully'
} else {
    Write-Fail 'Verification: module import failed'
    exit 1
}

# Ensure ~/.local/bin is on PATH
$pathDirs = $env:PATH -split ';'
if ($pathDirs -contains $LocalBin) {
    Write-Ok "PATH: $LocalBin is on PATH"
} else {
    $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

Write-Host ''
Write-Host '=== agent-machines init complete ===' -ForegroundColor Cyan
if ($CellMode) {
    Write-Host '  Runtime is ready through the owning payload command.'
} else {
    Write-Host '  Try: agent-machines version'
} -ForegroundColor DarkGray
exit 0
